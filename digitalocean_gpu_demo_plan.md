# Step-by-Step Plan: Deploying llm-d GPU Demo on DigitalOcean

This document provides a complete guide to running the **llm-d quickstart GPU demo** on a highly cost-effective **NVIDIA RTX 4000 Ada Generation** GPU Droplet on DigitalOcean. By sharing the single GPU across multiple replicas, you can run this demo with full hardware acceleration for **less than $1 total cost**.

---

## Step 1: Provision a DigitalOcean GPU Droplet

1. **GPU Model**: Select the **NVIDIA RTX 4000 Ada Generation** (20GB VRAM, 8 vCPUs, 32GB RAM, 500GB NVMe).
   * **Hourly Rate**: **$0.76 / hour** (billed per second)
2. **OS Image**: Select the **Inference-optimized image** or standard **Ubuntu 22.04 LTS**.
   * *Tip*: Using DigitalOcean's inference image ensures that NVIDIA drivers and the Docker GPU runtime are pre-configured.
3. **Region**: Select any region where GPU Droplets are available (e.g., `AMS3`, `TOR1`, `NYC2`).

---

## Step 2: Install Kubernetes (Minikube) on the Droplet

Once connected to your Droplet via SSH, install `kubectl` and `minikube`:

```bash
# 1. Install kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# 2. Install Minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube

# 3. Start Minikube with NVIDIA GPU Passthrough
minikube start --driver=docker --gpus=all --cpus=max --memory=max
```

> [!NOTE]
> Passing `--gpus=all` instructs Minikube's Docker runtime to share the host's physical RTX 4000 GPU directly with the containers.

---

## Step 3: Set Up Gateway API & Secrets

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

## Step 4: Adjust Manifests for 2 Replicas on 1 GPU

By default, the baseline GPU patch file `guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml` is configured for 4 replicas requesting 2 GPUs each (total 8). 

Since the RTX 4000 has **20 GB of VRAM**, we can run **2 replicas** sharing this single GPU using a lightweight model like `Qwen/Qwen2.5-3B-Instruct` (which takes ~6GB VRAM per replica). To do this, we configure them to share the VRAM and remove explicit GPU scheduling limits (so the scheduler doesn't lock the GPU to a single pod).

In `guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml`:
* Change `replicas: 4` to **`replicas: 2`**.
* In `resources.limits` and `resources.requests`:
  * **Remove** `nvidia.com/gpu: 2` entirely.
  * Adjust memory limits to `limits: memory: 8Gi` and `requests: memory: 6Gi`.
  * Adjust CPU limits to `limits: cpu: "4"` and `requests: cpu: "2"`.
* In the `args` section:
  * Remove `"--tensor-parallel-size=2"`.
  * Change the model name from `Qwen/Qwen3-32B` to a lightweight model: **`Qwen/Qwen2.5-3B-Instruct`** or **`Qwen/Qwen2.5-1.5B-Instruct`**.
  * Ensure the model alias matches what the router is expecting: `--alias Qwen3-32B`.

---

## Step 5: Deploy the llm-d Stack

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

## Step 6: Verify and Run Inference

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

## Step 7: Teardown & Cost Cleanup (CRITICAL)

Unlike standard Droplets, **DigitalOcean GPU Droplets incur charges even when they are powered off**, because the GPU slot remains reserved for your account on the physical hypervisor. 

To stop billing completely:
1. Delete your Droplet via the DigitalOcean Cloud Console, or using the `doctl` CLI:
   ```bash
   # Delete the Droplet completely
   doctl compute droplet delete <droplet-id> --force
   ```
2. Verify in the dashboard under **Droplets** that the instance is deleted and no longer visible.
