# Intel XPU PD Disaggregation Deployment Guide
This document provides complete steps for deploying Intel XPU PD (Prefill-Decode) disaggregation service on Kubernetes cluster using DeepSeek-R1-Distill-Qwen-1.5B model. PD disaggregation separates the prefill and decode phases of inference, allowing for more efficient resource utilization and improved throughput.

## Prerequisites
### Hardware Requirements
* Intel Data Center GPU Max 1550 or compatible Intel XPU device
* At least 8GB system memory
* Sufficient disk space (recommended at least 50GB available)

### Software Requirements
* Kubernetes cluster (v1.28.0+)
* Intel GPU Plugin deployed
* kubectl access with cluster-admin privileges

## Step 0: Build Intel XPU Docker Image (Optional)
If you need to customize the vLLM version or build the image from source, you can build the Intel XPU Docker image:

### Clone Repository
```shell
# Clone the llm-d repository
git clone https://github.com/llm-d/llm-d
cd llm-d
```
### Build Default Image
#### Intel Data Center GPU Max 1550
```shell
# Build with default vLLM version (v0.11.0)
make image-build DEVICE=xpu VERSION=v0.2.1
```

#### Intel Corporation Battlemage G21
```shell
# Build with default vLLM version (v0.11.0)
git clone https://github.com/vllm-project/vllm.git
git checkout v0.11.0
docker build -f docker/Dockerfile.xpu -t ghcr.io/llm-d/llm-d-xpu-dev:v0.3.0 --shm-size=4g .
```

### Available Build Arguments
* `VLLM_VERSION`: vLLM version to build (default: v0.11.0)
* `PYTHON_VERSION`: Python version (default: 3.12)
* `ONEAPI_VERSION`: Intel OneAPI toolkit version (default: 2025.1.3-0)

**⚠️ Important**:

* If you're using a pre-built image, you can skip this step and proceed directly to Step 1.
* If you build a custom image, remember to load it into your cluster (see Step 2 for Kind cluster loading instructions).
* **Repository Integration**: The llm-d-infra project has been integrated into the main llm-d repository. All previous references to separate llm-d-infra installations are now unified under the main llm-d project structure.

## Step 1: Install Tool Dependencies
```shell
# Navigate to llm-d repository (use the same repo from Step 0)
cd llm-d

# Install necessary tools (helm, helmfile, kubectl, yq, git, kind, etc.)
./guides/prereq/client-setup/install-deps.sh
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64 && chmod +x ./kind && sudo mv ./kind /usr/local/bin/kind

# Optional: Install development tools (including chart-testing)
./guides/prereq/client-setup/install-deps.sh --dev
```

**Installed tools include:**

* helm (v3.12.0+)
* helmfile (v1.1.0+)
* kubectl (v1.28.0+)
* yq (v4+)
* git (v2.30.0+)

## Step 2: Create Kubernetes Cluster
If you don't have a Kubernetes cluster, you can create one using Kind:

```shell
# Use the same llm-d repository
cd llm-d

# Create Kind cluster with Intel GPU support configuration
# Note: Adjust kind configuration for Intel XPU as needed
kind create cluster --name llm-d-cluster --image kindest/node:v1.28.15

# Verify cluster is running
kubectl cluster-info
kubectl get nodes
```

### Load Built Image into Cluster (If using custom built image)
If you built the Intel XPU image in Step 0, load it into the Kind cluster:

```shell
# Load the built image into Kind cluster
kind load docker-image ghcr.io/llm-d/llm-d-xpu:v0.3.0 --name llm-d-cluster

# Or if you built with custom tag
kind load docker-image llm-d:custom-xpu --name llm-d-cluster

# Verify image is loaded
docker exec -it llm-d-cluster-control-plane crictl images | grep llm-d
```

**For Intel XPU deployments**: You must have the Intel GPU Plugin deployed on your cluster. The plugin provides the `gpu.intel.com/i915` resource that the Intel XPU workloads require.

To deploy the Intel GPU Plugin:

```shell
kubectl apply -k 'https://github.com/intel/intel-device-plugins-for-kubernetes/deployments/gpu_plugin?ref=v0.32.1'
```

**Note**: If you already have a Kubernetes cluster (v1.28.0+) with Intel GPU Plugin deployed, you can skip this step.

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


## Step 5: Create HuggingFace Token Secret
```shell
# Set environment variables
export NAMESPACE=llm-d-pd
export RELEASE_NAME_POSTFIX=pd
export HF_TOKEN_NAME=${HF_TOKEN_NAME:-llm-d-hf-token}
export HF_TOKEN=$your-hf-token

# Create namespace
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -

# Create HuggingFace token secret (empty token for public models)
kubectl create secret generic $HF_TOKEN_NAME --from-literal="HF_TOKEN=${HF_TOKEN}" --namespace ${NAMESPACE}
```


## Step 6: Deploy Intel XPU PD Disaggregation
⚠️ **Important - For Intel BMG GPU Users**: Before running `helmfile apply`, you must update the GPU resource type in `ms-pd/values_xpu.yaml`:

```yaml
# Edit ms-pd/values_xpu.yaml
accelerator:
  type: intel
  resources:
    intel: "gpu.intel.com/xe"  # Add gpu.intel.com/xe

# Also update decode and prefill resource specifications:
decode:
  containers:
  - name: "vllm"
    resources:
      limits:
        gpu.intel.com/xe: 1  # Change from gpu.intel.com/i915 to gpu.intel.com/xe
      requests:
        gpu.intel.com/xe: 1  # Change from gpu.intel.com/i915 to gpu.intel.com/xe

prefill:
  containers:
  - name: "vllm"
    resources:
      limits:
        gpu.intel.com/xe: 1  # Change from gpu.intel.com/i915 to gpu.intel.com/xe
      requests:
        gpu.intel.com/xe: 1  # Change from gpu.intel.com/i915 to gpu.intel.com/xe
```

  
**Resource Requirements by GPU Type:**

* **Intel Data Center GPU Max 1550**: Use `gpu.intel.com/i915`
* **Intel BMG GPU (Battlemage G21)**: Use `gpu.intel.com/xe`

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

```
NAME       NAMESPACE   REVISION   STATUS     CHART                     
gaie-pd    llm-d-pd    1          deployed   inferencepool-v0.5.1      
infra-pd   llm-d-pd    1          deployed   llm-d-infra-v1.3.0        
ms-pd      llm-d-pd    1          deployed   llm-d-modelservice-v0.2.11 
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

