# Interactive Pod

The interactive pod is a debug and testing environment that runs inside your Kubernetes cluster, right next to your llm-d stack. It comes pre-loaded with all the tools you need to inspect, benchmark, and troubleshoot your deployment without needing to install anything locally or deal with network access issues from outside the cluster.

Think of it as a Swiss Army knife pod — you `exec` into it and have a full shell with `kubectl`, `curl`, `jq`, `guidellm`, `stern`, `yq`, and more already available.

## What's inside the image

| Tool | Purpose |
|------|---------|
| `kubectl` + `k` alias | Inspect and manage cluster resources |
| `curl` + `jq` | Query endpoints and parse JSON responses |
| `guidellm` | Run inference benchmarks against the gateway |
| `stern` | Tail logs from multiple pods at once |
| `yq` | Parse and query YAML files |
| `prepare-inference.sh` | Auto-discover gateway address and model name |
| `run-guide-llm.sh` | Wrapper to run guidellm benchmarks |
| `htop` / `btop` | Monitor CPU and memory usage |
| `promtool` | Query and validate Prometheus metrics |
| `vim` / `nano` | Edit files inside the pod |
| ShareGPT dataset | Pre-downloaded benchmark dataset at `/app/vllm/benchmarks/` |

## Why it needs RBAC permissions

By default, pods in Kubernetes have no permission to talk to the Kubernetes API — they can't list pods, read services, or inspect gateway objects. The interactive pod needs to do all of these things to be useful, so `rbac.yaml` grants it a `Role` called `interactive-pod-editor` with read/write access to the resources it needs.

Here's what each permission group is for:

| Permission group | Why it's needed |
|-----------------|----------------|
| `pods`, `pods/log`, `services`, `endpoints` | Inspect running vLLM pods, read their logs, find service addresses |
| `deployments`, `statefulsets`, `replicasets` | Check replica counts, rollout status, and resource specs |
| `configmaps`, `secrets` | Read configuration and HuggingFace token secrets |
| `gateways`, `httproutes` | Inspect gateway routing rules and check if routes are programmed correctly |
| `leaderworkersets` | Inspect wide-EP multi-host inference deployments |
| `inference.networking.k8s.io/*` | Read and manage InferencePool and InferenceModel resources (the core llm-d scheduling objects) |
| `servicemonitors`, `podmonitors` | Inspect Prometheus scrape configuration |
| `roles`, `rolebindings` | Debug RBAC issues |
| `telemetries` | Inspect Istio telemetry configuration |

> **Note**: These permissions are scoped to the namespace the pod is deployed in. The pod cannot access resources in other namespaces.

## How to deploy

Make sure you have `kubectl` configured and pointing at your cluster, and that you have already deployed an llm-d stack in a namespace.

```bash
export NAMESPACE=<your llm-d namespace>

kubectl apply -k helpers/interactive-pod/manifests -n ${NAMESPACE}
```

This creates three resources:
- `sa.yaml` — a ServiceAccount named `interactive-pod`
- `rbac.yaml` — a Role and RoleBinding granting the permissions above
- `deployment.yaml` — the pod itself, running `sleep infinity` so it stays alive

Check that the pod is running:

```bash
kubectl get pods -n ${NAMESPACE} -l app=interactive-pod
```

You should see something like:

```
NAME                               READY   STATUS    RESTARTS   AGE
interactive-pod-6d9f7b8c4d-xk2pq   1/1     Running   0          30s
```

## How to exec into it

```bash
kubectl exec -it -n ${NAMESPACE} \
  $(kubectl get pod -n ${NAMESPACE} -l app=interactive-pod -o name | head -1) \
  -- bash
```

You'll land in a full bash shell inside the pod. Your namespace is automatically available as the `$NAMESPACE` environment variable.

## Example workflows

### Discover your gateway and model name

The `prepare-inference.sh` script queries the cluster and exports the gateway address and model name as environment variables. Source it before running any benchmarks:

```bash
source /app/prepare-inference.sh
```

On success you'll see:

```
Successfully curled the gateway! The following values have been discovered:
GATEWAY_NAME: infra-inference-scheduling-inference-gateway
GATEWAY_ADDRESS: 10.96.1.42
GATEWAY_SERVICE_ENDPOINT: http://infra-inference-scheduling-inference-gateway-istio.llm-d.svc.cluster.local
MODEL_NAME: "Qwen/Qwen3-32B"
```

Use `-v` for verbose output if something isn't working:

```bash
source /app/prepare-inference.sh -v
```

### Run a quick benchmark with guidellm

After sourcing `prepare-inference.sh`:

```bash
guidellm benchmark \
  --target "${GATEWAY_SERVICE_ENDPOINT}" \
  --rate-type sweep \
  --max-seconds 30 \
  --model "${MODEL_NAME}" \
  --data "prompt_tokens=256,output_tokens=128"
```

This sweeps through request rates and reports throughput and latency at each rate.

### Send a test request to the gateway

```bash
curl -s "${GATEWAY_SERVICE_ENDPOINT}/v1/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": ${MODEL_NAME},
    \"prompt\": \"Hello, world!\",
    \"max_tokens\": 50
  }" | jq .
```

### Inspect the InferencePool

The InferencePool is the llm-d object that tracks which vLLM pods are available for scheduling. Check its status:

```bash
kubectl get inferencepool -n ${NAMESPACE}
kubectl describe inferencepool -n ${NAMESPACE}
```

Check which pods the pool considers ready:

```bash
kubectl get pods -n ${NAMESPACE} -l app=llm-d-model-server
```

### Check gateway routes

List all gateways and their assigned addresses:

```bash
kubectl get gateway -n ${NAMESPACE}
```

Inspect the HTTPRoute to see how traffic is being routed:

```bash
kubectl get httproute -n ${NAMESPACE}
kubectl describe httproute -n ${NAMESPACE}
```

A healthy gateway will show an address and a `Programmed: True` condition in the status.

### Tail logs from all vLLM pods at once

Use `stern` to stream logs from all model server pods simultaneously:

```bash
stern -n ${NAMESPACE} llm-d-model-server
```

Filter for errors only:

```bash
stern -n ${NAMESPACE} llm-d-model-server --include "ERROR|error|Exception"
```

### Check vLLM metrics directly

Query the Prometheus metrics endpoint on a specific pod:

```bash
POD=$(kubectl get pod -n ${NAMESPACE} -l app=llm-d-model-server -o name | head -1)

kubectl exec -n ${NAMESPACE} ${POD} -- \
  curl -s http://localhost:8000/metrics | grep -E "vllm:cache_config|vllm:num_requests"
```

## Cleanup

When you're done, remove the interactive pod and its RBAC resources:

```bash
kubectl delete -k helpers/interactive-pod/manifests -n ${NAMESPACE}
```
