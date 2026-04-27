# Active-Active High Availability

The [default install](README.md#installation-instructions) runs a single scheduler replica with a central ZMQ endpoint — vLLM publishers connect into the scheduler's service. Simple, and correct for most single-scheduler deployments.

This sub-guide switches the guide to **active-active HA**: two scheduler replicas both serving traffic simultaneously, fronted by a single Kubernetes Service that load-balances between them. If one replica dies, the Service routes entirely to the survivor — no manual failover, no leader-election gap.

## What "2 gateways and 2 schedulers" means here

The [standalone inference-scheduler chart](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/main/config/charts/standalone) bundles the scheduler (EPP) **and** its Envoy gateway sidecar into a single pod template. Setting `replicas: 2` gives you:

```
         ┌──────────────────────────┐
Client ──▶  svc/<release>-epp (ClusterIP)
         └───────────┬──────────────┘
                     │ load-balances
         ┌───────────┴──────────────┐
         ▼                          ▼
   pod replica 0             pod replica 1
   ┌──────────────┐          ┌──────────────┐
   │ envoy  :8081 │          │ envoy  :8081 │ ← gateway (proxy)
   │ epp   :9002 │           │ epp   :9002 │  ← scheduler
   └──────┬───────┘          └──────┬───────┘
          │ ZMQ SUB                 │ ZMQ SUB
          └──────────┬──────────────┘
                     │ both replicas dial every vLLM pod
          ┌──────────┴──────────────┐
          ▼                          ▼
       vllm-0 (tcp://*:5556)   vllm-1 ... vllm-N
```

Each replica pod runs:
- The **scheduler** (EPP) — scoring and routing decisions.
- An **Envoy gateway sidecar** — the public-facing proxy that clients connect to on port 8081.

So `replicas: 2` means two gateway+scheduler pairs behind one Service. Both are actively serving.

## Why this needs per-pod KV events

Each scheduler replica has its own in-memory prefix-cache index. To populate every replica's index identically, every vLLM pod must publish its KV events on its own socket (`tcp://*:5556`), and every scheduler replica must independently subscribe to every pod. The central ZMQ mode can't do this — each vLLM ZMQ PUB socket connects to exactly one SUB endpoint.

[llm-d-inference-scheduler#862](https://github.com/llm-d/llm-d-inference-scheduler/pull/862)'s data-layer `EndpointExtractor` handles the per-pod subscriber lifecycle: endpoint add/delete events from the `endpoint-notification-source` are fed into the scorer's `ExtractEndpoint`, which installs or removes a ZMQ subscriber per pod. No opportunistic subscribe-on-score, no TTL-cache hack.

## Two things flip together

| Component                              | Default (central) mode                                   | Active-active mode                                  |
| -------------------------------------- | -------------------------------------------------------- | --------------------------------------------------- |
| Scheduler `replicas`                   | `1`                                                      | `2` (or more)                                       |
| Scheduler `--ha-enable-leader-election`| (flag not added)                                         | explicitly `false` so all replicas serve            |
| Scheduler `discoverPods`               | `false`                                                  | `true` (+ `podDiscoveryConfig.socketPort: 5556`)    |
| Scheduler `zmqEndpoint`                | `tcp://*:5556`                                           | unset (scheduler dials per pod instead)             |
| Scheduler data-layer sources           | —                                                        | `endpoint-notification-source` → scorer             |
| Scheduler extra plugins                | —                                                        | `endpoint-notification-source`, `metrics-data-source`, `core-metrics-extractor` |
| vLLM `--kv-events-config` endpoint     | `tcp://<release>-epp.<ns>.svc.cluster.local:5556`        | `tcp://*:5556` (bind per-pod)                       |
| vLLM pod port `5556`                   | (not exposed)                                            | exposed as `kv-events`                              |

Flipping the scheduler side alone won't help — without the modelserver change, vLLM is still pushing to the central service and per-pod scorers see nothing. Both sides must move together.

### Leader election

The chart auto-adds `--ha-enable-leader-election` whenever `replicas > 1`. With that flag, only the elected leader's readiness probe passes — the Service routes traffic **only** to the leader, so you get active-passive HA, not active-active. The active-active values file overrides the flag to `false` (cobra/pflag honors last-occurrence wins), so both replicas pass readiness and the Service load-balances across both.

## Install

### Scheduler

Layer [`scheduler/features/active-active.values.yaml`](scheduler/features/active-active.values.yaml) on top of the base values file. It replaces `pluginsCustomConfig` wholesale (helm doesn't merge YAML strings), bumps `inferenceExtension.replicas` to 2, and disables leader election.

```bash
# Helm v4: register the post-renderer plugin once (same plugin as the default install)
helm plugin install guides/precise-prefix-cache-aware/scheduler/patches/uds-tokenizer 2>/dev/null || true

helm install precise-prefix-cache-aware \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/precise-prefix-cache-aware/scheduler/precise-prefix-cache-aware.values.yaml \
  -f guides/precise-prefix-cache-aware/scheduler/features/active-active.values.yaml \
  --post-renderer uds-tokenizer \
  -n ${NAMESPACE} --version v1.4.0
```

On helm v3, replace `--post-renderer uds-tokenizer` with `--post-renderer ./guides/precise-prefix-cache-aware/scheduler/patches/uds-tokenizer/post-renderer.sh` — see [README.md](README.md#2-deploy-the-standalone-inference-scheduler) for the details.

Bump `inferenceExtension.replicas` higher if you want more than two active replicas.

### Model server

Apply the [`modelserver/components/active-active/`](modelserver/components/active-active) kustomize Component on top of your chosen accelerator overlay. It overrides `KV_EVENTS_ENDPOINT` to `tcp://*:5556` and exposes container port 5556 so the schedulers can dial each pod.

Make a small sibling overlay and apply it:

```bash
mkdir -p my-overlays/gpu-active-active
cat > my-overlays/gpu-active-active/kustomization.yaml <<'EOF'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../guides/precise-prefix-cache-aware/modelserver/nvidia-gpu/vllm
components:
  - ../../guides/precise-prefix-cache-aware/modelserver/components/active-active
EOF

kubectl apply -n ${NAMESPACE} -k my-overlays/gpu-active-active
```

The same pattern works for the other accelerators (`amd-gpu`, `cpu`, `hpu`, `tpu-v6`, `tpu-v7`, `xpu`).

## Verifying active-active

**Both replica pods are Ready** (leader-election collapse would show only one):

```bash
kubectl get pods -n ${NAMESPACE} -l inferencepool=precise-prefix-cache-aware-epp
# NAME                                                    READY   STATUS
# precise-prefix-cache-aware-epp-<hash>-aaaaa   3/3     Running
# precise-prefix-cache-aware-epp-<hash>-bbbbb   3/3     Running
```

**Both pods receive traffic** — the Service endpoints list both:

```bash
kubectl get endpointslices -n ${NAMESPACE} -l kubernetes.io/service-name=precise-prefix-cache-aware-epp -o yaml \
  | grep -E "^\s*- addresses:|ready: "
```

Each endpoint should have `conditions.ready: true`.

**Each replica subscribes to every vLLM pod** — scheduler logs should show one `Ensured` line per `(replica × pod)`:

```bash
kubectl logs -l inferencepool=precise-prefix-cache-aware-epp -n ${NAMESPACE} --all-containers \
  | grep -E "Ensured KV-events subscriber|Removed KV-events subscriber"
```

Delete a vLLM pod and watch for a matching `Removed` line before the replacement comes up — that confirms `ExtractEndpoint` is wired to endpoint lifecycle events.

**Each vLLM pod's ZMQ socket is listening**:

```bash
kubectl exec -n ${NAMESPACE} <vllm-pod> -c modelserver -- \
  netstat -tln | grep 5556
# tcp6  0  0  :::5556  :::*  LISTEN
```

**Failover test** — delete one scheduler replica and observe that requests still succeed:

```bash
# In one terminal, send a steady stream of requests.
# In another:
kubectl delete pod -n ${NAMESPACE} -l inferencepool=precise-prefix-cache-aware-epp \
  --field-selector=metadata.name=<one-of-the-pods>
```

The Service removes the deleted pod from its endpoints within a few seconds; the surviving replica keeps serving. When the replacement pod comes up, its scorer re-subscribes to every vLLM pod and its index warms up from live KV events.

## Tradeoffs vs. the default

Active-active costs you:
- **1 ZMQ socket per (replica × vLLM pod)** — with N replicas × M pods, that's N×M sockets across the cluster. Negligible at normal scales.
- **Duplicate index memory** — each replica maintains its own KV-block index. Real-world index size is small relative to pod memory.
- **A deploy-time constraint**: the standalone chart hardcodes `strategy: Recreate`, so rolling updates take both replicas down briefly. For rolling-friendly HA, deploy two separate releases and front them with a custom Service.

Stick with the single-replica default unless you actually need HA — it's strictly simpler.
