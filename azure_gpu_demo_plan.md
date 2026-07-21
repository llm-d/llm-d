# Step-by-Step Plan: Deploying llm-d GPU Demo on Microsoft Azure

This document provides a complete guide to running the **llm-d quickstart GPU demo** on Microsoft Azure. By using a cost-effective single-GPU VM and sharing its VRAM across multiple replicas, you can run this demo with full hardware acceleration for **less than $1 total cost**.

---

## Step 1: Launch an Azure GPU VM Instance

1. **Instance Type / Size Selection**:
   * **`Standard_NC4as_T4_v3`** (Cheapest - Recommended)
     * **GPU**: 1x NVIDIA Tesla T4 (16GB VRAM)
     * **On-Demand Price**: ~$0.53 / hour
     * **Spot Price**: **~$0.15 / hour**
   * **`Standard_NV36ads_A10_v5`** (Alternative)
     * **GPU**: 1x NVIDIA A10 (24GB VRAM)
     * **On-Demand Price**: ~$1.20 / hour
2. **OS Image**: Select **Ubuntu Server 22.04 LTS** or the **Azure Deep Learning Virtual Machine (DLVM)** which comes pre-installed with NVIDIA drivers and CUDA.
3. **Storage**: Configure a **128 GB Premium SSD (LRS)** OS disk.
4. **Networking**: Ensure port `22` is open for SSH connection.

---

## Step 2: Install Kubernetes (Minikube) on the Host

Once connected to your Azure VM via SSH, install `kubectl` and `minikube`:

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
> Passing `--gpus=all` instructs Minikube's Docker runtime to share the host's physical NVIDIA GPU directly with the containers.

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

If using the **16GB VRAM T4** instance, we will configure **2 replicas** sharing this single GPU using a very light model like `Qwen/Qwen2.5-1.5B-Instruct` (which takes ~3.5GB VRAM per replica) to avoid OOM crashes. We will remove the explicit GPU limit checks so Kubernetes schedules both replicas on the single GPU.

In `guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml`:
* Change `replicas: 4` to **`replicas: 2`**.
* In `resources.limits` and `resources.requests`:
  * **Remove** `nvidia.com/gpu: 2` entirely.
  * Adjust memory limits to `limits: memory: 6Gi` and `requests: memory: 4Gi`.
  * Adjust CPU limits to `limits: cpu: "3"` and `requests: cpu: "1.5"`.
* In the `args` section:
  * Remove `"--tensor-parallel-size=2"`.
  * Change the model name from `Qwen/Qwen3-32B` to a lightweight model: **`Qwen/Qwen2.5-1.5B-Instruct`** (ideal for T4) or **`Qwen/Qwen2.5-3B-Instruct`** (ideal for A10).
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

To avoid recurring billing charges in Azure:
1. **Delete the VM**: In the Azure Portal, locate your VM and click **Delete**.
2. **Delete Associated Resources**: Ensure you check the boxes to delete:
   * **OS Disk** & **Data Disks** (EBS equivalent)
   * **Network Interfaces (NICs)**
   * **Public IP Addresses** (Azure charges for unassociated public IPs)
3. Alternatively, you can delete the entire **Resource Group** containing the VM to destroy all associated assets in a single step.
   ```bash
   az group delete --name <your-resource-group> --yes --no-wait
   ```
