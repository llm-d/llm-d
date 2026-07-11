# Step-by-Step Plan: Deploying llm-d GPU Demo on RunPod

This document provides a complete guide to running the **llm-d quickstart GPU demo** on a highly cost-effective **NVIDIA RTX 3090** or **RTX 4090** GPU instance on RunPod. 

Because RunPod instances run as containers on a host node, we will use **K3s** (a lightweight, highly robust Kubernetes distribution) instead of Minikube. K3s runs flawlessly in containerized environments and automatically integrates with the pre-installed NVIDIA runtime.

---

## Step 1: Launch a RunPod GPU Instance

1. Go to the **RunPod Console** and select **GPU Pods**.
2. **GPU Selection**: Select a single **RTX 3090** (24GB VRAM) or **RTX 4090** (24GB VRAM) in the Secure or Community Cloud.
   * **Rate**: ~$0.30 - $0.45 / hour
3. **Template**: Select the **`RunPod PyTorch`** template (comes with CUDA 12.1+ and NVIDIA drivers preloaded).
4. **Volume Size**: Increase the Container Disk to **100 GB** to handle model downloads.
5. Click **Deploy**. Once running, copy the SSH command (e.g., `ssh -p 12345 root@xx.xx.xx.xx`) and connect to the instance from your local terminal.

---

## Step 2: Install Kubernetes (K3s) on the Pod

Once connected via SSH as `root`, install K3s. K3s will automatically detect the NVIDIA container runtime:

```bash
# 1. Install K3s (lightweight Kubernetes)
curl -sfL https://get.k3s.io | sh -

# 2. Verify Kubernetes is running and kubectl is configured
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl get nodes
```

---

## Step 3: Install Helm

```bash
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
chmod 700 get_helm.sh
./get_helm.sh
```

---

## Step 4: Set Up Gateway API & Secrets

Apply the Gateway API CRDs and configure the namespace:

```bash
# 1. Apply GAIE CRDs
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.5.0/v1-manifests.yaml

# 2. Create Target Namespace
kubectl create namespace llm-d-quickstart --dry-run=client -o yaml | kubectl apply -f -

# 3. Create HuggingFace Secret
# Replace <your_token> with a valid HuggingFace Token
export HF_TOKEN="<your_token>"
kubectl create secret generic llm-d-hf-token \
  --from-literal=HF_TOKEN="${HF_TOKEN}" \
  -n llm-d-quickstart --dry-run=client -o yaml | kubectl apply -f -
```

---

## Step 5: Adjust Manifests for 2 Replicas on 1 GPU

By default, the baseline GPU patch file `guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml` is configured for 4 replicas requesting 2 GPUs each (total 8). 

Since our RunPod instance has **1 GPU** (24GB VRAM), we will configure **2 replicas** sharing this single GPU using a lightweight model like `Qwen/Qwen2.5-3B-Instruct` (which takes ~6GB VRAM per replica) to avoid OOM crashes.

In `guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml`:
* Change `replicas: 4` to **`replicas: 2`**.
* In `resources.limits` and `resources.requests`:
  * **Remove** `nvidia.com/gpu: 2` entirely so the Kubernetes scheduler doesn't lock the GPU, allowing the replicas to run concurrently.
  * Adjust memory limits to `limits: memory: 8Gi` and `requests: memory: 6Gi`.
  * Adjust CPU limits to `limits: cpu: "4"` and `requests: cpu: "2"`.
* In the `args` section:
  * Remove `"--tensor-parallel-size=2"`.
  * Change the model name from `Qwen/Qwen3-32B` to a lightweight model: **`Qwen/Qwen2.5-3B-Instruct`** or **`Qwen/Qwen2.5-1.5B-Instruct`**.
  * Ensure the model alias matches what the router is expecting: `--alias Qwen3-32B`.

---

## Step 6: Deploy the llm-d Stack

### A. Deploy EPP Router (via Helm)
```bash
helm install quickstart oci://ghcr.io/llm-d/charts/llm-d-router-standalone \
    -f guides/recipes/router/base.values.yaml \
    -f guides/optimized-baseline/router/optimized-baseline.values.yaml \
    -n llm-d-quickstart --version v0.9.0
```

### B. Deploy GPU Model Server (via Kustomize)
```bash
kubectl apply -n llm-d-quickstart -k guides/optimized-baseline/modelserver/gpu/vllm/base/
```

---

## Step 7: Verify and Run Inference

1. Watch the pod status until both EPP and vLLM are `Running` and `Ready`:
   ```bash
   kubectl get pods -n llm-d-quickstart -w
   ```
2. Port-forward the service:
   ```bash
   kubectl port-forward -n llm-d-quickstart service/quickstart-epp 8001:80 &
   ```
3. Test completion generation:
   ```bash
   curl -X POST http://localhost:8001/v1/completions \
       -H 'Content-Type: application/json' \
       -d '{"model": "Qwen3-32B", "prompt": "How are you today?"}'
   ```

---

## Step 8: Teardown & Cost Cleanup (CRITICAL)

Unlike standard cloud VMs, **RunPod instances incur storage/volume charges even when stopped**. To avoid ongoing billing:
1. In your RunPod dashboard, find your pod under **My Pods**.
2. Click the **Delete** (Trash can) icon to completely destroy the pod.
3. Confirm that the pod is no longer listed to stop all compute and storage charges.
