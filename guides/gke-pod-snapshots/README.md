# GKE Pod Snapshots for Single-GPU Model Servers

## Overview

Deploying large language models (LLMs) on Kubernetes often incurs multi-minute cold starts due to downloading model weights from HuggingFace and loading them into memory.

This guide demonstrates how to use **GKE Pod Snapshots** with **GKE Sandbox (gVisor)** to checkpoint and restore a **single-GPU** vLLM model server (e.g., an NVIDIA H100 80GB GPU serving `Qwen/Qwen3-32B`).

When launched with `python3 -m docker.scripts.snapshot.launcher`, the snapshot launcher orchestrates the checkpoint and restore lifecycle automatically:

1. **First Pod (Cold Start & Snapshot Creation):** The initial replica loads the model weights (e.g. ~64 GB for `Qwen/Qwen3-32B`) and calls `engine.sleep(level=1)`, which releases physical GPU memory. It then purges the on-disk weight cache so the same bytes are not also captured from the container filesystem, and triggers a GKE Pod Snapshot uploaded to Google Cloud Storage (GCS).
2. **Subsequent Pods (Fast Restoration):** When pods scale out or restart, GKE automatically restores the container from the GCS snapshot in seconds—bypassing model weight downloads and engine initialization.
3. **Serve Traffic:** The restored pod immediately wakes up GPU memory and begins serving inference requests.

---

## Single-GPU Scope & Technical Caveats

1. **Single-Rank Scope (One GPU Per Pod):**
   The launcher patches a single vLLM API server process, so each pod hosts exactly one rank (`nvidia.com/gpu: 1`, no tensor or data parallelism). Scaling out to multiple replicas is supported; pods snapshot and restore independently.
2. **Sleep Mode (`--enable-sleep-mode`):**
   Sleep mode is what makes vLLM route weight and KV cache allocations through `CuMemAllocator`. Without it `engine.sleep(level=1)` still returns successfully but frees nothing, so the checkpoint is taken with the GPU fully resident and the snapshot is correspondingly larger. Measured on Qwen3-32B, sleep moved 61.68 GiB of weights to host RAM and discarded 12.52 GiB of KV cache. Because the no-op is silent, confirm the pod logs report a non-zero `Sleep mode freed N GiB memory` before the checkpoint.
3. **Eager Safetensors Loading (`--safetensors-load-strategy eager`):**
   By default, `safetensors` uses `mmap` to memory-map weight files directly from disk. Because `MODEL_CACHE_DIR` is purged before the checkpoint is taken, those mappings would point at files that no longer exist. Passing `--safetensors-load-strategy eager` reads the weights into anonymous memory instead, which is what the snapshot captures.
4. **Workload-Triggered Policy:**
   The GKE `PodSnapshotPolicy` sets `spec.triggerConfig.type: workload` (with `postCheckpoint: resume`). If set to `manual`, the policy waits for a `PodSnapshotTrigger` resource instead, and the container's write to `/proc/gvisor/checkpoint` does nothing.
