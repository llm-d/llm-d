# Step-by-Step Plan: Deploying llm-d GPU Demo on Google Cloud (GCP)

This document provides a complete guide to running the **llm-d quickstart GPU demo** on a cost-effective **single-GPU** Google Compute Engine (GCE) VM instance. By sharing a single GPU across multiple replicas, you can demonstrate the load-balancing and prefix-caching benefits of `llm-d` on GCP.

---

## Step 1: Launch a GCP GPU GCE VM Instance

1. **Instance Type / Machine Family**: Select **`g2-standard-4`** (includes 1x NVIDIA L4 GPU with 24GB VRAM, 4 vCPUs, and 16GB memory).
   * **Estimated Price**: ~$0.85 / hour (On-Demand) or **~$0.30 / hour (Preemptible/Spot)**.
2. **OS Image (Boot Disk)**: Choose a **Deep Learning VM Image** from the Google Cloud Marketplace (specifically the **Debian-based DLVM with CUDA pre-installed**, or a standard Ubuntu image where you install CUDA manually). GCE offers images like `common-cu-12-1` in the `ml-images` family.
3. **Storage**: Configure a **100 GB** Balanced Persistent Disk.
4. **Firewall / Networking**: Allow HTTP/HTTPS traffic if you plan to expose the gateway externally, otherwise SSH-only is sufficient.

---

## Step 2: Install Kubernetes (Minikube) on the GCE Host

Once connected to your VM instance via SSH, install `kubectl` and `minikube`:

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
> Passing `--gpus=all` exposes GCE's physical NVIDIA L4 GPU directly to the containers in Kubernetes.

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

To run **2 replicas sharing the single GPU** on your `g2-standard-4` instance, we will configure them to share the VRAM and remove explicit GPU scheduling limits (so the scheduler doesn't lock the GPU to a single pod).

In `guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml`:
* Change `replicas: 4` to **`replicas: 2`**.
* In `resources.limits` and `resources.requests`:
  * **Remove** `nvidia.com/gpu: 2` entirely.
  * Adjust memory limits to `limits: memory: 8Gi` and `requests: memory: 6Gi`.
  * Adjust CPU limits to `limits: cpu: "4"` and `requests: cpu: "2"`.
* In the `args` section:
  * Remove `"--tensor-parallel-size=2"`.
  * Change the model name from `Qwen/Qwen3-32B` to a lightweight model: **`Qwen/Qwen2.5-3B-Instruct`** (which takes ~6GB VRAM per replica) or **`Qwen/Qwen2.5-1.5B-Instruct`** (~3.5GB VRAM per replica).
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

## Step 7: Teardown & Cost Cleanup (Critical)

To prevent ongoing charges on GCP, follow these steps to clean up all resources:

### 1. Delete Kubernetes Resources (Optional)
If you want to clear your deployments inside the cluster first:
```bash
helm uninstall quickstart -n llm-d-quickstart
kubectl delete namespace llm-d-quickstart
minikube delete
```

### 2. Delete the GCE VM Instance (Required)
You can delete the GCE instance from your Google Cloud Console or via the `gcloud` CLI:
```bash
# Delete the VM instance (replace <instance-name> and <zone>)
gcloud compute instances delete <instance-name> --zone=<zone> --delete-disks=all
```
> [!IMPORTANT]
> The `--delete-disks=all` flag ensures that the attached boot Persistent Disk is also deleted, preventing background storage costs.

### 3. Verify Static External IPs
If you allocated a static external IP address for the VM, make sure to release it if the VM is deleted, as Google Cloud charges for unattached IP addresses.
```bash
gcloud compute addresses delete <ip-name> --region=<region>
```
