# KubeCon Tutorial — AI Agent Context

> This file provides context for AI coding agents (Claude Code, Cursor, Copilot,
> Windsurf, etc.) to help users work through the KubeCon hands-on tutorial.
> If you're an AI agent, read this file and the tutorial, then guide the user
> step by step.

## What this tutorial is

**"KV-Cache Wins You Can Feel: Building AI-Aware LLM Routing on Kubernetes"**
— a 75-minute hands-on KubeCon session.

Users deploy llm-d, a Kubernetes-native inference serving stack, on a local kind
cluster using a vLLM simulator (no GPUs needed). They see the difference between
cache-blind round-robin routing and llm-d's prefix-cache-aware scheduling.

The full tutorial is at: `tutorial/kubecon-kv-cache-tutorial.md`

## How to help

Walk the user through each step. Run commands, verify they succeeded, explain
what happened. If something fails, diagnose and fix — don't just surface the
error. The user may have no Kubernetes background.

## Architecture

```
Client → Gateway (Envoy) → EPP (Inference Scheduler) → vLLM Simulator Pods

Without llm-d: Client → k8s Service → round-robin to any pod (cache-blind)
With    llm-d: Client → Gateway → EPP picks best pod based on prefix affinity
```

## Tutorial flow

### Part 0: Setup (15 min) — runs in parallel with instructor presentation

```bash
# 1. Clone and create kind cluster
git clone https://github.com/llm-d/llm-d.git && cd llm-d

cat <<'EOF' > kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
  - role: worker
EOF
kind create cluster --name llm-d-tutorial --config kind-config.yaml

# 2. Pull images in background
docker pull ghcr.io/llm-d/llm-d-inference-sim:v0.7.1 &
docker pull ghcr.io/llm-d/llm-d-routing-sidecar:v0.6.0 &
docker pull ghcr.io/llm-d/llm-d-inference-scheduler:v0.6.0 &
docker pull cr.kgateway.dev/kgateway-dev/envoy-wrapper:v2.1.1 &
# Optional (~3 GB): docker pull ghcr.io/llm-d/llm-d-benchmark:v0.5.0 &
wait

# 3. Load into kind
kind load docker-image ghcr.io/llm-d/llm-d-inference-sim:v0.7.1 --name llm-d-tutorial
kind load docker-image ghcr.io/llm-d/llm-d-routing-sidecar:v0.6.0 --name llm-d-tutorial
kind load docker-image ghcr.io/llm-d/llm-d-inference-scheduler:v0.6.0 --name llm-d-tutorial
kind load docker-image cr.kgateway.dev/kgateway-dev/envoy-wrapper:v2.1.1 --name llm-d-tutorial

# 4. Install monitoring
./docs/monitoring/scripts/install-prometheus-grafana.sh

# 5. Create namespace
export NAMESPACE=llm-d-tutorial
kubectl create namespace ${NAMESPACE}
```

Verify: `kubectl get nodes` (4 nodes, all Ready), `kubectl get pods -n llm-d-monitoring` (5+ pods).

### Part 1: Baseline (20 min) — deploy vLLM behind plain k8s Service

Deploy 8 simulator pods with a plain Service. The simulator needs `args: ["--model", "random"]`
or it crashes with "model parameter is empty."

After deploying, open Grafana (`kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80 &`)
and Prometheus (`kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090 &`).

Send traffic using one of two options:
- **Option A (full benchmark)**: Requires the ~3 GB benchmark image, an HF token, and a PVC. Runs at 35 req/s for 120s.
- **Option B (lightweight script)**: `./tutorial/generate-shared-prefix-traffic.sh -e http://localhost:8000 -m random -d 120 -r 5 -p 3`

Check which is available: `docker images ghcr.io/llm-d/llm-d-benchmark:v0.5.0 -q | grep -q . && echo "A" || echo "B"`

Observe: requests distributed evenly across all 8 pods (round-robin).

### Part 2: llm-d (30 min) — deploy inference scheduler, compare

