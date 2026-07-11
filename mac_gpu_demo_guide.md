# Recreating the GPU-Accelerated llm-d Demo on Apple Silicon

This guide explains how to recreate the **llm-d quickstart demo** with local GPU acceleration on a **macOS Apple Silicon (M-series)** device. 

Since the standard `llm-d` GPU manifests target NVIDIA CUDA (which is incompatible with macOS hardware), this setup uses the experimental `krunkit` Minikube driver to virtualize your Mac's Metal GPU and routes the serving stack to a Vulkan-capable `llama-server`.

---

## 1. Prerequisites (macOS Host)

Make sure the required hypervisor driver and networking dependencies are installed on your Mac:

```bash
# 1. Install krunkit hypervisor driver
brew tap slp/krunkit
brew install krunkit

# 2. Install vmnet-helper for virtual bridging (requires sudo password)
curl -fsSL https://github.com/minikube-machine/vmnet-helper/releases/latest/download/install.sh | bash
```

---

## 2. Manifest Configurations

The following files in your repository are pre-configured to run the GPU demo:

### A. GPU device plugin
The DaemonSet at [generic-device-plugin.yaml](file:///Users/rc/llmd/llm-d/guides/optimized-baseline/modelserver/cpu/vllm/generic-device-plugin.yaml) maps the virtualized `/dev/dri` GPU passed by `krunkit` and exposes it inside Kubernetes as `devic.es/dri`:
```yaml
# guides/optimized-baseline/modelserver/cpu/vllm/generic-device-plugin.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: generic-device-plugin
  namespace: kube-system
  # ... (detects /dev/dri and registers as devic.es/dri)
```

### B. Model Server Patch
The patch at [patch-vllm.yaml](file:///Users/rc/llmd/llm-d/guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml) has been altered to substitute vLLM with `llama-server` (ramalama) offloading to the GPU:
```yaml
# guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: decode
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: modelserver
          image: quay.io/ramalama/ramalama:latest
          command: ["llama-server"]
          args:
            - "--host"
            - "0.0.0.0"
            - "--port"
            - "8000"
            - "--hf-repo"
            - "Qwen/Qwen2.5-0.5B-Instruct-GGUF"
            - "--hf-file"
            - "qwen2.5-0.5b-instruct-q4_k_m.gguf"
            - "--alias"
            - "Qwen3-32B"
            - "-ngl"
            - "999"
            # ...
          resources:
            limits:
              devic.es/dri: "1"
```

---

## 3. Step-by-Step Execution Guide

Follow these commands in sequence to stand up and verify the demo:

### Step 1: Start Minikube with `krunkit`
```bash
minikube start --driver krunkit --mount-string ~/models:/mnt/models --cpus 4 --memory 6144
```

### Step 2: Initialize Namespace, Secrets & CRDs
```bash
# Create namespace
kubectl create namespace llm-d-quickstart --dry-run=client -o yaml | kubectl apply -f -

# Create mock Hugging Face token secret
kubectl create secret generic llm-d-hf-token \
  --from-literal=HF_TOKEN=mock-token \
  -n llm-d-quickstart --dry-run=client -o yaml | kubectl apply -f -

# Apply Gateway API Inference CRDs
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.5.0/v1-manifests.yaml
```

### Step 3: Apply the GPU Device Plugin
```bash
kubectl apply -f guides/optimized-baseline/modelserver/cpu/vllm/generic-device-plugin.yaml
```

### Step 4: Deploy the EPP Router
Deploy the Standalone Router Helm chart:
```bash
helm install quickstart oci://ghcr.io/llm-d/charts/llm-d-router-standalone \
    -f guides/recipes/router/base.values.yaml \
    -f guides/optimized-baseline/router/optimized-baseline.values.yaml \
    -n llm-d-quickstart --version v0.9.0
```

### Step 5: Deploy the GPU Model Server
Apply the model server deployment containing the `llama-server` patch:
```bash
kubectl apply -n llm-d-quickstart -k guides/optimized-baseline/modelserver/gpu/vllm/base/
```

---

## 4. Verification

1. Wait for the pods to show `Running` and `Ready` status:
   ```bash
   kubectl get pods -n llm-d-quickstart -w
   ```
2. Port-forward the EPP routing gateway to your local machine:
   ```bash
   kubectl port-forward -n llm-d-quickstart service/quickstart-epp 8001:80
   ```
3. Open a new terminal on your host Mac and send a test request:
   ```bash
   curl -X POST http://localhost:8001/v1/completions \
       -H 'Content-Type: application/json' \
       -d '{"model": "Qwen3-32B", "prompt": "How are you today?"}'
   ```
