# Reusing JIT Compile Caches Across Pods

Assumes the [main guide](./README.md) is deployed and its environment variables are set.

Once weight transfer is sub-second, vLLM's `torch.compile` becomes the bottleneck for receiver pod-Ready time. The bootstrap pod compiles the model graph and fills its JIT caches. Receivers can reuse those caches instead of recompiling per pod. There are two ways to move them.

## Option A: P2P cache artifact transfer (ModelExpress 0.5.0+)

The 0.5.0 artifact transfer protocol extends the NIXL path beyond weights to file-backed cache artifacts. Set `MX_ARTIFACT_TRANSFER=1` on the decode pods (it applies to both source and target roles). Before model initialization, receivers install compatible torch.compile (TorchInductor), Triton, DeepGEMM, TileLang, CuTe DSL, and FlashInfer caches, including persistent autotune files, from a ready source. No shared storage is needed.

Measured with the guide's default configuration (see [benchmark-results](./benchmark-results/h200-ib-gpt-oss-120b.md)): the receiver installs the seed's 148 MiB torch.compile bundle in 0.9 s, `torch.compile` drops from 20.1 s to 3.4 s (direct AOT cache load), and receiver pod-Ready time drops from 181 s to 156 s.

```yaml
# add to the modelserver container env in base/patch-vllm.yaml
- name: MX_ARTIFACT_TRANSFER
  value: "1"
```

Notes on this path:

* It needs the P2P metadata path (`MX_P2P_METADATA=1`, the default) and a central-coordinator backend. This guide's `kubernetes` backend qualifies; the decentralized `k8s-service` backend does not publish artifact discovery metadata yet.
* The source seals and publishes cache bundles only after the engine reports healthy (`MX_ARTIFACT_READY_URL`, default `http://127.0.0.1:8000/health` for vLLM). Receivers that start after the seed is Ready get the caches; a cold N-replica apply where all pods race up may not.
* Bundles stage as tars under `MX_ARTIFACT_BUNDLE_ROOT` (default `$TMPDIR/modelexpress-artifacts`), then install into the runtime cache directories. The guide's `/root/.cache` and `/root/.triton` emptyDir mounts are writable, so no manifest change is needed.
* Compatibility is digest-gated (compile config, accelerator family). An incompatible artifact is skipped and the receiver recompiles; it does not fail the load.
* Cudagraph capture is not a file-backed artifact. It still costs per pod on this path and on the PVC path below.
* `MX_ARTIFACT_TRANSFER_CHUNK_SIZE` (default 64 MiB) trades RPC overhead against registered DRAM buffer memory on both ends.

## Option B: Shared RWX PVC

vLLM stores the AOT compile artifacts under `/root/.cache/vllm` (the container's `/root/.cache` mount; the image runs as root, so `VLLM_CACHE_ROOT` resolves under `/root`). If you back that path with a `ReadWriteMany` PVC, receivers reuse the cached graphs from shared storage instead.

Layer the `shared-compile-cache` kustomize component on top of your provider overlay:

```yaml
# guides/modelexpress-p2p/modelserver/gpu/vllm/coreweave/kustomization.yaml (or your overlay)
components:
  - ../components/shared-compile-cache
```

Or build an inline overlay that pulls in both:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - guides/modelexpress-p2p/modelserver/gpu/vllm/coreweave
components:
  - guides/modelexpress-p2p/modelserver/gpu/vllm/components/shared-compile-cache
```

The component:

* Adds a `vllm-compile-cache` PVC (50 GiB, RWX, uses cluster-default StorageClass; edit `compile-cache-pvc.yaml` if you need a specific class like `shared-vast` on CoreWeave or `efs-sc` on EKS).
* Swaps the per-pod `torch-compile-cache` emptyDir for the PVC.
* Exports `VLLM_CACHE_ROOT=/root/.cache/vllm` and `TORCHINDUCTOR_CACHE_DIR=/root/.cache/torch_inductor` so vLLM and Inductor land on the shared mount.

## Workload notes

**RL training rollouts.** Frameworks like [veRL](https://github.com/volcengine/verl),
[OpenRLHF](https://github.com/OpenRLHF/OpenRLHF),
[TRL's GRPO trainer](https://huggingface.co/docs/trl/main/en/grpo_trainer),
and [NeMo-RL](https://github.com/NVIDIA-NeMo/RL) run vLLM rollout workers with
`enforce_eager=True`. Every policy weight update invalidates cudagraphs, and
re-capturing them between training steps costs too much time. For this
audience, the cudagraph capture caveat (Options A and B
above) does not apply. Add `--enforce-eager` to the args
in `patch-vllm.yaml` (or omit `--load-format=mx`'s cudagraph-related
compilation entirely) to put receiver pod-Ready time in the 30-40 second range.

**Elastic / bin-packed inference on dense racks** (for example, NVL72 hosting multiple model deployments that share a GPU budget). An operator may want to reshape the GPU budget across model deployments in response to realtime load: tear down replicas of one model to start replicas of another. In this case the primary cold-start cost may be weight movement rather than cudagraph capture. This guide moves that path to P2P RDMA. The remaining tradeoff between scale-up latency and steady-state TPOT (cudagraphs vs `--enforce-eager`) is workload-specific and outside the scope of this guide.
