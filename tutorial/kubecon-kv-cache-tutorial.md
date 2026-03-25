# KV-Cache Wins You Can Feel: Building AI-Aware LLM Routing on Kubernetes

> **KubeCon Hands-On Tutorial** | 75 minutes
>
> By the end of this tutorial you will have a working llm-d environment with
> intelligent inference scheduling, observability via Prometheus and Grafana,
> and a clear before/after comparison of cache-blind vs. cache-aware LLM routing.

---

## Agenda

| Time | Section | What You'll Do |
|------|---------|---------------|
| 0:00 | [Part 0: Presentation + Setup](#part-0-presentation--setup-15-min) | Context on KV-cache locality; set up your environment in parallel |
| 0:15 | [Part 1: Baseline — Vanilla Kubernetes](#part-1-baseline--vanilla-kubernetes-20-min) | Deploy vLLM behind a plain Service, send traffic, observe round-robin |
| 0:35 | [Part 2: Deploy llm-d & Compare](#part-2-deploy-llm-d--compare-30-min) | Install inference scheduler, route same traffic, see the difference |
| 1:05 | [Part 3: Wrap-Up](#part-3-wrap-up-10-min) | Recap, production numbers, next steps |

> **Using Claude Code?** There's a `tutorial/CLAUDE.md` guide that lets an AI
> assistant walk you through every step. Just open the repo in Claude Code and
> say "walk me through the tutorial."

---

## Prerequisites

Install these tools **before** the session if possible. If not, you can install
during the presentation (Part 0).

| Tool | Version | Install |
|------|---------|---------|
| `docker` | 20.10+ | [docs.docker.com](https://docs.docker.com/get-docker/) |
| `kind` | v0.20+ | `brew install kind` / [kind.sigs.k8s.io](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) |
| `kubectl` | v1.28+ | [kubernetes.io/docs](https://kubernetes.io/docs/tasks/tools/install-kubectl/) |
| `helm` | v3.12+ (not v4) | [helm.sh/docs](https://helm.sh/docs/intro/install/) |
| `helmfile` | v1.1+ | [github.com/helmfile](https://github.com/helmfile/helmfile?tab=readme-ov-file#installation) |
| `yq` | v4+ | `brew install yq` / [github.com/mikefarah/yq](https://github.com/mikefarah/yq) |
| `jq` | any | `brew install jq` / [jqlang.github.io](https://jqlang.github.io/jq/) |

> **Shortcut**: The llm-d repo has a script that installs kubectl, helm, helmfile, and yq:
> ```bash
> ./guides/prereq/client-setup/install-deps.sh
> ```

---

## Part 0: Presentation + Setup (15 min)

*The instructor presents while you set up your environment. Run the commands
below as you follow along — everything runs in the background while the
presentation covers the "why."*

### Key Concepts (presented by instructor)

**The problem**: Kubernetes default load balancing (round-robin / random) is cache-blind.
LLM inference workloads reuse KV-cache entries heavily — multi-turn conversations,
shared system prompts, retrieval-augmented generation all produce overlapping prefixes.
Scattering these requests across pods destroys locality, forces recomputation, wastes GPU
cycles, and inflates time-to-first-token (TTFT).

**Why it matters**: Hitting the KV-cache can make prefill 10x cheaper and up to 50x faster.
A cache miss means the GPU must recompute every token in the prompt from scratch.
At scale, this is the difference between viable and prohibitively expensive.

**What llm-d does**: llm-d is a Kubernetes-native distributed inference serving stack.
Its core idea: replace the default Service load balancing with an intelligent inference
scheduler that understands KV-cache state, prefix locality, and per-instance load.

Architecture:
- **vLLM** as the model server (or our simulator for this tutorial)
- **Kubernetes Gateway API** as the traffic management layer
- **Inference Scheduler (EPP)** — an endpoint picker that scores instances based on
  prefix-cache affinity, queue depth, and KV-cache utilization
- **Envoy proxy** handles the actual traffic, delegating routing decisions to the EPP

### While the presentation runs — set up your environment

**Step 1: Clone the repo and create the cluster**

```bash
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
```

This takes 2-5 minutes. While it runs, continue with Step 2.

**Step 2: Pull container images (run in background)**

```bash
# These 4 images are required (~800 MB total)
docker pull ghcr.io/llm-d/llm-d-inference-sim:v0.7.1 &
docker pull ghcr.io/llm-d/llm-d-routing-sidecar:v0.6.0 &
docker pull ghcr.io/llm-d/llm-d-inference-scheduler:v0.6.0 &
docker pull cr.kgateway.dev/kgateway-dev/envoy-wrapper:v2.1.1 &

# Optional: the full benchmark harness (~3 GB) — pull if your connection allows
docker pull ghcr.io/llm-d/llm-d-benchmark:v0.5.0 &

wait  # wait for all pulls to finish
```

**Step 3: Load images into kind and install monitoring**

Once the cluster is up and images are pulled:

```bash
# Load images into kind
kind load docker-image ghcr.io/llm-d/llm-d-inference-sim:v0.7.1 --name llm-d-tutorial
kind load docker-image ghcr.io/llm-d/llm-d-routing-sidecar:v0.6.0 --name llm-d-tutorial
kind load docker-image ghcr.io/llm-d/llm-d-inference-scheduler:v0.6.0 --name llm-d-tutorial
kind load docker-image cr.kgateway.dev/kgateway-dev/envoy-wrapper:v2.1.1 --name llm-d-tutorial

# If the benchmark image finished pulling, load it too
docker images ghcr.io/llm-d/llm-d-benchmark:v0.5.0 -q | grep -q . && \
  kind load docker-image ghcr.io/llm-d/llm-d-benchmark:v0.5.0 --name llm-d-tutorial

# Install Prometheus + Grafana with llm-d dashboards
./docs/monitoring/scripts/install-prometheus-grafana.sh
```

**Step 4: Create namespace**

```bash
export NAMESPACE=llm-d-tutorial
kubectl create namespace ${NAMESPACE}
```

### Verify setup

```bash
kubectl get nodes
# Expected: 1 control-plane + 3 workers, all Ready

kubectl get pods -n llm-d-monitoring --no-headers | wc -l
# Expected: 5+ pods
```

> **Don't have all images yet?** That's fine — the 3 core images (sim, sidecar,
> scheduler) are only ~600 MB total. The benchmark image is optional; we have a
> lightweight traffic script as a fallback.

---

## Part 1: Baseline — Vanilla Kubernetes (20 min)

Goal: deploy model server replicas behind a plain Kubernetes Service (round-robin)
and observe how requests are distributed without any cache awareness.

### 1.1 Deploy the simulator pods

We deploy **8 simulator replicas** behind a standard Kubernetes Service — matching
the 8-pod Qwen3-32B TP=2 setup from the inference-scheduling guide.

```bash
cat <<'EOF' | kubectl apply -n ${NAMESPACE} -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-baseline
  labels:
    app: vllm-baseline
spec:
  replicas: 8
  selector:
    matchLabels:
      app: vllm-baseline
  template:
    metadata:
      labels:
        app: vllm-baseline
    spec:
      containers:
        - name: vllm-sim
          image: ghcr.io/llm-d/llm-d-inference-sim:v0.7.1
          args: ["--model", "random"]
          ports:
            - containerPort: 8000
              name: http
          resources:
            requests:
              cpu: "250m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-baseline
spec:
  selector:
    app: vllm-baseline
  ports:
    - name: http
      port: 80
      targetPort: 8000
  type: ClusterIP
EOF
```

Wait for all pods to be ready:

```bash
kubectl rollout status deployment/vllm-baseline -n ${NAMESPACE} --timeout=120s
kubectl get pods -n ${NAMESPACE}
# Should show 8 Running pods
```

### 1.2 Open the observability dashboards

```bash
# Grafana
kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80 &

# Prometheus
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090 &
```

- **Grafana**: [http://localhost:3000](http://localhost:3000) (login: `admin` / `admin`)
- **Prometheus**: [http://localhost:9090](http://localhost:9090)

### 1.3 Send traffic to the baseline

We need a port-forward to the baseline Service, then we run the traffic generator:

```bash
kubectl port-forward -n ${NAMESPACE} svc/vllm-baseline 8000:80 &
```

**Option A — Full benchmark** (if you pulled the ~3 GB benchmark image):

```bash
export GATEWAY_SVC=vllm-baseline
export BENCHMARK_PVC=tutorial-bench-pvc

# Create HF token secret and PVC (needed by the benchmark harness)
export HF_TOKEN=<your-huggingface-token>
kubectl create secret generic llm-d-hf-token \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}"

cat <<'EOF' | kubectl apply -n ${NAMESPACE} -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: tutorial-bench-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
EOF

# Download the benchmark runner
curl -sL -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
chmod u+x run_only.sh

# Generate config and run
sed -e "s|\${NAMESPACE}|${NAMESPACE}|g" \
    -e "s|\${GATEWAY_SVC}|${GATEWAY_SVC}|g" \
    -e "s|\${BENCHMARK_PVC}|${BENCHMARK_PVC}|g" \
    tutorial/benchmark-template.yaml > config-baseline.yaml
./run_only.sh -c config-baseline.yaml
```

This runs inference-perf at 35 req/s for 120 seconds (~4200 requests) using
the same shared-prefix workload from the inference-scheduling guide: 150 prefix
groups, 6000-token system prompts, 1200-token questions, 1000-token outputs.

**Option B — Lightweight traffic script** (no extra images needed):

```bash
./tutorial/generate-shared-prefix-traffic.sh \
    -e http://localhost:8000 \
    -m random \
    -d 120 \
    -r 5 \
    -p 3
```

This sends requests with shared system prompts via curl. Lower volume than the
full benchmark but sufficient to see the routing difference in Grafana.

### 1.4 Observe the baseline behavior

> While traffic runs, watch the Grafana **llm-d vLLM Overview** dashboard.

In **Prometheus**, check per-pod request distribution:

```promql
sum by (pod) (increase(vllm:request_success_total[3m]))
```

**What you should see**: Requests are spread roughly evenly across all 8 pods.
Kubernetes round-robin treats all requests as equal — it has no concept of
"these requests share a prefix and should go to the same backend." On real GPUs,
this means every pod independently computes KV-cache for the same 6000-token
system prompts — **8x the GPU work for identical prefixes**.

---

## Part 2: Deploy llm-d & Compare (30 min)

Now we replace the plain Service with the llm-d inference scheduling stack.
This adds the Gateway API, the inference scheduler (EPP), and prefix-cache-aware
routing.

### 2.1 Install Gateway API CRDs

```bash
./guides/prereq/gateway-provider/install-gateway-provider-dependencies.sh
```

This installs:
- Gateway API v1.4.0 CRDs
- Gateway API Inference Extension v1.3.1 CRDs (including `InferencePool`)

Verify:

```bash
kubectl api-resources --api-group=inference.networking.k8s.io
# Should show InferencePool
```

### 2.2 Install the gateway provider (kgateway)

We use **kgateway** — a lightweight Envoy-based Gateway implementation
that works well on kind:

```bash
cd guides/prereq/gateway-provider
helmfile apply -f kgateway.helmfile.yaml
cd ../../..
```

### 2.3 Clean up the baseline

Remove the vanilla Service and Deployment — we'll redeploy through llm-d's
Helm charts:

```bash
kubectl delete deployment vllm-baseline -n ${NAMESPACE}
kubectl delete service vllm-baseline -n ${NAMESPACE}
```

### 2.4 Deploy the full llm-d simulator stack

The simulated-accelerators guide deploys the complete stack: infrastructure
(Gateway), inference scheduler (EPP), and model service (simulator pods).

```bash
cd guides/simulated-accelerators
helmfile apply -e kgateway -n ${NAMESPACE}
```

This creates three Helm releases:

| Release | Chart | What it deploys |
|---------|-------|----------------|
| `infra-sim` | `llm-d-infra` | Gateway + infrastructure |
| `gaie-sim` | `inferencepool` | InferencePool + EPP (inference scheduler) |
| `ms-sim` | `llm-d-modelservice` | Simulator pods (3 decode + 1 prefill by default) |

Scale the decode deployment to **8 replicas** and apply kind-specific fixes:

```bash
# Scale decode to 8 replicas
kubectl scale deployment ms-sim-llm-d-modelservice-decode -n ${NAMESPACE} --replicas=8

# Fix: kgateway's envoy needs a writable /tmp on kind
kubectl patch deployment infra-sim-inference-gateway -n ${NAMESPACE} --type=json -p='[
  {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"tmp","emptyDir":{}}},
  {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":{"name":"tmp","mountPath":"/tmp"}}
]'

# Fix: EPP imagePullPolicy must be IfNotPresent for pre-loaded images
kubectl patch deployment gaie-sim-epp -n ${NAMESPACE} --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]'
```

Wait for all pods to be ready:

```bash
kubectl get pods -n ${NAMESPACE} -w
```

You should see:
- 1 gateway pod (`infra-sim-inference-gateway-*`)
- 1 EPP pod (`gaie-sim-epp-*`)
- **8 decode simulator pods** (`ms-sim-llm-d-modelservice-decode-*`)
- 1 prefill simulator pod (`ms-sim-llm-d-modelservice-prefill-*`)

### 2.5 Install the HTTPRoute

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
cd ../..
```

### 2.6 Verify the deployment

```bash
kubectl get gateway -n ${NAMESPACE}
kubectl get inferencepool -n ${NAMESPACE}
```

### 2.7 Send traffic through llm-d

Smoke test:

```bash
export GATEWAY_SVC=$(kubectl get svc -n "${NAMESPACE}" -o yaml | \
  yq '.items[] | select(.metadata.name | test(".*-inference-gateway(-.*)?$")).metadata.name' | head -n1)

kubectl port-forward -n ${NAMESPACE} svc/${GATEWAY_SVC} 8000:80 &
curl -s http://localhost:8000/v1/models | jq
kill %% 2>/dev/null  # stop port-forward; benchmark connects in-cluster
```

Now run the **exact same traffic** through the llm-d inference scheduler:

**Option A — Full benchmark:**

```bash
sed -e "s|\${NAMESPACE}|${NAMESPACE}|g" \
    -e "s|\${GATEWAY_SVC}|${GATEWAY_SVC}|g" \
    -e "s|\${BENCHMARK_PVC}|${BENCHMARK_PVC}|g" \
    tutorial/benchmark-template.yaml > config-llmd.yaml
./run_only.sh -c config-llmd.yaml
```

**Option B — Lightweight traffic script:**

```bash
kubectl port-forward -n ${NAMESPACE} svc/${GATEWAY_SVC} 8000:80 &

./tutorial/generate-shared-prefix-traffic.sh \
    -e http://localhost:8000 \
    -m random \
    -d 120 \
    -r 5 \
    -p 3

kill %% 2>/dev/null
```

### 2.8 See the difference

**Check request distribution in the logs:**

```bash
for pod in $(kubectl get pods -n ${NAMESPACE} -l llm-d.ai/role=decode -o name); do
  echo "=== ${pod} ==="
  kubectl logs ${pod} -n ${NAMESPACE} -c vllm --tail=100 | grep -c "completions" || echo "0 requests"
done
```

**What you should see now**: Requests are no longer evenly distributed. The
inference scheduler routes requests that share a prefix to the **same instance**
— maximizing KV-cache reuse. Some decode pods handle more traffic while others
are relatively idle.

**Compare in Grafana:**

Make sure port-forwards are active:

```bash
kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80 &
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090 &
```

Open [http://localhost:3000](http://localhost:3000) and check these dashboards:

| Dashboard | What to Look For |
|-----------|-----------------|
| **llm-d vLLM Overview** | Request distribution is now skewed — concentrated on fewer pods |
| **llm-d Performance Dashboard** | KV cache utilization differs across pods (hot vs. cold) |

**Key PromQL queries** in [http://localhost:9090](http://localhost:9090):

Per-pod request count (the main before/after signal):
```promql
sum by (pod) (increase(vllm:request_success_total[3m]))
```

EPP scheduling decisions:
```promql
sum by (pod) (rate(inference_extension_picked_endpoint_total[3m]))
```

KV cache utilization per pod:
```promql
avg by (pod) (vllm:kv_cache_usage_perc)
```

In the baseline this was ~equal across 8 pods. With llm-d, you see concentration
— pods that serve shared-prefix groups get more traffic.

---

## Part 3: Wrap-Up (10 min)

### What you built

You now have a working llm-d environment with:
- An inference simulator running on Kubernetes
- The llm-d inference scheduler providing prefix-cache-aware routing
- Prometheus + Grafana observability with llm-d dashboards
- A clear understanding of why cache-blind load balancing is wasteful

### Production numbers

In production benchmarks on 8× Qwen3-32B (TP=2) with the same shared-prefix
workload, llm-d shows:

| Metric | k8s Service | llm-d | Change |
|--------|-------------|-------|--------|
| Requests/sec | 5.1 | 7.1 | **+38.9%** |
| Output tokens/sec | 4,787 | 6,644 | **+38.8%** |
| Mean TTFT | 72.9s | 2.1s | **-97.1%** |
| Mean request latency | 123.5s | 85.0s | **-31.2%** |

*(Source: [inference-scheduling guide](./guides/inference-scheduling/README.md#comparing-llm-d-scheduling-to-a-simple-kubernetes-service)
— same workload, same hardware, llm-d vs plain k8s Service)*

### Cleanup

```bash
# Remove the llm-d stack
cd guides/simulated-accelerators
helmfile destroy -e kgateway -n ${NAMESPACE}
cd ../..

# Remove the gateway provider
cd guides/prereq/gateway-provider
helmfile destroy -f kgateway.helmfile.yaml
./install-gateway-provider-dependencies.sh delete
cd ../../..

# Remove monitoring
./docs/monitoring/scripts/install-prometheus-grafana.sh -u

# Delete the namespace
kubectl delete namespace ${NAMESPACE}

# Delete the kind cluster
kind delete cluster --name llm-d-tutorial
```

### Where to go next

| Path | Guide | What it adds |
|------|-------|-------------|
| **Production deployment** | [Inference Scheduling](./guides/inference-scheduling/README.md) | Real GPU deployment with vLLM + full benchmark |
| **Lower TTFT** | [Prefill/Decode Disaggregation](./guides/pd-disaggregation/README.md) | Split prefill and decode onto separate servers |
| **Large MoE models** | [Wide Expert-Parallelism](./guides/wide-ep-lws/README.md) | Deploy DeepSeek-R1 class models |
| **Better cache reuse** | [Tiered Prefix Cache](./guides/tiered-prefix-cache/README.md) | Offload KV-cache to CPU/SSD/storage |
| **Precise routing** | [Precise Prefix Cache Aware](./guides/precise-prefix-cache-aware/README.md) | Introspect actual vLLM cache state |
| **Autoscaling** | [Workload Autoscaling](./guides/workload-autoscaling/README.md) | Dynamic scaling based on traffic |

### Resources

- **llm-d repository**: [github.com/llm-d/llm-d](https://github.com/llm-d/llm-d)
- **Documentation**: [llm-d.ai](https://www.llm-d.ai)
- **Slack**: [llm-d.ai/slack](https://llm-d.ai/slack)
- **Inference Gateway (upstream)**: [gateway-api-inference-extension.sigs.k8s.io](https://gateway-api-inference-extension.sigs.k8s.io/)

---

## Appendix A: Troubleshooting

### Pods stuck in Pending

```bash
kubectl describe pod <pod-name> -n ${NAMESPACE}
```

Common causes on kind: insufficient resources. Reduce simulator replicas or add
more kind worker nodes.

### Gateway not programmed

```bash
kubectl describe gateway -n ${NAMESPACE}
```

Ensure the gateway provider (kgateway) is installed and the CRDs are present:

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
```

### No metrics in Grafana

1. Verify PodMonitors exist: `kubectl get podmonitors -n ${NAMESPACE}`
2. Check Prometheus targets: visit `http://localhost:9090/targets`
3. Ensure the monitoring namespace label is set:
   ```bash
   kubectl label namespace ${NAMESPACE} monitoring-ns=llm-d-monitoring
   ```

### Port-forward drops

Port-forwards are fragile. If they stop working, restart them:

```bash
kubectl port-forward -n ${NAMESPACE} svc/${GATEWAY_SVC} 8000:80 &
kubectl port-forward -n llm-d-monitoring svc/llmd-grafana 3000:80 &
kubectl port-forward -n llm-d-monitoring svc/llmd-kube-prometheus-stack-prometheus 9090:9090 &
```

### Gateway crashes with "Failed to create temporary file"

The kgateway envoy proxy runs with `readOnlyRootFilesystem: true`. The kind-specific
patches in step 2.4 fix this by adding a writable `/tmp` volume. If you missed that
step:

```bash
kubectl patch deployment infra-sim-inference-gateway -n ${NAMESPACE} --type=json -p='[
  {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"tmp","emptyDir":{}}},
  {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":{"name":"tmp","mountPath":"/tmp"}}
]'
```

### EPP stuck in ImagePullBackOff

The EPP deployment defaults to `imagePullPolicy: Always`. On kind (where images
are pre-loaded and the registry may be unreachable), patch it:

```bash
kubectl patch deployment gaie-sim-epp -n ${NAMESPACE} --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"IfNotPresent"}]'
```

### Helm v4 breaks helmfile

If you see `unknown flag: --client` errors, you have Helm v4 installed. Helmfile
and helm-diff do not yet support Helm v4. Install Helm v3 instead:

```bash
curl -sL https://get.helm.sh/helm-v3.17.3-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz | \
  tar xz --strip-components=1 -C /usr/local/bin */helm
```

### Simulator returns errors

Check simulator logs:

```bash
kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=decode -c vllm --tail=20
```

The simulator mimics the vLLM API. If you get 404s, verify the model name is
`random` (what the simulator serves). Check with:

```bash
kubectl port-forward -n ${NAMESPACE} svc/${GATEWAY_SVC} 8000:80 &
curl -s http://localhost:8000/v1/models | jq
```

---

## Appendix B: Architecture Reference

```
                                    ┌─────────────────────────┐
                                    │     Grafana + Prom      │
                                    │    (observability)       │
                                    └────────────┬────────────┘
                                                 │ scrapes metrics
                                                 │
  Client                                         │
    │                                            │
    │  curl /v1/completions                      │
    ▼                                            │
┌─────────┐      ┌───────────┐      ┌───────────▼──────────┐
│ Gateway  │─────▶│    EPP    │─────▶│   vLLM Simulator     │
│ (Envoy)  │      │ (Inference│      │   Pod 1 (decode)     │
│          │      │ Scheduler)│      ├──────────────────────┤
│          │      │           │      │   Pod 2 (decode)     │
│          │      │  Scores:  │      ├──────────────────────┤
│          │      │  - prefix │      │   Pod 3 (decode)     │
│          │      │    match  │      ├──────────────────────┤
│          │      │  - load   │      │   Pod 4 (prefill)    │
│          │      │  - kv $   │      └──────────────────────┘
└─────────┘      └───────────┘

Without llm-d: Client → k8s Service → random pod (cache-blind)
With    llm-d: Client → Gateway → EPP picks best pod → cache-aware
```
