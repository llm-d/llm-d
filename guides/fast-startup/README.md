# LLM Cold Start Optimization: GKE Image Streaming and Pod Snapshots

Deploying large language models (LLMs) on Kubernetes often suffers from high cold-start latencies. For instance, launching a container to serve `meta-llama/Llama-3.1-8B-Instruct` can take **nearly 7 minutes** due to:
1. **Network Transfer**: Pulling the massive container image (~9.6GB) over the network (~3.6 minutes).
2. **Model Initialization**: Downloading model weights from HuggingFace, loading them into host memory, allocating GPU VRAM, and initializing the CUDA graph (~3.25 minutes).

This guide demonstrates how to combine two powerful GKE-exclusive features—**GKE Image Streaming** and **GKE Pod Snapshots**—to eliminate both bottlenecks. Together, they reduce the total cold-start latency of a massive LLM container from **nearly 7 minutes to just 15 seconds (a ~96.3% latency reduction)**, making highly responsive auto-scaling and scale-to-zero architectures practical for production workloads.

---

## Startup Time Benchmark Analysis

The table below showcases the real-world performance improvements achieved across multiple test runs by combining GKE Pod Snapshots and GKE Image Streaming:

| Deployment Scenario | Image Pull Time | Model/Engine Initialization | Total Startup Time | Latency Reduction |
| :--- | :---: | :---: | :---: | :---: |
| **Standard Cold Start (Baseline)** | ~214s | ~195s | **~409s (6.8 mins)** | Baseline |
| **GKE Image Streaming Only** | ~4s | ~195s | **~199s (3.3 mins)** | ~51.6% |
| **Pod Snapshots Only** | ~214s | ~11s | **~225s (3.8 mins)** | ~45% |
| **Optimized Stack (Image Streaming + Pod Snapshots)** | **~4s** | **~11s** | **~15s (15 seconds)** | **~96.3%** |

*Benchmarks are based on running `meta-llama/Llama-3.1-8B-Instruct` on a single NVIDIA L4 GPU.

---

## Overview and Concepts

For a detailed conceptual overview of how GKE Pod snapshots work, refer to the official [About GKE Pod snapshots](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/pod-snapshots) documentation.

To learn more about how GKE Image Streaming functions under the hood, refer to the official [About GKE Image Streaming](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/image-streaming) documentation.

## Key Use Cases

1. **Scale Out**: Accelerate horizontal scaling of model servers under load by warm-starting new replicas.
2. **Scale from Zero**: Aggressively scale down to zero when idle and restore instantly upon request. See the [Workload Autoscaling guide](../workload-autoscaling) for configuring scale-to-zero in llm-d.
3. **Spot VM Recovery**: Minimize disruption when [spot VMs](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/spot-vms) are preempted and replaced.

## Requirements and Limitations

