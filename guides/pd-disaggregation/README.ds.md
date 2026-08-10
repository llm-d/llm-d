# P/D Disaggregation with DisaggregatedSet

This guide deploys the same `openai/gpt-oss-120b` prefill-decode disaggregated stack as the main guide, but manages the whole P/D topology with a single [DisaggregatedSet](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) resource from [LeaderWorkerSet (LWS)](https://lws.sigs.k8s.io/) instead of two independent Deployments.

For a comprehensive overview of P/D disaggregation architecture, best practices, and benchmarking, please refer to the **[Unified P/D Disaggregation Guide](./README.md)**.

## Why DisaggregatedSet?

The Deployment-based variant of this guide manages prefill and decode as two unrelated workloads: nothing ties their versions together, rollouts are uncoordinated, and running several identical copies of the topology means duplicating manifests. DisaggregatedSet addresses this by making the P/D topology a first-class API object:

* **One declarative unit** — the prefill and decode roles (replicas, rollout strategy, and pod template each) live in a single custom resource. Each role maps to a managed LeaderWorkerSet, so all LWS capabilities (multi-host groups, subgroup policies, exclusive placement) remain available per role.
* **Coordinated rollouts** — a template change rolls prefill and decode as one version-synchronized unit, preserving your xPyD ratio throughout the update instead of letting two Deployments drift.
* **Slices** — `spec.slices` replicates the entire role topology into N independent copies. Each slice is a complete prefill+decode set with its own rollout clock and a stable identity (`disaggregatedset.x-k8s.io/slice` label). Changing `slices` is a pure scale operation: it never re-rolls existing slices.
* **Placement policy** — `spec.placementPolicy` builds on the slice identity to confine each complete P/D copy to a single topology domain (a rack, NVLink domain, or zone) and spread slices across domains, so KV-cache handoff stays within a low-latency domain.

### Topology in this guide

Instead of one flat 8-prefill/2-decode pool, this variant deploys **2 slices**, each a complete copy of the topology:

* Per slice: 4 TP=1 Prefill instances + 1 TP=4 Decode instance
* Total: 8 TP=1 Prefill + 2 TP=4 Decode — the same aggregate capacity (16 GPUs) as the [main guide](./README.md#overview)

Each slice can be rolled, recovered, or (with placement policy) pinned to an accelerator domain independently of the other.

> [!NOTE]
> Slices are a deployment-topology construct: they govern rollout, scale, and placement. The llm-d Router (EPP) still discovers all prefill and decode pods by label as flat pools and may pair a prefill in one slice with a decode in another. If you use placement policy to confine slices to separate network domains, make sure cross-domain NIXL transfers remain possible on your fabric.

## Prerequisites

### 1. LWS Controller with DisaggregatedSet

This variant requires the LWS controller manager with the DisaggregatedSet API. The `slices` field used here was added after LWS `v0.9.0` — install `v0.10.0` or newer (see the [LWS installation guide](https://lws.sigs.k8s.io/docs/installation/#disaggregatedset) for the current release and options):

```bash
export LWS_CHART_VERSION=0.10.0
helm install lws oci://registry.k8s.io/lws/charts/lws \
  --version=${LWS_CHART_VERSION} \
  --namespace lws-system \
  --create-namespace \
  --set enableDisaggregatedSet=true \
  --wait --timeout 300s
```

Verify the controller is available and the CRD is registered:

```bash
kubectl wait deploy/lws-controller-manager -n lws-system --for=condition=available --timeout=5m
kubectl get crd disaggregatedsets.disaggregatedset.x-k8s.io
```

### 2. Cluster and Repo Setup

Complete the **[Prerequisites](./README.md#prerequisites)** section of the main guide: client tools, repo checkout, environment variables, Gateway API Inference Extension CRDs, target namespace, and the `llm-d-hf-token` secret. The same environment variables apply:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
export GUIDE_NAME="pd-disaggregation"
export NAMESPACE="llm-d-pd-disaggregation"
export MODEL_NAME="openai/gpt-oss-120b"
```

## Installation Instructions

### 1. Deploy the llm-d Router

Deploy the router in either Standalone or Gateway mode by following the exact instructions in the **[Deploy the llm-d Router](./README.md#1-deploy-the-llm-d-router)** section of the main guide. Nothing changes here: the EPP discovers model server pods by label (`llm-d.ai/guide` and `llm-d.ai/role`), which the DisaggregatedSet's pod templates carry, so routing is identical regardless of which workload controller manages the pods.

### 2. Deploy the Model Server

Apply the DisaggregatedSet overlay:

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm-ds
```

This creates one `DisaggregatedSet` named `pd-disagg`. The controller fans it out into one LeaderWorkerSet per `(slice, role)`, named `<ds>-<slice>-<revision>-<role>`:

```bash
kubectl get leaderworkerset -n ${NAMESPACE} -l disaggregatedset.x-k8s.io/name=pd-disagg

# NAME                           READY   DESIRED   UP-TO-DATE   AGE
# pd-disagg-0-785b966c-decode    1       1         1            2m
# pd-disagg-0-785b966c-prefill   4       4         4            2m
# pd-disagg-1-785b966c-decode    1       1         1            2m
# pd-disagg-1-785b966c-prefill   4       4         4            2m
```

> [!NOTE]
> The revision hash (`785b966c` above) is generated dynamically and changes on each rollout. Always query child objects through labels (`disaggregatedset.x-k8s.io/name`, `.../slice`, `.../role`) rather than hardcoded names.

Check readiness per slice and role through the child LeaderWorkerSets, e.g. for slice 0:

```bash
kubectl get leaderworkerset -n ${NAMESPACE} \
  -l disaggregatedset.x-k8s.io/name=pd-disagg,disaggregatedset.x-k8s.io/slice=0
```

### 3. Enable Monitoring (optional)

Follow the [monitoring section of the main guide](./README.md#3-enable-monitoring-optional) — the model server pods carry the same labels as the Deployment-based variant, so the same monitoring resources apply.

## Operating the DisaggregatedSet

Day-to-day mechanics (scaling slices, scaling role replicas, rolling updates, placement policy) are DisaggregatedSet features rather than anything llm-d specific, and are documented in the [LWS DisaggregatedSet docs](https://lws.sigs.k8s.io/docs/examples/disaggregatedset/). The notes that matter for this guide:

* **Scaling `slices` adds or removes complete P/D copies** (4 prefill + 1 decode each) at the current revision, without touching existing slices.
* **Per-role `replicas` applies per slice**, so your xPyD ratio (see [P/D Best Practices](./README.md#pd-best-practices)) is defined once and holds in every slice. With 2 slices, raising prefill replicas from 4 to 6 yields 12 prefill instances in total.
* **Rolling updates proceed independently per slice**, always keeping a complete same-version P/D set serving in each slice. A stuck slice degrades only itself.
* **Placement policy pins each complete P/D copy to one topology domain.** Use the node-label key that identifies your low-latency domain (for example a rack or NVLink-domain label on NVL72-class systems) so prefill-to-decode KV-cache transfer stays inside the domain. A commented example ships in `modelserver/gpu/vllm-ds/disaggregatedset.yaml`.

> [!NOTE]
> `spec.placementPolicy` is merged upstream ([lws#916](https://github.com/kubernetes-sigs/lws/pull/916), [KEP-848](https://github.com/kubernetes-sigs/lws/tree/main/keps/848-disaggregatedset-placement-policy)) and, like `slices`, ships in the next LWS release after `v0.9.0`. Controllers at `v0.9.0` or older reject the field.

## Verification

Follow the **[Verification steps in the main guide](./README.md#verification)** to retrieve the proxy IP and send a test request — the model name and request are identical:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "openai/gpt-oss-120b",
        "prompt": "How are you today?"
    }' | jq
```

You can confirm both roles participate by checking pod logs per role:

```bash
kubectl logs -n ${NAMESPACE} -l llm-d.ai/role=prefill --tail=10
kubectl logs -n ${NAMESPACE} -l llm-d.ai/role=decode --tail=10
```

## Benchmarking

The deployed stack has the same aggregate topology, model, and endpoint as the Deployment-based variant, so the **[Benchmarking section of the main guide](./README.md#benchmarking)** applies verbatim — install `llmdbenchmark`, resolve the endpoint, and run the `guide_pd-disaggregation_1.yaml` workload profile.

## Cleanup

Remove the router:

```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
```

Remove the model server. Deleting the DisaggregatedSet cascades to all child LeaderWorkerSets, their pods, and per-slice Services:

```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm-ds
```

## Known Issues

Testing this guide surfaced the following upstream issues in the DisaggregatedSet controller (being filed against [kubernetes-sigs/lws](https://github.com/kubernetes-sigs/lws)). None affect the deploy, slice scaling, or serving paths above.

1. **Rolling update can drain the old revision immediately when replicas change with the template.** If a single apply changes a role's pod template *and* its `replicas`, the planner can scale the old revision to zero before any new pod is ready, causing an outage. Until fixed, change the template and replica counts in **separate applies**.
2. **Rolling updates can stall on fully allocated clusters.** With `maxSurge: 0`, the controller creates new pods before draining their old counterparts, then waits for them to become ready. On a cluster with no spare accelerators the new pods stay Pending and the rollout never progresses. Until fixed, ensure at least one decode's worth of free accelerators before a template change (for example by scaling `slices` down by one first), or unstick a stalled rollout by scaling the old revision's LeaderWorkerSet down manually.
3. **`status.roleStatuses` is not yet populated.** The API defines it, but the controller does not write status today. Use the label-based LeaderWorkerSet queries shown in this guide instead.

## Further Reading

* [DisaggregatedSet concepts](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) and [API reference](https://lws.sigs.k8s.io/docs/reference/disaggregatedset.v1/)
* [KEP-766: DisaggregatedSet](https://github.com/kubernetes-sigs/lws/tree/main/keps/766-DisaggregatedSet) — the coordinated multi-role rollout design
* [KEP-846: DisaggregatedSet Slices](https://github.com/kubernetes-sigs/lws/tree/main/keps/846-disaggregatedset-slices) — slice semantics, naming, and scale behavior
