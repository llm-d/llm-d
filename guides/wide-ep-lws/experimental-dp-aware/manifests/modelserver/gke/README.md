# GKE Overlay — Known Issues and Caveats

## RDMA Resource Scheduling

GKE Warden (mutating webhook) injects resource requests for both `networking.gke.io.networks/rdma-X` and `networking.gke.io.networks/rdma-X.IP` from the `networking.gke.io/interfaces` annotation. Modern GKE H200 (A3 Ultra) nodes only advertise the `.IP` variant, causing pods to stay Pending.

**Workaround:** The overlay sets the legacy non-`.IP` resource limits to `0`, preventing the webhook from requesting the unavailable variant. See [PR #1738](https://github.com/llm-d/llm-d/pull/1738).

## Router: Multi-Port targetPorts

The router config must include `targetPorts` (8000-8007) so the EPP routes requests to all 8 DP ranks per pod. This is set in `router/wide-ep-lws.values.yaml` (standalone mode) or `manifests/inferencepool.values.yaml` (gateway mode). Without `targetPorts`, the EPP only routes to the default port (8000/rank 0), leaving 7 out of 8 ranks idle. This results in ~1/8th expected throughput.

**Symptom:** Only `APIServer_DP0` and `APIServer_DP8` show high CPU usage; other ranks show <1% CPU.

**Fix:** Ensure `targetPorts` is set in the router values file and the Helm chart is deployed with the updated values.

## Router: Standalone Mode Required

GKE native gateway classes (`gke-l7-rilb`, etc.) do not support ext-proc/InferencePool backends. Use the standalone router mode (`llm-d-router-standalone-dev`) instead of gateway mode. The standalone chart includes an Envoy sidecar that handles the ext-proc integration. This is what the GKE nightly workflow uses.

```bash
export REPO_ROOT=$(git rev-parse --show-toplevel)
helm install wide-ep-lws \
  oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/wide-ep-lws/router/wide-ep-lws.values.yaml \
  -n ${NAMESPACE} --version v0
```

Get the endpoint IP for benchmarking:
```bash
export IP=$(kubectl get service wide-ep-lws-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

## PodMonitor / Prometheus Operator CRDs

The GKE cluster may not have Prometheus Operator CRDs (`PodMonitor`, `ServiceMonitor`) installed. Deploying manifests that include `pod-monitors.yaml` will fail with `no matches for kind "PodMonitor"`. GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) as an alternative.

**Workaround:** Comment out `pod-monitors.yaml` from `base/kustomization.yaml` if the CRD is not installed, or install it:
```bash
kubectl apply -f https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/main/example/prometheus-operator-crd/monitoring.coreos.com_podmonitors.yaml
```

## Pod Startup Ordering

With 4 pods (2 decode, 2 prefill) requiring inter-node DP coordination, staggered startup can cause cascade failures. If one pod starts significantly before the others, it may timeout waiting for peers and exit cleanly (exit code 0), which triggers the DPSupervisor to shut down all children, causing the other pods to also exit.

**Workaround:** If pods are stuck in a restart loop, delete all model server pods at once so they restart simultaneously:
```bash
kubectl delete pods -l llm-d.ai/guide=wide-ep-lws
```

Set a priority class to prevent preemption during the 10+ minute startup:
```bash
kubectl patch leaderworkerset wide-ep-llm-d-decode --type=merge -p '{"spec":{"leaderWorkerTemplate":{"workerTemplate":{"spec":{"priorityClassName":"nightly-gpu-critical"}}}}}'
kubectl patch leaderworkerset wide-ep-llm-d-prefill --type=merge -p '{"spec":{"leaderWorkerTemplate":{"workerTemplate":{"spec":{"priorityClassName":"nightly-gpu-critical"}}}}}'
```

## NCCL Shared Memory

With 8 DP rank processes per pod sharing the `/dev/shm` volume (default 2Gi), NCCL may report "No available shared memory broadcast block" during initialization. This is typically a warning — NCCL falls back to a slower communication path. If pods hang during startup, increase `dshm` `sizeLimit` in the base manifests (e.g., to 16Gi).

## Image: llm-d-cuda with vLLM v0.22.1+

The `llm-d-cuda` base image must include vLLM v0.22.1+ for the DP Supervisor feature. The official `llm-d-cuda:v0.7.0` is pinned to vLLM v0.19.1. Use `llm-d-cuda-dev:pr-1769` or a newer build that includes the vLLM version bump.

When using the upstream `vllm/vllm-openai` image instead of `llm-d-cuda`, the following additional issues occur (resolved by using `llm-d-cuda`):

- **TRITON_LIBCUDA_PATH**: NVIDIA driver is at `/usr/local/nvidia/lib64/` instead of `/usr/lib64/`, causing Triton JIT compilation to fail.
- **NVSHMEM IBGDA (ibv_ah bug)**: Unpatched NVSHMEM causes `Unable to create ah.` errors, breaking `deepep_low_latency`. See [docs/infra-providers/gke/README.md](../../../../../docs/infra-providers/gke/README.md).
- **GPU OOM during DeepGEMM warmup**: May require `--gpu-memory-utilization 0.85` to leave headroom for the 14 GiB warmup allocation.
- **nixl_ep_cpp libcudart.so.12**: The pip-installed `nixl` package may have the wrong CUDA variant's `.so` active. Fixed by force-reinstalling `nixl-cu${CUDA_MAJOR}` in the image build.

## Summary of Overlay Patches

| Issue | Env Var / Resource | Base Value | GKE Override |
|---|---|---|---|
| RDMA scheduling | `networking.gke.io.networks/rdma-X` | (injected by webhook) | limits set to `0` |
| GKE NCCL tuner | `NCCL_TUNER_PLUGIN` / `NCCL_NET_PLUGIN` | (not set) | `none` / `""` (via component) |
| DEEP_EP HCA mapping | `DEEP_EP_DEVICE_TO_HCA_MAPPING` | (not set) | GPU-to-NIC index mapping |
| NVSHMEM gdrcopy | `NVSHMEM_DISABLED_GDRCOPY` | (not set) | `true` |
| HF XET | `HF_HUB_DISABLE_XET` | (not set) | `1` |
| Host volumes | `hf-cache` / `jit-cache` | `emptyDir` | GKE hostPath (`/mnt/stateful_partition/...`) |