```bash
# Install Gateway API CRDs
./guides/prereq/gateway-provider/install-gateway-provider-dependencies.sh

# Install kgateway
cd guides/prereq/gateway-provider
helmfile apply -f kgateway.helmfile.yaml
cd ../../..

# Delete baseline
kubectl delete deployment vllm-baseline -n ${NAMESPACE}
kubectl delete service vllm-baseline -n ${NAMESPACE}

# Deploy llm-d stack
cd guides/simulated-accelerators
helmfile apply -e kgateway -n ${NAMESPACE}
kubectl scale deployment ms-sim-llm-d-modelservice-decode -n ${NAMESPACE} --replicas=8

# Kind-specific fixes (not needed on real clusters)
kubectl patch deployment infra-sim-inference-gateway -n ${NAMESPACE} --type=json -p='[
  {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"tmp","emptyDir":{}}},
  {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":{"name":"tmp","mountPath":"/tmp"}}
]'
kubectl patch deployment gaie-sim-epp -n ${NAMESPACE} --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]'

kubectl apply -f httproute.yaml -n ${NAMESPACE}
cd ../..

# Discover gateway service
export GATEWAY_SVC=$(kubectl get svc -n "${NAMESPACE}" -o yaml | \
  yq '.items[] | select(.metadata.name | test(".*-inference-gateway(-.*)?$")).metadata.name' | head -n1)
```

Run the same traffic (Option A or B) through the gateway. Observe: requests are
now concentrated — prefix-aware routing sends shared-prefix traffic to the same pods.

### Part 3: Wrap-up (10 min)

Show production numbers, cleanup, next steps. See the tutorial for details.

## Key facts

- **Namespace**: `llm-d-tutorial`
- **Model name**: `random` (what the simulator serves)
- **Helmfile environment**: `-e kgateway` (for kind deployments)
- **Decode deployment name**: `ms-sim-llm-d-modelservice-decode`
- **Gateway service name**: `infra-sim-inference-gateway`
- **Grafana login**: `admin` / `admin` on `http://localhost:3000`
- **Prometheus**: `http://localhost:9090`
- **Benchmark template**: `tutorial/benchmark-template.yaml` — uses `sed` for variable substitution (not `envsubst`)
- **Traffic script**: `tutorial/generate-shared-prefix-traffic.sh` — bash 3.2 compatible, no dependencies beyond curl

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Simulator crashes: "model parameter is empty" | Missing `--model` arg | Add `args: ["--model", "random"]` to container spec |
| `ImagePullBackOff` | Image not loaded into kind | `kind load docker-image <image> --name llm-d-tutorial` |
| Gateway not programmed | CRDs missing | `./guides/prereq/gateway-provider/install-gateway-provider-dependencies.sh` |
| No metrics in Grafana | Namespace not labeled | `kubectl label namespace llm-d-tutorial monitoring-ns=llm-d-monitoring` |
| Port-forward drops | They're fragile | Just restart it |
| Wrong kubectl context | Context switched | `kubectl config use-context kind-llm-d-tutorial` |
| Gateway pod crash: "Failed to create temporary file" | kgateway runs with `readOnlyRootFilesystem` | Patch: `kubectl patch deployment infra-sim-inference-gateway -n $NS --type=json -p='[{"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"tmp","emptyDir":{}}},{"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":{"name":"tmp","mountPath":"/tmp"}}]'` |
| EPP in ImagePullBackOff | `imagePullPolicy: Always` but registry unreachable | Patch: `kubectl patch deployment gaie-sim-epp -n $NS --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]'` |
| Helm v4 breaks helmfile | `--client` flag removed in Helm v4 | Use Helm v3.x (v3.12-v3.17). Helm v4 is not yet supported by helmfile/helm-diff |

## PromQL queries for comparison

```promql
# Per-pod request distribution (the main before/after signal)
sum by (pod) (increase(vllm:request_success_total[3m]))

# EPP scheduling decisions
sum by (pod) (rate(inference_extension_picked_endpoint_total[3m]))

# KV cache utilization per pod
avg by (pod) (vllm:kv_cache_usage_perc)
```

## Production benchmark results (for context)

On 8× Qwen3-32B (TP=2) with the same shared-prefix workload:

| Metric | k8s Service | llm-d | Change |
|--------|-------------|-------|--------|
| Requests/sec | 5.1 | 7.1 | +38.9% |
| Mean TTFT | 72.9s | 2.1s | -97.1% |
| Mean latency | 123.5s | 85.0s | -31.2% |
