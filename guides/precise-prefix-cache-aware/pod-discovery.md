# Sub-guide: pod discovery (active-active)

The [default install](README.md#prerequisites-and-installation) runs a **single** scheduler replica with a central ZMQ endpoint — vLLM publishers connect into the scheduler's service. This mode is simple and works for most single-scheduler deployments.

When you need **multiple scheduler replicas** (active-active, for HA or horizontal read scaling), every replica must observe every pod's KV events. The central endpoint can't fan out — each ZMQ PUB socket connects to exactly one SUB endpoint. Pod discovery flips the direction: vLLM binds `tcp://*:5557` per pod, and each scheduler replica dials every pod individually. The scheduler side uses [llm-d-inference-scheduler#862](https://github.com/llm-d/llm-d-inference-scheduler/pull/862)'s data-layer `EndpointExtractor` so per-pod subscribers are created and torn down in lockstep with pod lifecycle events — no opportunistic subscribe-on-score, no TTL-cache hack.

## What changes

Two pieces switch over together:

| Component | Central mode | Pod-discovery mode |
|---|---|---|
| vLLM `--kv-events-config` endpoint | `tcp://<sched-svc>.<ns>.svc.cluster.local:5557` | `tcp://*:5557` (bind per-pod) |
| vLLM pod ports | (none extra) | `5557/tcp` exposed |
| Scheduler `discoverPods` | `false` | `true` (+ `podDiscoveryConfig.socketPort: 5557`) |
| Scheduler `zmqEndpoint` | `tcp://*:5557` | unset |
| Scheduler extra plugins | — | `endpoint-notification-source`, `metrics-data-source`, `core-metrics-extractor` |
| Scheduler `dataLayer.sources` | — | wires endpoint events into the scorer |
| Scheduler replicas | 1 | N |

Flipping the scheduler side alone won't help — without the modelserver change, vLLM is still pushing to the central service and the per-pod scorer sees nothing. Both sides must move together.

## Install

### Scheduler

Layer [`scheduler/features/pod-discovery.values.yaml`](scheduler/features/pod-discovery.values.yaml) on top of the base values file. It replaces `pluginsCustomConfig` wholesale (helm doesn't merge YAML strings) and bumps `inferenceExtension.replicas` to 2; raise it further if you want more.

```bash
helm install precise-prefix-cache-aware-scheduler \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/recipes/scheduler/features/monitoring.values.yaml \
  -f guides/precise-prefix-cache-aware/scheduler/precise-prefix-cache-aware.values.yaml \
  -f guides/precise-prefix-cache-aware/scheduler/features/pod-discovery.values.yaml \
  --set provider.name=none \
  -n ${NAMESPACE} --version v1.4.0
```

### Model server

Apply the [`modelserver/components/pod-discovery/`](modelserver/components/pod-discovery) kustomize Component on top of your chosen accelerator overlay. The component overrides the `KV_EVENTS_ENDPOINT` env var to `tcp://*:5557` and exposes container port `5557`.

Make a small sibling overlay and build from there:

```bash
mkdir -p my-overlays/nvidia-gpu-pod-discovery
cat > my-overlays/nvidia-gpu-pod-discovery/kustomization.yaml <<'EOF'
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../guides/precise-prefix-cache-aware/modelserver/nvidia-gpu/vllm
components:
  - ../../guides/precise-prefix-cache-aware/modelserver/components/pod-discovery
EOF

kustomize build my-overlays/nvidia-gpu-pod-discovery | kubectl apply -n ${NAMESPACE} -f -
```

The same pattern works for the other accelerators (`amd-gpu`, `cpu`, `hpu`, `tpu-v6`, `tpu-v7`, `xpu`).

## Verifying pod discovery

Each scheduler replica should maintain one ZMQ subscriber per vLLM pod. Check that the scheduler observes endpoint add/delete events:

```bash
kubectl logs -l app=precise-prefix-cache-aware-scheduler-epp -n ${NAMESPACE} \
  | grep -E "Ensured KV-events subscriber|Removed KV-events subscriber"
```

An `Ensured` line per (replica × pod) pair confirms the data-layer `endpoint-notification-source` is wired and the scorer's `ExtractEndpoint` is handling lifecycle events. Delete a vLLM pod and watch for a matching `Removed` line before the new one comes up.

On the vLLM side, each pod's ZMQ socket should be listening:

```bash
kubectl exec -n ${NAMESPACE} <vllm-pod> -c modelserver -- \
  netstat -tln | grep 5557
# tcp6  0  0  :::5557  :::*  LISTEN
```

## Why two modes, not one

Pod discovery works with a single scheduler replica too — but it costs one ZMQ socket per (replica × pod) and adds the data-layer plumbing. For the common single-scheduler case the central binding is strictly simpler, so that's the default. Flip to pod discovery when you need more than one replica.