5. **Restores Are Environment-Specific:**
   Upgrading a node pool, or editing the container image, command, or args, invalidates existing snapshots: the pod no longer matches, so it silently cold-starts and writes a new one. Environment variables are **not** part of the match — editing one leaves the snapshot valid, and a restored pod keeps the environment captured at checkpoint time rather than the one in its spec. Delete the `PodSnapshot` to force a fresh capture. See [How GKE Matches Pods to Snapshots](#how-gke-matches-pods-to-snapshots).
6. **gVisor Localhost Isolation:**
   Inside gVisor sandboxes, `kubectl port-forward pod/<pod-name>` reports a healthy tunnel and then fails on first request with `connection refused` — it dials `localhost` inside the sandbox's network namespace, which is not routed. Test connectivity via a Kubernetes `Service` or an in-cluster test pod.
7. **Hierarchical Namespace GCS Buckets:**
   The snapshot bucket must be created with `--enable-hierarchical-namespace`. Hierarchical namespace cannot be turned on after the fact, so an existing flat bucket cannot be reused.
8. **Cloud Storage FUSE CSI Driver Is Unsupported:**
   Pods using the Cloud Storage FUSE CSI sidecar cannot be snapshotted. Model weights must be downloaded to the container filesystem, as this guide does, rather than mounted from a bucket.
9. **Snapshots Are Model-Sized:**
   `engine.sleep(level=1)` offloads weights to host RAM rather than discarding them, and a whole-pod snapshot captures that RAM — so the artifact is roughly the model's VRAM footprint, not a few gigabytes. Purging `MODEL_CACHE_DIR` only stops those bytes being captured twice. Size node memory for the offloaded weights alongside the running process, budget GCS storage accordingly, and expect the checkpoint freeze to scale with size — see [Benchmarking Reports](#benchmarking-reports).

> [!WARNING]
> **A snapshot is a memory image of the pod, so it contains every secret the process holds.**
> `HF_TOKEN` is the obvious one — it is in the process environment when the checkpoint fires, so it
> is written into `pages.img` and survives into every restored pod. Anything else the container has
> in memory is captured the same way. Treat the snapshot bucket as exactly as sensitive as the pod
> itself: restrict it to the workload's own principal as the IAM step below does, prefer short-lived
> credentials for gated models, and remember that rotating a Kubernetes Secret does **not** change
> the value baked into an existing snapshot.

---

## Configuration

### Snapshot Variables

These are **container** environment variables read by [`docker/scripts/snapshot`](../../docker/scripts/snapshot). They are set on the pod by the deployment overlay, not exported in your shell. The `Default` column is the behavior when the variable is absent from the container.

| Variable | Description | Default | Set by `gke/` overlay |
| :--- | :--- | :--- | :--- |
| `SNAPSHOT_PROVIDER` | Snapshot provider backend. Selects the implementation in `providers.py`; any unrecognized value disables snapshotting. | `""` (disabled) | `gke_gvisor` |
| `MODEL_CACHE_DIR` | Local model weight cache directory purged before triggering the checkpoint, so the on-disk copy of the weights is not captured in addition to the in-memory one. Unset means no purge. | `""` (no purge) | `~/.cache/huggingface/hub` |

> [!NOTE]
> `SNAPSHOT_PROVIDER` is fixed per overlay rather than templated, because it must agree with the manifests deployed alongside it — the `gke/` overlay applies `podsnapshot.gke.io` resources that only the `gke_gvisor` provider acts on. Support for an additional backend would be added as a sibling overlay directory.

The overlay also sets `VLLM_HOST_IP=127.0.0.1`, which pins the `torch.distributed` TCPStore to loopback; a store bound to the pod IP does not survive the checkpoint, and every restored pod then logs a `Broken pipe` warning once per second.

---

## Prerequisites

### GKE: Cluster Pre-provisioning (with Pod Snapshots & GKE Sandbox)

Before running this guide, make sure your GKE cluster, GPU node pool, and GCS storage bucket are configured.

> [!NOTE]
> Replace `<PROJECT_ID>`, `<REGION>`, `<ZONE>`, `<CLUSTER_NAME>`, `<NODE_POOL_NAME>`, `<GPU_MACHINE_TYPE>`, `<GPU_ACCELERATOR>`, `<DISK_SIZE>`, `<GCS_BUCKET>`, and `<NAMESPACE>` (default: `llm-d-gke-pod-snapshots`) below to match your target environment:
>
> - **GPU Machine Family & Accelerator (`<GPU_MACHINE_TYPE>`, `<GPU_ACCELERATOR>`):** See [Choose a GPU machine type on GKE](https://cloud.google.com/kubernetes-engine/docs/concepts/gpus#gpu_machine_types) and [GPU availability by region and zone](https://cloud.google.com/compute/docs/gpus/gpu-regions-zones) — for example `--machine-type=a3-highgpu-1g` with `--accelerator=type=nvidia-h100-80gb,count=1,gpu-driver-version=latest`.
> - **Node Locations (`<ZONE>`):** `--num-nodes` applies *per zone* on a regional node pool, so pinning to one zone with `--node-locations` keeps this single-GPU guide at exactly one GPU node — and avoids zones that lack your machine type.
> - **Boot Disk Size (`<DISK_SIZE>`):** The boot disk holds the container image, the model weights downloaded during cold start, and local snapshot staging files before they are uploaded to GCS (for example, `200GB`). See [node pool requirements](https://cloud.google.com/kubernetes-engine/docs/how-to/pod-snapshots#create-node-pool).
> - **Default Node Pool:** The cluster below uses `e2-standard-16` because the router's endpoint picker pod requests 8 vCPU and 16 GiB, which the default `e2-medium` cannot fit — the pod would sit `Pending` with no error.

1. **Create a GKE Cluster with Pod Snapshots & Workload Identity:**

   ```bash
   gcloud container clusters create "<CLUSTER_NAME>" \
     --region="<REGION>" \
     --node-locations="<ZONE>" \
     --machine-type=e2-standard-16 \
     --num-nodes=1 \
     --release-channel=rapid \
     --workload-pool="<PROJECT_ID>.svc.id.goog" \
     --enable-pod-snapshots
   ```

2. **Create a Single-GPU Node Pool with GKE Sandbox (gVisor):**

   ```bash
   gcloud container node-pools create "<NODE_POOL_NAME>" \
     --cluster="<CLUSTER_NAME>" \
     --region="<REGION>" \
     --node-locations="<ZONE>" \
     --machine-type="<GPU_MACHINE_TYPE>" \
     --disk-size="<DISK_SIZE>" \
     --image-type=cos_containerd \
     --workload-metadata=GKE_METADATA \
     --sandbox type=gvisor \
     --accelerator=type=<GPU_ACCELERATOR>,count=1,gpu-driver-version=latest \
     --num-nodes=1
   ```

3. **Create a Hierarchical Namespace GCS Bucket & Bind Workload Identity:**

   ```bash
   PROJECT_NUMBER=$(gcloud projects describe "<PROJECT_ID>" --format="value(projectNumber)")

   gcloud storage buckets create gs://<GCS_BUCKET> \
     --location="<REGION>" \
     --enable-hierarchical-namespace \
     --soft-delete-duration=0 \
     --uniform-bucket-level-access

   # Grant bucket object user to the GKE Service Agent (required by GKE's controller to delete snapshots)
   gcloud storage buckets add-iam-policy-binding gs://<GCS_BUCKET> \
     --member="serviceAccount:service-${PROJECT_NUMBER}@container-engine-robot.iam.gserviceaccount.com" \
     --role="roles/storage.objectUser"

   # Grant the Kubernetes Service Account (KSA) principal direct read/write
   # access to the bucket. The PodSnapshotStorageConfig in this guide uses
   # tokenSource "podKSA", so the gVisor sandbox authenticates to GCS as the KSA
   # principal itself via Workload Identity Federation. No GCP service account
   # and no impersonation binding are involved in the snapshot path.
   gcloud iam roles create podSnapshotGcsReadWriter \
     --project="<PROJECT_ID>" \
     --permissions="storage.objects.get,storage.objects.create,storage.objects.delete,storage.folders.create"

   gcloud storage buckets add-iam-policy-binding gs://<GCS_BUCKET> \
     --member="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/<PROJECT_ID>.svc.id.goog/subject/ns/<NAMESPACE>/sa/gke-pod-snapshots-nvidia-gpu-vllm-sa" \
     --role="projects/<PROJECT_ID>/roles/podSnapshotGcsReadWriter"
   ```

### Checkout Repo & Setups

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Checkout the `llm-d` repository:

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH:-main}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

- Set the guide-specific environment variables:

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=gke-pod-snapshots
export NAMESPACE=llm-d-gke-pod-snapshots
```
<!-- llm-d-cicd:skip start -->
```bash
export GCS_BUCKET=GCS_BUCKET_PLACEHOLDER
export HF_TOKEN=HF_TOKEN_PLACEHOLDER
```
<!-- llm-d-cicd:skip end -->
```bash
export MODEL=Qwen/Qwen3-32B
export CURL_TEST_IMAGE=cfmanteiga/alpine-bash-curl-jq:latest
```
<!-- guide:env.static end -->

- Source the common guide environment variables (`GAIE_VERSION`, `ROUTER_CHART_VERSION`, `ROUTER_STANDALONE_CHART`, …):

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

- Install the Gateway API Inference Extension CRDs:

<!-- guide:prerequisites.gaie start -->
```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

- Create the target namespace:

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

- Create the HuggingFace token secret:

<!-- guide:prerequisites.secrets start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.secrets end -->

---

## Installation Instructions

### 1. Deploy the Router

- Configure router values:

<!-- guide:deploy.router_values start -->
```bash
export ROUTER_BASE_VALUES="-f ${REPO_ROOT}/guides/recipes/router/base.values.yaml"

export ROUTER_VALUES="-f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

- Install the router (standalone mode — the EPP and its Envoy sidecar run in one pod behind a `ClusterIP` Service, with no Kubernetes Gateway required):

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  ${ROUTER_BASE_VALUES} \
  ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

<details>
<summary><b>Gateway Mode</b></summary>

To route through a Kubernetes Gateway managed proxy instead of the standalone Envoy sidecar, do **not** install the standalone chart above. Instead:

1. **Install the Gateway API CRDs and deploy a Gateway.** The [Prerequisites](#prerequisites) install only the Gateway API Inference Extension CRDs, which do not include `gateway.networking.k8s.io`. See [the gateway guides](../../docs/infrastructure/gateway) for both, and deploy a Gateway named `llm-d-inference-gateway`.

2. **Install the router and its `HTTPRoute`:**

<!-- guide:deploy.gateway start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  ${ROUTER_BASE_VALUES} \
  ${ROUTER_VALUES} \
  --set provider.name=gke \
  --set httpRoute.create=true \
  --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.gateway end -->

</details>

### 2. Deploy the Single-GPU Model Server & Snapshot Policies

Apply the Kustomize overlay to deploy the `PodSnapshotStorageConfig`, `PodSnapshotPolicy`, and single-GPU vLLM `Deployment` running under `runtimeClassName: gvisor`:

<!-- guide:deploy.modelserver start -->
```bash
kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/gke/ \
  | sed "s/GCS_BUCKET_PLACEHOLDER/${GCS_BUCKET}/g" \
  | kubectl apply -n ${NAMESPACE} -f -
```
<!-- guide:deploy.modelserver end -->

---

## How GKE Matches Pods to Snapshots

With policy-based snapshotting (`PodSnapshotPolicy`), GKE transparently matches restored pods to the correct snapshot without needing hardcoded snapshot IDs:

1. **Distilled Pod Spec Hash:** GKE computes a hash over runtime-critical pod fields (container image, commands, arguments, and sandbox settings). Environment variables are not included: changing one does not invalidate a snapshot, and the restored process keeps the environment it was captured with.
2. **Node Compatibility Metadata:** GKE labels the snapshot with the node's machine family and type, GPU model, GPU driver version, and GKE node version.
3. **Lookup & Restoration:** When a new replica is scheduled (or a pod is recreated), GKE matches the pod's distilled hash and node metadata to the most recent matching `PodSnapshot` in the cluster and restores directly from GCS.

---

## Verification

### 1. Monitor Snapshot Creation

Wait for the initial cold start to complete and check the `PodSnapshot` status:

<!-- guide:verify.tests.snapshot start -->
```bash
kubectl wait --for=condition=ready pod -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE} --timeout=2400s
kubectl wait --for=condition=Ready podsnapshots --all -n ${NAMESPACE} --timeout=600s
kubectl get podsnapshots -n ${NAMESPACE}
```
<!-- guide:verify.tests.snapshot end -->

Status progression:

1. `AwaitingCheckpoint`: GKE signaled gVisor to freeze the container runtime.
2. `AllSnapshotsAvailable`: Snapshot files have been uploaded to GCS and are ready for restoration.

> [!IMPORTANT]
> **The pod appears hung for the entire upload, and that is normal.** It sits at `0/1 Running`
> with its startup probe failing and no log output while gVisor freezes the sandbox — ~14 minutes
> for `Qwen/Qwen3-32B` on an H100 (see [Benchmarking Reports](#benchmarking-reports)); raise
> `--timeout` for substantially larger models. Bucket size is not a progress signal either:
> `pages.img` is written in one step at the very end, so a flat bucket looks identical whether
> the checkpoint is healthy or stuck. Wait for the `PodSnapshot` conditions.

### 2. Test Pod Restoration & Verify Restore Logs

Delete the running pod so the Deployment schedules a replacement replica that restores directly from the snapshot:

<!-- guide:verify.tests.restore start -->
```bash
kubectl delete pod -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE}
kubectl wait --for=condition=ready pod -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE} --timeout=600s
kubectl get events -n ${NAMESPACE} --field-selector reason=GKEPodSnapshotting --sort-by=.lastTimestamp
kubectl logs -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE} --tail=20
```
<!-- guide:verify.tests.restore end -->

GKE records the restore against the pod, which is the authoritative signal — it names the exact `PodSnapshot` that was used:

```text
LAST SEEN   TYPE     REASON               OBJECT                     MESSAGE
58s         Normal   GKEPodSnapshotting   pod/<pod-name>             Successfully restored the pod from PodSnapshot <namespace>/<snapshot-id>
```

Events from earlier pods stay listed until they age out, so check the most recent row — that is why the command sorts by timestamp. Kubernetes retains events for one hour by default, so run this soon after the restore.

The pod logs corroborate it: a restored pod wakes the engine instead of reloading weights.

```text
(APIServer pid=1) [vllm.snapshot.wrapper] INFO: Executing engine.wake_up() to restore VRAM...
(EngineCore pid=NN) INFO: It took N seconds to wake up tags {'kv_cache', 'weights'}.
(APIServer pid=1) INFO: Application startup complete.
```

### 3. Verify Inference Endpoint

- Resolve the router endpoint IP. The standalone Service is a `ClusterIP`, so it is reachable only from inside the cluster — hence the in-cluster test pod below:

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

<details>
<summary><b>Gateway Mode</b></summary>

<!-- guide:verify.endpoint.gateway start -->
```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
<!-- guide:verify.endpoint.gateway end -->

