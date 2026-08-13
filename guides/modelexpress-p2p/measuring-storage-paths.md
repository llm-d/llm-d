# Measuring Storage-Backed Loading Paths

Assumes the [main guide](./README.md) is deployed and its environment variables are set. Example observations collected with this procedure are in [benchmark-results](./benchmark-results/coreweave-h200-llama-3.3-70b.md).

These tests use `meta-llama/Llama-3.3-70B-Instruct` (gated; needs the HF token secret), not the guide's default `gpt-oss-120b`: `--load-format=fastsafetensors` cannot load MXFP4 checkpoints, so a dense bf16 model is required to compare the storage paths.

Run the fastsafetensors tests the same way you ran the P2P test: prewarm once, then scale 1→N and record `Loading weights took` plus the scale-out wall clock. Keep cudagraph/compile constant across every path you measure (set `--enforce-eager` on all, or layer `shared-compile-cache` on all) so the weight path stays the main variable.

These measurement workloads sit outside the router on purpose. The router's `modelServers.matchLabels` selects `llm-d.ai/guide: modelexpress-p2p`, so the fastsafetensors pods never receive routed traffic. Measure them by port-forwarding or by hitting the pods directly.

## 1. Prewarm the checkpoint onto NFS (once)

The prewarm Job and RWX PVC use their own kustomize overlay without a `namePrefix`, so the PVC keeps the literal name `fst-model-cache`, and both fastsafetensors tests share the single download. The PVC requests the `shared-vast` StorageClass (CoreWeave's VAST-backed NFS, the cluster this guide was validated on). Edit `fastsafetensors-prewarm/prewarm-job.yaml` if your cluster's RWX-capable class has a different name:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/fastsafetensors-prewarm/
kubectl wait --for=condition=complete job/fst-prewarm-llama33-70b -n ${NAMESPACE} --timeout=60m
```

This is the **one** HuggingFace download for all storage paths. It is idempotent: re-running the experiment reuses the warm PVC.

## 2. fastsafetensors off prewarmed NFS (primary)

```bash
DECODE=fastsafetensors-nfs-nvidia-gpu-vllm-decode
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/fastsafetensors-nfs/

# Seed: scale to 1 and wait Ready (reads the warm NFS PVC, not HuggingFace)
kubectl scale deploy/${DECODE} -n ${NAMESPACE} --replicas=1
kubectl rollout status deploy/${DECODE} -n ${NAMESPACE} --timeout=30m

# Scale-out: time 1 -> 2
T0=$(date +%s)
kubectl scale deploy/${DECODE} -n ${NAMESPACE} --replicas=2
kubectl rollout status deploy/${DECODE} -n ${NAMESPACE} --timeout=10m
T1=$(date +%s); echo "fastsafetensors-nfs pool reached Ready in $((T1 - T0))s"

# Per-pod weight-load time
for p in $(kubectl get pod -n ${NAMESPACE} -l llm-d.ai/guide=fastsafetensors-nfs -o name); do
    kubectl logs -n ${NAMESPACE} $p -c modelserver | grep -iE 'Loading weights took'
done
```

Check whether GDS engaged. It needs an RDMA-capable mount, so on a TCP NFS mount (like CoreWeave's, where this guide was validated) expect the POSIX-pread path instead:

```bash
kubectl logs -n ${NAMESPACE} <pod> -c modelserver | grep -iE 'cuFile|GDS is not supported|nogds'
# If "GDS is not supported in this platform but nogds is False" appears, add
# --model-loader-extra-config={"nogds": true} to patch-vllm.yaml and re-run
# (the test still works on the POSIX-pread path; note it when recording results).
```

## 3. fastsafetensors off warm local NVMe (optional)

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/fastsafetensors-localnvme/
# Same scale-1-then-1->2 timing on deploy fastsafetensors-localnvme-nvidia-gpu-vllm-decode.
# Confirm P2PDMA engaged vs nogds compat with the same grep as the NFS test.
```

This test pins to NVMe nodes (`nodeSelector: local-persistent-storage=true`). An init container copies the warm NFS checkpoint to a node-local NVMe `emptyDir` (the untimed prime) before the timed serve.

## 4. Per-pod timing data (all tests)

```bash
kubectl get pod -n ${NAMESPACE} -l llm-d.ai/guide=<test> \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.creationTimestamp}{"\t"}{.status.conditions[?(@.type=="Ready")].lastTransitionTime}{"\n"}{end}'
# Diff creationTimestamp vs Ready lastTransitionTime per pod; report median + max.
```

Tear each test down before the next so they do not compete for GPUs (each is 4 GPUs at the default `replicas: 2`):

```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/<test>/
kubectl wait --for=delete pod -l llm-d.ai/guide=<test> -n ${NAMESPACE} --timeout=10m
```

Keep the `fst-model-cache` PVC between runs (the download is the expensive part). Delete it only when you are fully done.

Tear-down commands for these workloads are in the main guide [Cleanup](./README.md#cleanup).