Before proceeding, review the official [GKE Pod Snapshots Requirements and Limitations](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/pod-snapshots#limitations) and [GKE Image Streaming Requirements](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/image-streaming#requirements) for up-to-date details on supported machine types and other requirements.

**Note:** The GKE Pod Snapshots feature has been only validated on single host deployments with NVIDIA L4 GPUs. Multi-host deployment is currently not tested.

---

## How this Guide Works

To demonstrate the full lifecycle and performance benefits of these optimizations, this guide walks through the following workflow:
1. **Environment Setup**: Set up the GKE cluster with GKE Pod Snapshots and GKE Image Streaming enabled.
2. **Multi-Replica Deployment**: Initialize the model server deployment with at least two replicas, in this guide we use 2 replicas.
3. **Snapshot Trigger**: Only one of the active replicas will trigger a system-level memory and GPU state snapshot. During the snapshot, the pod will be frozen. The other replica(s) remains fully operational to serve active traffic. This is the recommended deployment pattern to maintain high availability.
4. **Scale-out Test**: Scale the deployment to three replicas to trigger a warm start (restoring directly from the newly created snapshot).

---

## Prerequisites

### 1. Infrastructure Requirements

To run through this guide, you must have a GKE cluster with:
*   [Workload Identity enabled](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/workload-identity#enable_on_clusters_and_node_pools).
*   [Image Streaming enabled](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/image-streaming#enable-streaming-clusters).
*   [Pod snapshots enabled](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/pod-snapshots#enable).

Additionally, the GKE Sandbox (gVisor) runtime must be enabled on your node pools. For Autopilot clusters, this is enabled automatically. For standard clusters, you must explicitly create a GPU node pool with GKE Sandbox enabled (using the `--sandbox=type=gvisor` flag).

For detailed instructions on enabling Pod snapshots, setting up GKE Sandbox, and configuring GPU node pools, see the official GKE guide on [Enabling Pod snapshots](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/pod-snapshots#enable).

### 2. Environment Preparation

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Checkout llm-d repo:
  ```bash
  export branch="main" # branch, tag, or commit hash
  git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

- Set target environment variables:
  ```bash
  export ROUTER_CHART_VERSION=v0
  export GUIDE_NAME="fast-startup"
  export NAMESPACE="llm-d-${GUIDE_NAME}"
  export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  ```

- Create the target namespace for the installation:
  ```bash
  kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models:
<!-- llm-d-cicd:skip start -->
  ```bash
  export HF_TOKEN=<your HuggingFace token>
  kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
<!-- llm-d-cicd:skip end -->

---

## Pod Snapshot Storage and Policy Configuration

Setting up GKE Pod Snapshots storage involves three main steps:

### 1. Create a GCS Bucket and Configure IAM Permissions
You must create a Google Cloud Storage bucket with hierarchical namespace enabled and set up Workload Identity permissions so your Pods can read and write to the bucket. For complete, step-by-step instructions, follow the official GKE guide on [Store snapshots](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/pod-snapshots#store-snapshots).

> [!IMPORTANT]
> The Kubernetes ServiceAccount (KSA) name used by the model server deployment is dynamically generated by Kustomize using the `namePrefix` configured in `base/kustomization.yaml`.
> - By default, the name of the service account is **`fast-startup-gpu-vllm-sa`**.
> - You must grant this service account `roles/storage.objectUser` permissions on your GCS bucket using Workload Identity.

### 2. Deploy the PodSnapshotStorageConfig
Create and deploy a `PodSnapshotStorageConfig` resource named `pod-snapshot-storage-config` pointing to your GCS bucket, following the official GKE guide on [Configuring snapshot storage](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/pod-snapshots#configure-storage).

### 3. Deploy the PodSnapshotPolicy
Create a `PodSnapshotPolicy` that references your storage configuration and targets the model server pods.

For details on policy configuration options, see [Creating a PodSnapshotPolicy](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/pod-snapshots#create-policy) in the official GKE documentation.

```yaml
apiVersion: podsnapshot.gke.io/v1
kind: PodSnapshotPolicy
metadata:
  name: pod-snapshot-policy
spec:
  storageConfigName: pod-snapshot-storage-config # This must match storage config name
  selector:
    matchLabels:
      llm-d.ai/guide: fast-startup # This must match label in pod definition
  triggerConfig:
    type: workload
    postCheckpoint: resume
```

> [!NOTE]
> * **Namespace Scoping**: `PodSnapshotStorageConfig` is a cluster-scoped resource and is globally accessible. `PodSnapshotPolicy` is a namespace-scoped resource and must be deployed in the same namespace as your model server pods.
> * **Trigger Configuration**: The `triggerConfig.type` must be set to `workload` to allow the container itself to trigger the snapshot programmatically. If it is set to `manual`, GKE will ignore container-initiated writes and expect snapshots to be triggered on-demand by creating `PodSnapshotManualTrigger` custom resources.

Save the above to `snapshot-policy.yaml` and apply it to your target namespace:

```bash
kubectl apply -f snapshot-policy.yaml -n $NAMESPACE
```

## Installation Instructions

### 1. Deploy the llm-d Router

#### Standalone Mode
Deploy the router with a standalone Envoy proxy sidecar:

```bash
helm install ${GUIDE_NAME} \
  oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

#### Gateway Mode (Inference Gateway)
To use a managed Kubernetes Gateway instead of a standalone Envoy sidecar:

1. **Deploy a Kubernetes Gateway**. Follow the [Gateway guides](../prereq/gateways) to deploy a Gateway named `llm-d-inference-gateway`.
2. **Deploy the llm-d Router & HTTPRoute**:

```bash
export PROVIDER_NAME=gke

helm install ${GUIDE_NAME} \
  oci://ghcr.io/llm-d/charts/llm-d-router-gateway-dev \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/recipes/router/features/httproute-flags.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  --set provider.name=${PROVIDER_NAME} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

### 2. Deploy the Model Server
Apply the Kustomize overlay to deploy the model server configured for GKE Sandbox and checkpoint hooks:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/gke/
```

### 3. (Optional) Enable monitoring

> [!NOTE]
> GKE provides [automatic application monitoring](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/configure-automatic-application-monitoring) out of the box. The llm-d [Monitoring stack](../../docs/resources/observability/setup.md) is not required for GKE, but it is available if you prefer to use it.

- Install the [Monitoring stack](../../docs/resources/observability/setup.md).
- Deploy the monitoring resources for this guide.

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```

---

## Triggering the Checkpoint (Warm-up)

When the model server starts for the first time, it performs a "cold start" (downloading the model weights and loading them onto the GPU memory). Once the server is ready, the configured wrapper script executes the checkpoint trigger:

```bash
# Check the logs of the initial pod to watch the startup process
kubectl logs -f -l llm-d.ai/guide=fast-startup -n ${NAMESPACE}
```

When the server is ready, the logs will show:

```logs
Model server is ready. Triggering checkpoint...
```

This triggers GKE to snapshot the container runtime and memory, and save it to the configured GCS bucket.

### Verify Snapshot Status
Keep monitoring the `PodSnapshot` custom resource to check the status of the snapshot:

```bash
kubectl get podsnapshots -o yaml -n ${NAMESPACE}
```

Once the snapshot is complete, the pod will resume and be ready to serve traffic.

## Verification

### 1. Get the IP of the Proxy

**Standalone Mode**

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

<details>
<summary> <b>Gateway Mode</b> </summary>

```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

</details>

### 2. Send Test Requests

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    --env="NAMESPACE=$NAMESPACE" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "prompt": "How are you today?"
    }' | jq
```

Verify that you receive a valid JSON response containing the generated text from the model.

---

## Scale-Up

Once the snapshot is successfully created, we can test scaling up the deployment.

### 1. Scale Up the Deployment

Scale the deployment to 3 replicas:

```bash
kubectl scale deployment fast-startup-gpu-vllm-decode --replicas=3 -n ${NAMESPACE}
```

### 2. Observe Warm Start Events
Watch the events of the new warm-started pod:

```bash
kubectl get events \
  --field-selector involvedObject.name=fast-startup-gpu-vllm-decode-<new-pod-suffix> \
  -o custom-columns='LAST_SEEN:.lastTimestamp,TYPE:.type,REASON:.reason,MESSAGE:.message'
```

Output:
```
LAST_SEEN            TYPE      REASON             MESSAGE
2026-06-22T21:20:50Z   Normal    Scheduled                 Successfully assigned llm-d-fast-startup/fast-startup-gpu-vllm-decode-744b8cf49-4825r to gke-snapshot-test-l4-stable-e3adc226-q6lh
2026-06-22T21:20:51Z   Normal    Pulling                   Pulling image "vllm/vllm-openai:v0.19.1"
2026-06-22T21:20:54Z   Normal    Pulled                    Successfully pulled image "vllm/vllm-openai:v0.19.1" in 2.606s (2.606s including waiting). Image size: 9612695005 bytes.
2026-06-22T21:20:54Z   Normal    Created                   Container created
2026-06-22T21:20:59Z   Normal    Started                   Container started
2026-06-22T21:21:07Z   Normal    GKEPodSnapshotting        Successfully restored the pod from PodSnapshot llm-d-fast-startup/ca5b05f0-8253-4975-adb9-c1eec9e8a71e
```

## Cleanup

Uninstall the router and delete the model server components:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/gke/
```

Delete the GKE Pod Snapshot policy and storage configurations:

```bash
kubectl delete podsnapshotpolicy pod-snapshot-policy -n ${NAMESPACE}
kubectl delete podsnapshotstorageconfig pod-snapshot-storage-config
```

Delete all created `PodSnapshot` custom resources in the namespace to release the associated storage:

```bash
kubectl delete podsnapshots --all -n ${NAMESPACE}
```

> [!NOTE]
> Deleting a `PodSnapshot` custom resource automatically triggers GKE's snapshot controller to asynchronously delete the corresponding binary snapshot files from your GCS bucket.
>
> If the deletion fails or `PodSnapshot` resources remain stuck in a `Terminating` state, it is usually because the GKE Service Agent lacks the required `roles/storage.objectUser` permission on your GCS bucket. Follow the [Grant Controller Permission guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/pod-snapshots#grant-controller-permissions) to grant the GKE Service Agent permission to delete snapshots.