</details>

- Send a test inference request:

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\"}"'
```
<!-- guide:verify.tests.request end -->

---

## Cleanup

Uninstall the router, delete the `PodSnapshot` resources, remove the model server, and delete the namespace:

<!-- guide:cleanup start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete podsnapshots --all -n ${NAMESPACE} --ignore-not-found=true

kubectl kustomize ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/gke/ \
  | kubectl delete -n ${NAMESPACE} --ignore-not-found=true -f -

kubectl delete namespace ${NAMESPACE}
```
<!-- guide:cleanup end -->

This leaves the cluster, GPU node pool, and GCS bucket in place — delete them separately when you are finished, as the accelerator node pool bills for as long as it exists.

> [!NOTE]
> The ordering matters: `PodSnapshot` resources must be deleted while `PodSnapshotStorageConfig` still exists, or the snapshot data is orphaned in GCS and keeps billing. If they hang in `Terminating`, check that the GKE Service Agent (`service-<PROJECT_NUMBER>@container-engine-robot.iam.gserviceaccount.com`) still holds `roles/storage.objectUser` on the bucket — see [Prerequisites](#prerequisites).

---

## Benchmarking Reports

This guide is hardware-neutral. The report below measures one full cycle — cold start, checkpoint, and restore — on a single accelerator:

- **[Qwen/Qwen3-32B on vLLM (1×H100 Snapshot & Restore)](./benchmark-results/vllm-qwen3-32b-h100.md)**: Compares cold start against restore-from-snapshot, and reports the throughput of the checkpoint and restore paths.

> [!NOTE]
> These reports are measured from pod lifecycle timings rather than with [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark), which drives request-level workloads. The metric this guide improves is startup latency; steady-state serving performance after a restore is unchanged from a normal vLLM deployment.
