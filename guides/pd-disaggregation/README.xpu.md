# Intel XPU PD Disaggregation Deployment Guide

## Overview

This document provides complete steps for deploying Intel XPU PD (Prefill-Decode) disaggregation service on Kubernetes cluster using DeepSeek-R1-Distill-Qwen-1.5B model. PD disaggregation separates the prefill and decode phases of inference, allowing for more efficient resource utilization and improved throughput.

## Prerequisites

### Hardware Requirements

* Intel Data Center GPU Max 1550 or compatible Intel XPU device
* At least 8GB system memory
* Sufficient disk space (recommended at least 50GB available)

### Software Requirements

* Kubernetes cluster (v1.29.0+)
* Intel GPU Plugin deployed
* kubectl access with cluster-admin privileges

### Client Setup

* Create a namespace for installation.

  ```bash
  export NAMESPACE=llm-d-pd # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models.

## Step 0: Build Intel XPU Docker Image (Optional)

If you need to customize the vLLM version or build the image from source, you can build the Intel XPU Docker image:

### Build Default Image

#### Intel Data Center GPU Max 1550

```shell
# Build with the default vLLM version from docker/common-versions
make image-build DEVICE=xpu VERSION=v0.6.0
```

#### Intel Corporation Battlemage G21

```shell
# Build directly from the upstream vLLM Dockerfile.xpu
git clone https://github.com/vllm-project/vllm.git
cd vllm
git checkout v0.15.1
docker build -f docker/Dockerfile.xpu -t ghcr.io/llm-d/llm-d-xpu-dev:v0.6.0 --shm-size=4g .
```

### Available Build Arguments

The upstream vLLM Dockerfile.xpu defines the build arguments. Common ones include:

* `PYTHON_VERSION`
* `PIP_EXTRA_INDEX_URL`
* `UCX_VERSION`
* `NIXL_VERSION`

Refer to <https://github.com/vllm-project/vllm/blob/main/docker/Dockerfile.xpu> for the full list.

**⚠️ Important**:

* If you're using a pre-built image, you can skip this step and proceed directly to Step 1.
* If you build a custom image, remember to load it into your cluster (see Step 2 for Kind cluster loading instructions).
* **Repository Integration**: The llm-d-infra project has been integrated into the main llm-d repository. All previous references to separate llm-d-infra installations are now unified under the main llm-d project structure.

## Step 1: Install Tool Dependencies

```shell
# Navigate to llm-d repository (use the same repo from Step 0)
cd llm-d

# Install necessary tools (helm, helmfile, kubectl, yq, git, kind, etc.)
./helpers/client-setup/install-deps.sh
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64 && chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind

# Optional: Install development tools (including chart-testing)
./helpers/client-setup/install-deps.sh --dev
```

**Installed tools include:**

* helm (v3.12.0+)
* helmfile (v1.1.0+)
* kubectl (v1.29.0+)
* yq (v4+)
* git (v2.30.0+)

## Step 2: Create Kubernetes Cluster

If you don't have a Kubernetes cluster, you can create one using Kind:

```shell
# Use the same llm-d repository
cd llm-d

# Create Kind cluster with Kubernetes v1.34.0 for full sidecar support
kind create cluster --name llm-d-cluster --image kindest/node:v1.34.0

# Verify cluster is running
kubectl cluster-info
kubectl get nodes
```

### Load Built Image into Cluster (If using custom built image)

If you built the Intel XPU image in Step 0, load it into the Kind cluster:

```shell
# Load the built image into Kind cluster
kind load docker-image ghcr.io/llm-d/llm-d-xpu:v0.6.0 --name llm-d-cluster

# Or if you built with custom tag
kind load docker-image llm-d:custom-xpu --name llm-d-cluster

# Verify image is loaded
docker exec -it llm-d-cluster-control-plane crictl images | grep llm-d
```

**For Intel XPU deployments**: You must have the Intel GPU Plugin deployed on your cluster. See [Intel XPU Hardware-Specific Setup](../../docs/accelerators/README.md#intel-xpu) for installation instructions.

**Note**: If you already have a Kubernetes cluster (v1.29.0+) with Intel GPU Plugin deployed, you can skip this step.

## Step 3: Install Gateway API Dependencies

```shell
# Install Gateway API dependencies
cd guides/prereq/gateway-provider
./install-gateway-provider-dependencies.sh
```

## Step 4: Deploy Gateway Control Plane

```shell
# Deploy Istio Gateway control plane
cd guides/prereq/gateway-provider
helmfile apply -f istio.helmfile.yaml

# Or deploy only control plane (if CRDs already exist)
helmfile apply -f istio.helmfile.yaml --selector kind=gateway-control-plane
```

## Step 5: Deploy Intel XPU PD Disaggregation

The Intel XPU configuration in `ms-pd/values_xpu.yaml` uses a unified accelerator type that works with all Intel GPU drivers:

```yaml
# Unified configuration for all Intel GPUs
accelerator:
  type: intel
  dra: true
```

**Note:** The unified `intel` type works with both Intel Data Center GPU Max 1550 (i915 driver) and Intel BMG GPUs (Battlemage G21, xe driver). Dynamic Resource Allocation (DRA) automatically handles driver selection.

```shell
# Navigate to PD disaggregation guide directory
cd guides/pd-disaggregation

