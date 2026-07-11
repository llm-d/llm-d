# Step-by-Step Plan: Deploying llm-d GPU Demo on AWS EC2 (Cost-Optimized)

This document provides a complete guide to running the **llm-d quickstart GPU demo** on a highly cost-effective **single-GPU** AWS EC2 instance. By running a lightweight model and sharing the single GPU across multiple replicas, you can demonstrate `llm-d`'s load-balancing and prefix-caching benefits for **less than $1 total cost**.

---

## Step 1: Launch an AWS EC2 Single-GPU Instance

1. **Instance Type**: Select **`g6.xlarge`** (contains 1x NVIDIA L4 GPU with 24GB VRAM) or **`g5.xlarge`** (contains 1x NVIDIA A10G GPU with 24GB VRAM).
   * **On-Demand Price**: ~$0.80 / hour (g6.xlarge)
   * **Spot Instance Price**: **~$0.24 / hour** (g6.xlarge)
2. **AMI (Amazon Machine Image)**: Select **Deep Learning AMI GPU PyTorch (Ubuntu 22.04)**.
3. **Storage**: Configure at least **100 GB** of gp3 SSD storage.

---

## Step 2: Install Kubernetes (Minikube) on the Host

Once connected to your EC2 instance via SSH, install `kubectl` and `minikube`:

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

To run **2 replicas sharing the single GPU** on your `g6.xlarge` instance, we will configure them to share the VRAM and remove explicit GPU scheduling limits (so the scheduler doesn't lock the GPU to a single pod).

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

## Step 6: Verify and Demonstrate performance benefits

Wait for both EPP and vLLM pods to become healthy:
```bash
kubectl get pods -n llm-d-quickstart -w
```

### 1. Set Up Port-Forwarding
```bash
kubectl port-forward -n llm-d-quickstart service/quickstart-epp 8001:80 &
```

### 2. Demonstrate Prefix Caching (Affinity Routing)
`llm-d` automatically routes requests with matching prompts or prefixes to the same replica where the key-value cache is pre-warmed. 

1. Send a request with a long system prompt/prefix to warm up the cache on one replica:
   ```bash
   curl -X POST http://localhost:8001/v1/completions \
       -H 'Content-Type: application/json' \
       -d '{"model": "Qwen3-32B", "prompt": "System: You are an expert code developer. Answer all queries in Go. User: How do you format a string?"}'
   ```
2. Send another query with the **same system prompt/prefix**. `llm-d`'s `prefix-cache-scorer` will automatically detect the cached prefix and route the request to the exact same GPU replica. You will see a significantly faster Time-To-First-Token (TTFT) because it skips context processing!

### 3. Demonstrate Load-Aware Load Balancing
`llm-d`'s `queue-scorer` distributes concurrent requests to the replica with the shortest active processing queue.

1. Send multiple concurrent heavy generation requests at the same time using a benchmarking tool (like `ab` or `wrk`):
   ```bash
   # Send 10 concurrent requests to the proxy
   ab -n 20 -c 4 -p post.json -T "application/json" http://localhost:8001/v1/completions
   ```
2. Observe EPP logs (`kubectl logs -n llm-d-quickstart -l app.kubernetes.io/name=llm-d-router-endpoint-picker`). You will see EPP picking separate replicas dynamically depending on their active queue sizes, avoiding load hotspots and keeping tail latency low.

---

## Step 7: Teardown & Cost Cleanup (Critical)

To prevent ongoing charges on AWS, follow these steps to clean up all resources:

### 1. Delete Kubernetes Resources (Optional)
If you want to clear your deployments inside the cluster first:
```bash
helm uninstall quickstart -n llm-d-quickstart
kubectl delete namespace llm-d-quickstart
minikube delete
```

### 2. Terminate the EC2 Instance (Required)
Go to your AWS Console or run the AWS CLI command to terminate the instance:
```bash
# Get the instance ID and terminate it
aws ec2 terminate-instances --instance-ids <your-instance-id>
```

### 3. Verify EBS Volume Deletion
* By default, when you launch an instance, the root EBS volume is configured to **Delete on Termination**.
* To double check, go to **EC2 Dashboard -> Volumes** and verify that no volumes associated with the demo are left in the `available` state. (Volumes left in `available` state continue to charge you for storage!).

### 4. Release Elastic IPs (If allocated)
* If you allocated an **Elastic IP** to access the instance, release it. (AWS charges for allocated Elastic IPs that are not attached to a running instance).
