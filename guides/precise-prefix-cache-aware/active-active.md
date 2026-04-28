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
   pod replica 0              pod replica 1
   ┌────────────────┐         ┌────────────────┐
   │ envoy   :8081  │         │ envoy   :8081  │ ← gateway (proxy)
   │ epp     :9002  │         │ epp     :9002  │ ← scheduler
   │ tokenizer-uds  │         │ tokenizer-uds  │ ← UDS tokenizer (post-renderer)
   └──────┬─────────┘         └──────┬─────────┘
          │ ZMQ SUB                  │ ZMQ SUB
          └──────────┬───────────────┘
                     │ both replicas dial every vLLM pod
          ┌──────────┴───────────────┐
          ▼                          ▼
       vllm-0 (tcp://*:5556)    vllm-1 ... vllm-N
```

Each replica pod runs three containers:
- The **scheduler** (`epp`) — scoring and routing decisions.
- An **Envoy gateway sidecar** — the public-facing proxy that clients connect to on port 8081.
- The **UDS tokenizer sidecar** (`tokenizer-uds`) — appended by the helm post-renderer; serves the `tokenizer` plugin over a Unix socket so tokenization runs out-of-process.

So `replicas: 2` means two gateway+scheduler+tokenizer triples behind one Service. Both are actively serving.

## Why this needs per-pod KV events

Each scheduler replica has its own in-memory prefix-cache index. To populate every replica's index identically, every vLLM pod must publish its KV events on its own socket (`tcp://*:5556`), and every scheduler replica must independently subscribe to every pod. The central ZMQ mode can't do this — each vLLM ZMQ PUB socket connects to exactly one SUB endpoint. Indexer write-path internals (event ingestion, sharded workers, dual-key design) are documented in [llm-d-kv-cache architecture](https://github.com/llm-d/llm-d-kv-cache/blob/main/docs/architecture.md).

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

The longer-term plan is to make pod-discovery the only mode and collapse this distinction. We're keeping central as the default while the per-pod path matures.

### Leader election

The chart auto-adds `--ha-enable-leader-election` whenever `replicas > 1`. With that flag, only the elected leader's readiness probe passes — the Service routes traffic **only** to the leader, so you get active-passive HA, not active-active. The active-active values file overrides the flag to `false` (cobra/pflag honors last-occurrence wins), so both replicas pass readiness and the Service load-balances across both.

## Install

### Scheduler

Layer [`scheduler/features/active-active.values.yaml`](scheduler/features/active-active.values.yaml) on top of the base values file. It replaces `pluginsCustomConfig` wholesale (helm doesn't merge YAML strings), bumps `inferenceExtension.replicas` to 2, and disables leader election.

```bash
# Register the post-renderer plugin once (same plugin as the default install)
helm plugin install guides/precise-prefix-cache-aware/scheduler/patches/uds-tokenizer 2>/dev/null || true

helm install precise-prefix-cache-aware \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/standalone \
  -f guides/recipes/scheduler/base.values.yaml \
  -f guides/precise-prefix-cache-aware/scheduler/precise-prefix-cache-aware.values.yaml \
  -f guides/precise-prefix-cache-aware/scheduler/features/active-active.values.yaml \
  --post-renderer uds-tokenizer \
  -n ${NAMESPACE} --version v1.4.0
```

Bump `inferenceExtension.replicas` higher if you want more than two active replicas.

### Model server

Apply the bundled active-active overlay at [`modelserver/active-active/`](modelserver/active-active). It pulls in the NVIDIA GPU + vLLM base and layers the `active-active` kustomize component, which overrides `KV_EVENTS_ENDPOINT` to `tcp://*:5556` and exposes container port 5556 so the schedulers can dial each pod.

```bash
kubectl apply -n ${NAMESPACE} -k guides/precise-prefix-cache-aware/modelserver/active-active/
```

For other accelerators (`amd`, `cpu`, `hpu`, `tpu-v6`, `tpu-v7`, `xpu`), copy [`modelserver/active-active/kustomization.yaml`](modelserver/active-active/kustomization.yaml) and change the `resources` path to point at the chosen base overlay.

## Verifying active-active

Confirm both replica pods are Ready and the Service load-balances across them:

```bash
kubectl get pods -n ${NAMESPACE} -l inferencepool=precise-prefix-cache-aware-epp
# NAME                                          READY   STATUS
# precise-prefix-cache-aware-epp-<hash>-aaaaa   3/3     Running
# precise-prefix-cache-aware-epp-<hash>-bbbbb   3/3     Running
```

`READY 3/3` means each pod has all three containers up (envoy + epp + tokenizer-uds). If only one pod ever reaches Ready while the other stays at `2/3`, the leader-election workaround failed — check that `--ha-enable-leader-election=false` made it onto the EPP container's args.

Send a test request to the Service ClusterIP — the same `${IP}` env var pattern from the [main verification section](README.md#verification) works here, since active-active uses the same `precise-prefix-cache-aware-epp` Service. Both replicas will appear as endpoints; the Service load-balances across them.

A second identical completion request through the same Service should bump `kvcache_index_lookup_hits_total` on the replica that handles it, confirming both replicas have built their index from the per-pod KV events.

## Tradeoffs vs. the default

Active-active costs you:
- **1 ZMQ socket per (replica × vLLM pod)** — with N replicas × M pods, that's N×M sockets across the cluster. Negligible at normal scales.
- **Duplicate index memory** — each replica maintains its own KV-block index. Real-world index size is small relative to pod memory.
- **A deploy-time constraint** — the standalone chart hardcodes `strategy: Recreate`, so rolling updates take both replicas down briefly. For rolling-friendly HA, deploy two separate releases and front them with a custom Service.

Stick with the single-replica default unless you actually need HA.
