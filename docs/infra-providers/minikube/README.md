# llm-d on minikube

## Prerequisites

### Platform Setup

Minikube is a convenient option for rapid testing of llm-d capabilities. However, it has more resource constraints than production clusters and is best suited for development and experimentation.

**Requirements:**
- [Minikube installed](https://minikube.sigs.k8s.io/docs/start/) on your local machine
- A container runtime (Docker or Podman)
- An accelerator supported by llm-d (for example, NVIDIA GPU, AMD GPU, Google TPU, or Intel XPU) that is visible to your container runtime
- The appropriate container toolkit or drivers for your accelerator (for example, NVIDIA Container Toolkit, ROCm, or vendor tooling)

The following quickstart is an example setup using Docker and NVIDIA Container Toolkit, on a 2-GPU single node cluster.

### Quick Start

Set up a minikube cluster. This example uses Docker and NVIDIA Container Toolkit.

```bash
# Delete any existing minikube cluster
minikube delete

# Configure the NVIDIA Container Toolkit (if not already configured)
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker

# Start minikube with GPU support
minikube start --driver=docker --container-runtime=docker --gpus all
```

### Install llm-d Prerequisites

These steps install the prerequisite components needed for llm-d inference workloads.

#### 1. Install Client Dependencies and Set Up Credentials
For more about dependencies, see the [client-setup readme](../../../guides/prereq/client-setup/README.md).

```bash
cd llm-d/guides/prereq/client-setup
./install-deps.sh

# Set up Hugging Face token for model access
export HF_TOKEN=<your-token-here>
export HF_TOKEN_NAME=${HF_TOKEN_NAME:-llm-d-hf-token}
export NAMESPACE=${NAMESPACE:-llm-d}

kubectl create namespace ${NAMESPACE}
kubectl create secret generic ${HF_TOKEN_NAME} \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
```

#### 2. Install Monitoring Stack (Prometheus & Grafana)
For more about the monitoring stack, see the [monitoring readme](../../monitoring/README.md).

```bash
cd llm-d/docs/monitoring/scripts
./install-prometheus-grafana.sh
```

#### 3. Install Gateway Provider (Istio)
For more about gateway providers, see the [gateway-provider readme](../../../guides/prereq/gateway-provider/README.md).

```bash
cd llm-d/guides/prereq/gateway-provider/
./install-gateway-provider-dependencies.sh

# Apply the Helmfile for the gateway provider you prefer. To use Istio:
helmfile apply -f istio.helmfile.yaml
```

#### 4. Deploy a Model and Test Inference

After completing the foundation setup above, continue to the [guides](../../../guides) to select a well-lit path and test out inference.

The [Intelligent Inference Scheduler guide](../../../guides/inference-scheduling/README.md) is validated on minikube and demonstrates intelligent request routing across multiple model server replicas. Remember to adjust resource requirements to fit your hardware and software stack.

## Minikube Considerations

### Resource Constraints

Minikube runs on a single node, which limits available resources compared to production clusters. When following llm-d guides:

- **Reduce replica counts**: Use 1-2 replicas instead of higher counts in example configurations
- **Use smaller models**: Models like Qwen/Qwen3-0.6B are recommended
- **Adjust resource requests**: Match hardware requirements and memory requests to your available hardware
  - Example: If using 2 GPUs, set `replicas: 2` with `nvidia.com/gpu: "1"` per replica

When running examples, ensure you have adjusted values.yaml files where appropriate. For testing advanced features, use a cloud provider (GKE, EKS, AKS) or on-premises cluster with appropriate hardware.

