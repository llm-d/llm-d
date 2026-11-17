# XPU MP validation — 2026-11-17

Local Docker validation used the reserved Intel Arc Pro B60, xpu-smi device
ID 3 at PCI `0000:58:00.0`. Containers received only `/dev/dri/card4` and
`/dev/dri/renderD131`; PyTorch saw one XPU. Kubernetes runtime validation
remains pending because the node evicted a namespace-scoped probe for
DiskPressure.

## Image and configuration

| Component | Validated value |
| --- | --- |
| Local image | `llm-d-xpu:lmcache-v0.5.7.post1-validation` |
| Image ID | `sha256:5d193863cf8d331e66965b8bba5b19e79aec5140765af4b86c93653dc8cc0bf5` |
| LMCache | `0.5.7.post1`, upstream commit `1181bea5c65a6f477e3680acd14ffac988b13dc3` |
| vLLM / PyTorch | `0.30.0+xpu` / `2.13.0+xpu` |
| Model | `Qwen/Qwen3-8B`, revision `b968826d9c46dd6066d109eabc6255188de91218` |
| Model settings | BF16, TP=1, block size 64, maximum context 20,480, maximum sequences 128 |
| Transfer | `engine_driven`, inline ZMQ payloads, no cross-container shared memory |
| L1 | One CPU server, 20 GiB preallocated pool, chunk size 256 |
| Filesystem L2 | Overlay's `fs_native` adapter, direct I/O enabled, local block filesystem |
| Health / metrics | Cache port 8080, bound to `0.0.0.0`; model port 8000 |

The released `ghcr.io/llm-d/llm-d-xpu:v1.0.0` image failed real inference:
its LMCache 0.5.0 connector called `torch.xpu.Event(interprocess=True)`,
which raises `TypeError`. The validated image uses the untouched upstream
LMCache release with rebuilt SYCL and native filesystem extensions.

The full Dockerfile build could not reach Intel's apt endpoint. The local
rebuild reused the existing image's oneAPI 2026.0 compiler, removed its
LMCache source, and ran the updated Dockerfile's LMCache build commands.
The image has not been published; deployments require the image override
described in the [MP guide](README.md#deployment).

The image's default 92% GPU memory budget failed one restart because free
memory was slightly below the requested allocation. The overlay now sets
`--gpu-memory-utilization 0.90`. The initial CPU prime used 92%; CPU retrieval
and all filesystem inference checks used 90%.

## Correctness checks

The deterministic request contained 2,611 prompt tokens, with the access code
`CACHE_OK_42` near the start of a long reference document. Every successful
response returned that exact code with six completion tokens. The request
used temperature 0, seed 42, and disabled Qwen thinking.

The request's sorted JSON SHA-256 was
`30c976ba292cf98c1cdaa5a82d12c6fec8771db981ea764324dd3caa0fc9c09a`.
Its ten complete 256-token chunks cover 2,560 tokens and consume 360 MiB
of L1 data.

Counter deltas for the inference checks:

| Check | L1 writes | L1 reads | L2 stored chunks | L2 loaded chunks | External hit tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU prime | 10 | 0 | 0 | 0 | 0 |
| CPU, new vLLM process | 0 | 10 | 0 | 0 | 2,560 |
| Filesystem prime | 10 | 10 | 10 | 0 | 0 |
| Filesystem, new vLLM process | 0 | 10 | 0 | 0 | 2,560 |
| Filesystem, new cache server and vLLM | 10 | 10 | 0 | 10 | 2,560 |

For model-only restarts, the cache server stayed running. Each new vLLM
process reported zero local HBM prefix hits and returned the identical
completion, proving retrieval from the external cache.

For the cold filesystem check, both processes restarted and the disk
directory stayed intact. The new cache server began with zero L1 data.
The request completed one L2 load containing ten chunks, increased
`lmcache_mp_l2_prefetch_load_completed_chunks_total` by ten, and returned the
same completion with zero local HBM prefix hits. Its L1 writes were staging
the disk-loaded chunks. No new L2 stores occurred.

A separate XPU transfer fixture used two BF16 layers in the fused
`NL_X_NB_BS_NH_CS` layout, each shaped `(24, 64, 2, 256)`. It stored 512 tokens
from non-contiguous source blocks, zeroed the XPU tensors, and retrieved into
different destination blocks. Both CPU and filesystem variants returned
bitwise-identical data, and untouched blocks remained zero. The filesystem
fixture also passed in load-only mode after replacing the cache server:
startup L1 usage was zero, both chunks loaded from disk, and the retrieved
XPU data remained bitwise identical.

## Kubernetes and remaining validation

All Kubernetes operations used context `shared-infra-context` and namespace
`shared-infra`. Strict server-side dry-runs accepted both site overlays,
including replacement of the cache, model, and init container images.
The node-scoped busybox probe scheduled onto the Intel host and was then
evicted for DiskPressure. The probe was deleted.

The local results do not validate Kubernetes DRA allocation, the node-local
Service, llm-d router integration, or a shared RWX filesystem. After the node
can admit pods and the deployment image is available, run the
[Kubernetes validation sequence](README.md#runtime-validation-pending).
Concurrency, peak memory, and multi-node behavior also remain unmeasured.

Test containers, their private network, model RAM disk, compile caches, and
filesystem scratch were removed. The local image and validation logs were
retained.

Guide consistency checks, Dockerfile environment checks, manifest rendering,
and all 35 nightly verifier unit tests passed. The new LMCache connector,
SYCL extension, and native filesystem extension imported successfully.
`pip check` still reports two inherited base-image issues: missing
`nixl-cu13` and unsupported-platform `decord`. These were not changed by this
migration.
