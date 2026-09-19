# Pod Snapshots for Single-GPU Model Servers

## Overview

Deploying large language models (LLMs) on Kubernetes often incurs multi-minute cold starts due to downloading model weights from HuggingFace and loading them into memory.

This guide demonstrates how to checkpoint and restore a **single-GPU** vLLM model server (e.g., an NVIDIA H100 80GB GPU serving `Qwen/Qwen3-32B`), currently implemented with **GKE Pod Snapshots** and **GKE Sandbox (gVisor)** — see [Platform Support](#platform-support).

When launched with `python3 -m docker.scripts.snapshot.launcher`, the snapshot launcher orchestrates the checkpoint and restore lifecycle automatically:

1. **First Pod (Cold Start & Snapshot Creation):** The initial replica loads the model weights, captures CUDA graphs, and calls `engine.sleep(level=1)` to offload weights from GPU VRAM to host CPU RAM while discarding the KV cache. It then purges the on-disk weight cache so the downloaded files are not duplicated in the container filesystem snapshot, and triggers a GKE Pod Snapshot uploaded to Google Cloud Storage (GCS).
2. **Subsequent Pods (Fast Restoration):** When pods scale out or restart, GKE automatically restores the container from the GCS snapshot in seconds—bypassing model weight downloads and engine initialization.
3. **Serve Traffic:** The restored pod immediately wakes up GPU memory and begins serving inference requests.

### Platform Support

This guide currently implements pod snapshotting on **GKE**, using GKE Pod Snapshots with GKE Sandbox (gVisor) and Google Cloud Storage. The snapshot mechanism is reached through a pluggable provider (`docker/scripts/snapshot/providers.py`), so the launcher, the vLLM wrapper, and the sleep/restore lifecycle are platform-independent — only the checkpoint backend is GKE-specific.

We are also working on a **platform-agnostic implementation based on [CRIU](https://criu.org/)** (Checkpoint/Restore In Userspace), which would bring the same cold-start elimination to any Kubernetes cluster without depending on a managed snapshot service.

**Contributions are welcome**, particularly on the CRIU-based provider or on snapshot backends for other platforms.

---

## Single-GPU Scope & Technical Caveats

1. **Single-GPU Only — `TP>1` Is Not Supported:**
   This guide supports single-GPU pods only (`nvidia.com/gpu: 1`, `TP=1`, `DP=1`). Tensor parallelism greater than 1 is **not supported** in this guide, and benchmark results target a single NVIDIA H100 80GB. Scale capacity horizontally by adding replicas, not by adding GPUs per replica.
2. **Snapshot Warmup (`replicas: 1` → `N`):**
   Once a `PodSnapshot` is `Ready` in GCS, you can set any number of `replicas` in your Deployment (or use HPA/KEDA) and all pods will restore in seconds. When creating a snapshot for the **first** time (or after changing flags/images), start with `replicas: 1` until the `PodSnapshot` reaches `Ready=True` before scaling out—otherwise pods scheduled before the snapshot completes will cold-start.
3. **Sleep Mode (`--enable-sleep-mode`):**
   Recommended, not required. It lets `engine.sleep(level=1)` offload weights from GPU VRAM to host RAM and discard the KV cache before checkpointing. Snapshot and restore also succeed without it—GKE captures resident VRAM directly—but enabling it measured faster end to end (shorter checkpoint upload, smaller snapshot, faster restore). If omitted, `engine.sleep()` silently no-ops rather than erroring, so verify the cold-start logs report a non-zero `Sleep mode freed N GiB memory`.
4. **Eager Safetensors Loading (`--safetensors-load-strategy eager`):**
   Required when purging `MODEL_CACHE_DIR` before checkpointing, as default `mmap` loading leaves file-backed mappings to deleted weight files that fail or bloat the gVisor snapshot.
5. **Workload-Triggered Policy:**
   The GKE `PodSnapshotPolicy` sets `spec.triggerConfig.type: workload` (with `postCheckpoint: resume`). If set to `manual`, the policy waits for a `PodSnapshotTrigger` resource instead, and the container's write to `/proc/gvisor/checkpoint` does nothing.
6. **Snapshot Matching & `env` Changes:**
   Changing the container `image`, `command`, `args`, or node/driver version automatically invalidates existing snapshots and triggers a fresh cold start. However, container `env` variables are **not** hashed—if you edit an `env` variable, you must manually run `kubectl delete podsnapshots --all -n <namespace>` to force a new snapshot, or restored pods will keep the old environment captured at checkpoint time. See [How GKE Matches Pods to Snapshots](#how-gke-matches-pods-to-snapshots).
7. **gVisor Localhost Isolation (`kubectl port-forward`):**
   `kubectl port-forward pod/<vllm-pod>` fails with `connection refused` because `kubelet` dials `127.0.0.1` in the host CNI namespace rather than inside gVisor's user-space network stack. To test from your local machine, port-forward to the router service (`kubectl port-forward service/gke-pod-snapshots-epp 8080:80`) or use an in-cluster test pod.
8. **Hierarchical Namespace GCS Buckets:**
   The snapshot bucket must be created with `--enable-hierarchical-namespace`. Hierarchical namespace cannot be turned on after the fact, so an existing flat bucket cannot be reused.
9. **Cloud Storage FUSE CSI Driver Is Unsupported:**
   Pods using the Cloud Storage FUSE CSI sidecar cannot be snapshotted. Model weights must be downloaded to the container filesystem, as this guide does, rather than mounted from a bucket.
10. **Host Memory & Snapshot Sizing:**
    Because `engine.sleep(level=1)` copies model weights from GPU VRAM into host CPU RAM before checkpointing, ensure container `memory` requests and limits (`96Gi–128Gi` in `patch-vllm.yaml`) are large enough to hold the full model weights alongside the vLLM process without being `OOMKilled`. The resulting GCS snapshot (`pages.img`) is roughly equal to the model weight footprint (purging `MODEL_CACHE_DIR` prevents storing a duplicate copy on disk).

> [!WARNING]
> **Snapshots capture process memory and environment secrets (including `HF_TOKEN`).** Restrict GCS bucket access to least-privilege IAM principals, and note that rotating a Kubernetes Secret requires deleting existing `PodSnapshot` resources to take effect.

---

## Configuration

### Snapshot Variables

These are **container** environment variables set on the pod by the `gke/` deployment overlay (`patch-vllm.yaml`), not exported in your shell:

| Variable | Description | Default | Set by `gke/` overlay |
| :--- | :--- | :--- | :--- |
| `SNAPSHOT_PROVIDER` | Snapshot provider backend read by [`docker/scripts/snapshot`](../../docker/scripts/snapshot). Any unrecognized value disables snapshotting. | `""` (disabled) | `gke_gvisor` |
| `MODEL_CACHE_DIR` | Local model weight cache directory purged before triggering the checkpoint so downloaded files are not duplicated on disk. Unset means no purge. | `""` (no purge) | `~/.cache/huggingface/hub` |
| `VLLM_HOST_IP` | Pins the `torch.distributed` TCPStore to loopback so restored pods do not log `Broken pipe` warnings when their pod IP changes. | Pod IP | `127.0.0.1` |

---

## Prerequisites

### GKE: Cluster Pre-provisioning (with Pod Snapshots & GKE Sandbox)

Before running this guide, make sure your GKE cluster, GPU node pool, and GCS storage bucket are configured.

> [!NOTE]
> Replace `<PROJECT_ID>`, `<REGION>`, `<ZONE>`, `<CLUSTER_NAME>`, `<NODE_POOL_NAME>`, `<GPU_MACHINE_TYPE>`, `<GPU_ACCELERATOR>`, `<GPU_COUNT>`, `<MAX_NODES>`, `<DISK_SIZE>`, `<GCS_BUCKET>`, and `<NAMESPACE>` (default: `llm-d-gke-pod-snapshots`) below:
>
> - **GPU Machine & Accelerator (`<GPU_MACHINE_TYPE>`, `<GPU_ACCELERATOR>`, `<GPU_COUNT>`):** e.g., `--machine-type=a3-highgpu-1g` with `--accelerator=type=nvidia-h100-80gb,count=1,gpu-driver-version=latest`. See [supported GPU machine types](https://cloud.google.com/kubernetes-engine/docs/concepts/gpus#gpu_machine_types) and [zone availability](https://cloud.google.com/compute/docs/gpus/gpu-regions-zones).
> - **Capacity & Provisioning (`--spot` / `--flex-start`):** Depending on regional GPU availability and quota for some machine types, add `--spot` or `--flex-start` to the node pool creation command if standard on-demand capacity is unavailable.
> - **Autoscaling & Node Locations (`<ZONE>`, `<MAX_NODES>`):** The workload starts with 1 replica to create the initial snapshot; `--enable-autoscaling` (`--min-nodes=1 --max-nodes=<MAX_NODES>`) allows GKE to automatically provision additional GPU nodes when scaling out replicas. Pinning `--node-locations` to a single zone ensures `--num-nodes` and `--max-nodes` apply to that zone.
> - **Boot Disk Size (`<DISK_SIZE>`):** Must fit the container image and temporary model weight download during cold start prior to `MODEL_CACHE_DIR` purge (e.g., `200GB`). See [Pod Snapshots node pool requirements](https://cloud.google.com/kubernetes-engine/docs/how-to/pod-snapshots#create-node-pool).
> - **Default Node Pool (`e2-standard-16`):** The router's endpoint picker (`epp`) requests 8 vCPU and 16 GiB RAM, which will not fit on GKE's default `e2-medium` nodes.

1. **Create a GKE Cluster with Pod Snapshots, Workload Identity & Image Streaming:**

   ```bash
   gcloud container clusters create "<CLUSTER_NAME>" \
     --region="<REGION>" \
     --node-locations="<ZONE>" \
     --machine-type=e2-standard-16 \
     --num-nodes=1 \
     --release-channel=rapid \
     --workload-pool="<PROJECT_ID>.svc.id.goog" \
     --enable-image-streaming \
     --enable-pod-snapshots
   ```

2. **Create an Autoscaled GPU Node Pool with GKE Sandbox (gVisor) & Image Streaming:**

   ```bash
   gcloud container node-pools create "<NODE_POOL_NAME>" \
     --cluster="<CLUSTER_NAME>" \
     --region="<REGION>" \
     --node-locations="<ZONE>" \
     --machine-type="<GPU_MACHINE_TYPE>" \
     --disk-size="<DISK_SIZE>" \
     --image-type=cos_containerd \
     --workload-metadata=GKE_METADATA \
     --enable-image-streaming \
     --sandbox type=gvisor \
     --accelerator=type=<GPU_ACCELERATOR>,count=<GPU_COUNT>,gpu-driver-version=latest \
     --num-nodes=1 \
     --enable-autoscaling \
     --min-nodes=1 \
     --max-nodes="<MAX_NODES>"
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

   # Grant the workload's Kubernetes Service Account (KSA) direct read/write access via Workload Identity Federation.
   # storage.buckets.get is required for parallel composite uploads: the GCS client reads bucket metadata to
   # decide whether it may upload in parallel chunks. Without it the checkpoint falls back to a single stream,
   # which is dramatically slower. Do not trim it when narrowing this role.
   gcloud iam roles create podSnapshotGcsReadWriter \
     --project="<PROJECT_ID>" \
     --title="Pod Snapshot GCS Read/Writer" \
     --permissions="storage.buckets.get,storage.objects.get,storage.objects.list,storage.objects.create,storage.objects.delete,storage.folders.create"

   gcloud storage buckets add-iam-policy-binding gs://<GCS_BUCKET> \
     --member="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/<PROJECT_ID>.svc.id.goog/subject/ns/<NAMESPACE>/sa/gke-pod-snapshots-nvidia-gpu-vllm-sa" \
     --role="projects/<PROJECT_ID>/roles/podSnapshotGcsReadWriter"
   ```

#### Recommended Settings for Performance

- **GKE Image Streaming (`--enable-image-streaming`):** Enabling Image Streaming on both the cluster and GPU node pool (with container images hosted in Artifact Registry) streams container layers on demand when scaling out to new nodes, avoiding upfront full image downloads before snapshot restore begins.
- **GCS Bucket Configuration:** Co-locate the GCS bucket in the same region (`--location=<REGION>`), enable hierarchical namespace (`--enable-hierarchical-namespace`), set `--soft-delete-duration=0`, and avoid CMEK encryption overhead so gVisor can stream checkpoint memory pages using parallel composite uploads.
- **IAM Permissions (`storage.buckets.get`):** Parallel composite uploads depend on the workload's custom role including `storage.buckets.get`, which lets the GCS client inspect bucket metadata before choosing an upload strategy. If the role is narrowed and this permission is dropped, the checkpoint still succeeds but silently falls back to a single-stream upload — confirm `componentCount` on the uploaded `pages.img` is greater than `1`.

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
export GCS_BUCKET=gcs-bucket-placeholder
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
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
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

- Install the standalone router:

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
<summary><b>Gateway Mode (Optional)</b></summary>

To use a Kubernetes Gateway instead of standalone mode, deploy a Gateway named `llm-d-inference-gateway` (see [Gateway guides](../../docs/infrastructure/gateway)) and install the Gateway router chart:

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
  | sed "s/gcs-bucket-placeholder/${GCS_BUCKET}/g" \
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
> **The pod pauses log output during the checkpoint upload, which is normal.** It remains at `0/1 Running`
> while `engine.sleep(level=1)` offloads VRAM and gVisor freezes the sandbox to stream the memory image to GCS (see [Benchmarking Reports](#benchmarking-reports)). Wait for the `PodSnapshot` `STATUS` to reach `AllSnapshotsAvailable` (`Ready=True`).

### 2. Scale Out & Verify Fast Restoration

Now that the initial `PodSnapshot` is `AllSnapshotsAvailable` (`Ready=True`), any new pods matching the deployment spec will restore directly from GCS. Scale the deployment to `2` replicas to see the new pod restore in seconds (triggering GKE node pool autoscaling if a second GPU node is needed):

<!-- guide:verify.tests.restore start -->
```bash
kubectl scale deployment -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE} --replicas=2
kubectl wait --for=condition=ready pod -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE} --timeout=600s
kubectl get events -n ${NAMESPACE} --field-selector reason=GKEPodSnapshotting --sort-by=.lastTimestamp
kubectl logs -l llm-d.ai/guide=${GUIDE_NAME} -n ${NAMESPACE} --tail=20
```
<!-- guide:verify.tests.restore end -->

Verify the most recent `GKEPodSnapshotting` event confirms restoration from the snapshot:

```text
LAST SEEN   TYPE     REASON               OBJECT                     MESSAGE
58s         Normal   GKEPodSnapshotting   pod/<pod-name>             Successfully restored the pod from PodSnapshot <namespace>/<snapshot-id>
```

And confirm the pod logs show `engine.wake_up()` instead of reloading weights:

```text
(APIServer pid=1) INFO MM-DD HH:MM:SS [wrapper.py:84] Process restored from snapshot checkpoint. Resuming engine...
(APIServer pid=1) INFO MM-DD HH:MM:SS [wrapper.py:88] Executing engine.wake_up() to restore VRAM...
(EngineCore pid=NN) INFO MM-DD HH:MM:SS [abstract.py:345] It took N seconds to wake up tags {'kv_cache', 'weights'}.
(APIServer pid=1) INFO:     Application startup complete.
```

### 3. Verify Inference Endpoint

- Resolve the standalone router Service IP:

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

<details>
<summary><b>Gateway Mode (Optional)</b></summary>

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

> [!NOTE]
> **Deletion order matters:** Delete `PodSnapshot` resources before removing `PodSnapshotStorageConfig` so GKE deletes the backing GCS objects (if snapshots hang in `Terminating`, ensure the GKE Service Agent has `roles/storage.objectUser` on the bucket). Delete the GKE cluster and GCS bucket separately when finished to stop billing.

---

## Benchmarking Reports

- **[Qwen/Qwen3-32B on vLLM (1×H100 Snapshot & Restore)](./benchmark-results/vllm-qwen3-32b-h100.md)**: End-to-end pod lifecycle timings comparing cold start against snapshot restore.

> [!NOTE]
> GKE Pod Snapshots targets pod startup latency; because restoration resumes the initialized vLLM process and CUDA graphs in GPU memory, steady-state serving behavior is expected to match a standard deployment.