# Deploy Intel XPU PD disaggregation configuration
helmfile apply -e xpu -n ${NAMESPACE}
```

This will deploy three main components in the `llm-d-pd` namespace:

1. **infra-pd**: Gateway infrastructure for PD disaggregation
2. **gaie-pd**: Gateway API inference extension with PD-specific routing
3. **ms-pd**: Model service with separate prefill and decode deployments

### Deployment Architecture

* **Decode Service**: 1 replica with 1 Intel GPUs
* **Prefill Service**: 3 replicas with 1 Intel GPU each
* **Total GPU Usage**: 4 Intel GPUs (1 for decode + 3 for prefill)

## Step 7: Verify Deployment

### Check Helm Releases

```shell
helm list -n llm-d-pd
```

Expected output:

```text
NAME       NAMESPACE   REVISION   STATUS     CHART
gaie-pd    llm-d-pd    1          deployed   inferencepool-v1.4.0
infra-pd   llm-d-pd    1          deployed   llm-d-infra-v1.4.0
ms-pd      llm-d-pd    1          deployed   llm-d-modelservice-v0.4.9
```

### Check All Resources

```shell
kubectl get all -n llm-d-pd
```

### Monitor Pod Startup Status

```shell
# Check all PD pods status
kubectl get pods -n llm-d-pd

# Monitor decode pod startup (real-time)
kubectl get pods -n llm-d-pd -l llm-d.ai/role=decode -w

# Monitor prefill pods startup (real-time)
kubectl get pods -n llm-d-pd -l llm-d.ai/role=prefill -w
```

### View vLLM Startup Logs

#### Decode Pod Logs

```shell
# Get decode pod name
DECODE_POD=$(kubectl get pods -n llm-d-pd -l llm-d.ai/role=decode -o jsonpath='{.items[0].metadata.name}')

# View vLLM container logs
kubectl logs -n llm-d-pd ${DECODE_POD} -c vllm -f

# View recent logs
kubectl logs -n llm-d-pd ${DECODE_POD} -c vllm --tail=50
```

#### Prefill Pod Logs

```shell
# Get prefill pod names
PREFILL_PODS=($(kubectl get pods -n llm-d-pd -l llm-d.ai/role=prefill -o jsonpath='{.items[*].metadata.name}'))

# View first prefill pod logs
kubectl logs -n llm-d-pd ${PREFILL_PODS[0]} -f

# View all prefill pod logs
for pod in "${PREFILL_PODS[@]}"; do
  echo "=== Logs for $pod ==="
  kubectl logs -n llm-d-pd $pod --tail=20
  echo ""
done
```

## Step 8: Create HTTPRoute for Gateway Access

### Check if HTTPRoute was Auto-Created

First, check if the HTTPRoute was automatically created by the Chart:

```shell
# Check if HTTPRoute already exists
kubectl get httproute -n llm-d-pd
```

Note

**HTTPRoute Auto-Creation**: When using `llm-d-modelservice` Chart v0.2.9+, the HTTPRoute is typically created automatically during deployment. If you see `ms-pd-llm-d-modelservice` HTTPRoute listed, you can skip the manual creation step below.

### Manual HTTPRoute Creation (If Not Auto-Created)

If no HTTPRoute was found, create one manually:

**_IMPORTANT:_** If you used a custom `$RELEASE_NAME_POSTFIX` environment variable during deployment, you **must** update the HTTPRoute file to match your custom release names before applying it. The HTTPRoute references the Gateway and InferencePool names which include the release name postfix.

For example, if you set `RELEASE_NAME_POSTFIX=pd-xpu`, you need to update the HTTPRoute:

```shell
# Update the HTTPRoute to match your release names
sed -e "s/infra-pd-inference-gateway/infra-pd-xpu-inference-gateway/g" \
    -e "s/gaie-pd/gaie-pd-xpu/g" \
    httproute.yaml > httproute-custom.yaml

# Then apply the customized HTTPRoute
kubectl apply -f httproute-custom.yaml -n llm-d-pd
```

If using default release names (no custom `RELEASE_NAME_POSTFIX`), simply apply:

```shell
# Apply the HTTPRoute configuration from the PD disaggregation guide
kubectl apply -f httproute.yaml
```

### Verify HTTPRoute Configuration

Verify the HTTPRoute is properly configured:

```shell
# Check HTTPRoute status
kubectl get httproute -n llm-d-pd

# Check gateway attachment
kubectl get gateway infra-pd-inference-gateway -n llm-d-pd -o yaml | grep -A 5 attachedRoutes

# View HTTPRoute details
kubectl describe httproute -n llm-d-pd
```

Expected output should show:

* HTTPRoute connecting to `infra-pd-inference-gateway`
* Backend pointing to `gaie-pd` InferencePool
* Status showing `Accepted` and `ResolvedRefs` conditions

## Step 9: Test PD Disaggregation Inference Service

### Get Gateway Service Information

```shell
kubectl get service -n llm-d-pd infra-pd-inference-gateway-istio
```

### Perform Inference Requests

#### Method 1: Using Port Forwarding (Recommended)

```shell
# Port forward to local
kubectl port-forward -n llm-d-pd service/infra-pd-inference-gateway-istio 8086:80 &

# Test health check
curl -X GET "http://localhost:8086/health" -v

# Perform inference test
curl -X POST "http://localhost:8086/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
    "messages": [
      {
        "role": "user",
        "content": "Explain the benefits of prefill-decode disaggregation in LLM inference"
      }
    ],
    "max_tokens": 150,
    "temperature": 0.7
  }'
```
