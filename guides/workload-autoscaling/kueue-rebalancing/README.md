# Kueue-Based Replica Rebalancing

When several model deployments share one GPU budget, their HPAs scale
independently and none of them knows what the others are consuming. Two models
scaling up at the same time collectively ask for more GPUs than the budget
holds.

This guide solves that *below* the HPA with [Kueue](https://kueue.sigs.k8s.io/).
Every replica pod becomes its own Kueue `Workload`, and Kueue holds a pod at a
scheduling gate until GPU quota is free for it. The HPA is never touched, so it
needs no annotation and KEDA keeps sole ownership of the HPA it generates. Each
model gets a guaranteed floor of GPUs, lends what it is not using to the others,
and preempts to take its floor back when demand returns.

```text
      KEDA ScaledObject            KEDA ScaledObject
              │                            │
        HPA (model-a)                HPA (model-b)     ← untouched, unannotated
              │  replicas                  │  replicas
      Deployment model-a           Deployment model-b
              │  pods                      │  pods
      ┌───────┴───────────────────────────-┴───────┐
      │            Kueue admission                 │
      │  model-a-lq ─→ model-a-cq   floor 5 GPU    │
      │  model-b-lq ─→ model-b-cq   floor 5 GPU    │
      │            cohort llm-d-gpu (10 GPU)       │
      └────────────────────────────────────────────┘
         admitted pods → scheduled    over-budget pods → gated (Pending)
```

## Prerequisites

1. Two or more model server Deployments in one namespace. The
   [Multi-Inference Pool Setup guide](../multi-inference-pool/README.md) adds
   the second router and `InferencePool`; it does not add a second model server,
   so `DEPLOYMENT_B` below is a placeholder for whichever Deployment you run as
   the second model. With a single model server there is nothing to rebalance —
   one ClusterQueue would own the whole budget.
2. An autoscaler per pool — either
   [KEDA + EPP Metrics](../keda-epp/README.md) or
   [KEDA + WVA Metrics](../wva/README.md). Nothing in this guide changes that
   configuration.
3. No hard `requests.nvidia.com/gpu` `ResourceQuota` on the namespace. A gated
   pod still counts against a `ResourceQuota`, so one left in place would stop
   the ReplicaSet from creating the very pod Kueue is meant to queue. Kueue's
   `ClusterQueue` quotas replace it.
4. Every model pod declares explicit GPU requests and limits. This is what Kueue
   accounts against quota, so a pod without them is admitted for free:

   ```yaml
   resources:
     requests:
       nvidia.com/gpu: "1"
     limits:
       nvidia.com/gpu: "1"
   ```

5. `jq`, for the verification commands.

Set the guide environment variables:

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export NAMESPACE=llm-d-optimized-baseline
export KUEUE_NAMESPACE=kueue-system
export KUEUE_VERSION=0.19.1
export DEPLOYMENT_A=optimized-baseline-nvidia-gpu-vllm-decode
export DEPLOYMENT_B=model-b-nvidia-gpu-vllm-decode
export QUEUE_A=model-a-lq
export QUEUE_B=model-b-lq
export CQ_A=llm-d-model-a-cq
export CQ_B=llm-d-model-b-cq
export QUOTA_LABEL=app.kubernetes.io/part-of=llm-d-kueue-rebalancing
export GUIDE_ROOT=${REPO_ROOT}/guides/workload-autoscaling/kueue-rebalancing
export QUOTA_ROOT=${GUIDE_ROOT}/optimized-baseline
```
<!-- guide:env.static end -->

Source the common guide environment variables:

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

## Step 1: Install Kueue

<!-- guide:prerequisites.kueue start -->
```bash
helm install kueue oci://registry.k8s.io/kueue/charts/kueue \
  --version ${KUEUE_VERSION} \
  -n ${KUEUE_NAMESPACE} --create-namespace --wait \
  -f ${GUIDE_ROOT}/kueue.values.yaml
```
<!-- guide:prerequisites.kueue end -->

[`kueue.values.yaml`](kueue.values.yaml) exists to keep the install narrow. The
chart enables the `pod` and `deployment` integrations by default, and it derives
every webhook's `namespaceSelector` from `managedJobsNamespaceSelector` in the
manager config. Left at the default, `mpod.kb.io` intercepts pod creation in
*every* namespace except `kube-system` and the release namespace, with
`failurePolicy: Fail` — so a Kueue outage would block pod creation across the
cluster. Naming the model namespace confines both the webhooks and the
controller's reconciliation to it, and is the only edit the file makes:

```console
$ diff <(helm template ... ) <(helm template ... -f kueue.values.yaml)   # ConfigMap
> managedJobsNamespaceSelector:
>   matchExpressions:
>   - key: kubernetes.io/metadata.name
>     operator: In
>     values:
>     - llm-d-optimized-baseline
```

That single field narrows all 36 namespace-scoped webhooks in the chart, in both
webhook configurations. `managedJobsNamespaceSelector` lives inside the
`controllerManagerConfigYaml` *string*, which Helm replaces rather than merges,
so the file has to repeat the chart's defaults — re-diff it against
`helm show values` when you bump `KUEUE_VERSION`.

> [!NOTE]
> On OpenShift, install the Red Hat build of Kueue operator from OperatorHub
> instead of the upstream chart (it requires the cert-manager operator and
> installs into `openshift-kueue-operator`, not `kueue-system`). The quota
> objects in this guide are unchanged — Red Hat build of Kueue 1.3 and later
> serve `kueue.x-k8s.io/v1beta2` — but the setup differs in three ways:
> integrations are configured in a cluster-scoped `Kueue` CR
> (`kueue.openshift.io/v1`, `metadata.name: cluster`) at
> `spec.config.integrations.frameworks`, where the names are capitalised and the
> field is required with no default, so `Pod` and `Deployment` must be listed
> explicitly; verification reads `oc get kueue cluster` and the
> `kueue-manager-config` ConfigMap in `openshift-kueue-operator`; and the
> operator manages only namespaces labelled `kueue.openshift.io/managed=true`,
> so label the model namespace or nothing is ever gated. Follow the Red Hat
> build of Kueue documentation for the authoritative steps.

Confirm the controller is running and that the `pod` and `deployment`
integrations are enabled — the chart turns both on by default (the Kueue API's
own default is `batch/job` alone), and without them the queue-name label is
inert and no pod is ever gated:

<!-- guide:prerequisites.integrations start -->
```bash
kubectl wait --for=condition=Available -n ${KUEUE_NAMESPACE} \
  deploy/kueue-controller-manager --timeout=180s
kubectl get cm kueue-manager-config -n ${KUEUE_NAMESPACE} \
  -o jsonpath='{.data.controller_manager_config\.yaml}' \
  | grep -A 20 'integrations:'
```
<!-- guide:prerequisites.integrations end -->

## Step 2: Define the GPU Budget

Five objects across three files, in
[`kueue-rebalancing/optimized-baseline/base`](optimized-baseline/base/):

| File | Object | Role |
| --- | --- | --- |
| [`resourceflavor.yaml`](optimized-baseline/base/resourceflavor.yaml) | `ResourceFlavor` `llm-d-gpu-default` | the accelerator pool being rationed. One empty flavor for homogeneous nodes; one per GPU type, with `nodeLabels`, otherwise |
| [`clusterqueues.yaml`](optimized-baseline/base/clusterqueues.yaml) | `ClusterQueue` `llm-d-model-a-cq`, `llm-d-model-b-cq` | one per model. `nominalQuota` is that model's guaranteed floor; a shared `cohortName` makes the floors lendable |
| [`localqueues.yaml`](optimized-baseline/base/localqueues.yaml) | `LocalQueue` `model-a-lq`, `model-b-lq` | the namespaced handle each Deployment points at. A pod can only name a LocalQueue, never a ClusterQueue |

ClusterQueues and ResourceFlavors are cluster-scoped, hence the `llm-d-` prefix:
two teams both reaching for `model-a-cq` on one cluster would collide. The
kustomization stamps the namespace onto the LocalQueues and puts
`app.kubernetes.io/part-of: llm-d-kueue-rebalancing` on all five, so this
guide's queues can be selected out of a cluster that has others.

The defaults describe 10 GPUs split into two floors of 5, replacing a
`requests.nvidia.com/gpu: "10"` ResourceQuota. Before applying, set each
`nominalQuota` to your own GPU count:

```bash
kubectl describe nodes | grep nvidia.com/gpu
```

Floors should sum to the physical GPU count. Kueue accounting is nominal — quota
larger than the cluster admits pods the scheduler cannot place, which then sit
`Pending` as unschedulable instead of gated. Summing to less than capacity
simply leaves GPUs unused.

Apply them before the model servers start pointing at the queues, so the
LocalQueue exists when the first Workload appears:

<!-- guide:deploy.quota start -->
```bash
kubectl apply -k ${QUOTA_ROOT}/base
```
<!-- guide:deploy.quota end -->

## Step 3: Opt Each Deployment In

A model server joins the budget by carrying `kueue.x-k8s.io/queue-name`. Where
that label goes decides which Kueue integration manages it, and — because Kueue
validates the Deployment-level label as immutable — whether you can add it to a
Deployment that is already running.

**At install time (preferred).** If you are about to create the model server, put
the label on the Deployment itself and let the `deployment` integration own it.
The model servers in these guides are plain kustomize overlays, so this is a
patch in the model server's own kustomization, for example in
`guides/optimized-baseline/modelserver/gpu/vllm/base/kustomization.yaml`:

```yaml
patches:
  - target:
      group: apps
      version: v1
      kind: Deployment
      name: decode          # the pre-namePrefix name in that overlay
    patch: |-
      apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: unused
        labels:
          kueue.x-k8s.io/queue-name: model-a-lq
      spec:
        strategy:
          rollingUpdate:
            maxSurge: 0     # see below
            maxUnavailable: 1
        template:
          metadata:
            labels:
              kueue.x-k8s.io/queue-name: model-a-lq
```

The label must not reach `spec.selector.matchLabels`, which is immutable after
create — so if you use a kustomize `labels:` transformer instead of a patch, it
has to be a second entry with `includeSelectors: false` and
`includeTemplates: true`, never folded into an existing entry that sets
selectors.

**On a Deployment that is already serving.** The Deployment-level label cannot
be added retroactively. Kueue's `vdeployment` webhook enforces it as immutable
whenever `status.readyReplicas > 0`, and rejects removing it at any replica
count:

```console
$ kubectl patch deployment ${DEPLOYMENT_A} -n ${NAMESPACE} --type=merge \
    -p '{"metadata":{"labels":{"kueue.x-k8s.io/queue-name":"model-a-lq"}}}'
Error from server (Forbidden): admission webhook "vdeployment.kb.io" denied the
request: metadata.labels[kueue.x-k8s.io/queue-name]: Invalid value: "model-a-lq":
field is immutable
```

Label the **pod template** instead. Each replica pod is then admitted, gated and
preempted individually by the `pod` integration — same quota, same borrowing,
same preemption — and the label can be patched back out later, which the
Deployment-level one cannot.

First set the rollout strategy, before anything touches the pod template:

<!-- guide:deploy.rollout_strategy start -->
```bash
kubectl patch deployment ${DEPLOYMENT_A} ${DEPLOYMENT_B} -n ${NAMESPACE} --type=merge \
  -p 'spec: {strategy: {rollingUpdate: {maxSurge: 0, maxUnavailable: 1}}}'
```
<!-- guide:deploy.rollout_strategy end -->

At `replicas: 1` the default 25% strategy rounds to `maxSurge: 1`,
`maxUnavailable: 0` — strictly create-before-delete. Once the budget is fully
admitted the surge pod has no quota to claim, so Kueue gates it and the rollout
waits behind a pod that can never start:

```text
NAME                     PHASE     GATES
model-a-...-ds6cq        Pending   kueue.x-k8s.io/admission,kueue.x-k8s.io/topology
```

`maxSurge: 0` inverts that into terminate-then-create, which frees the GPU the
replacement needs. Patching `spec.strategy` does not touch the pod template, so
this step rolls nothing by itself — the alternative is to keep one replica of
slack in the budget instead.

Then opt in:

<!-- guide:deploy.opt-in start -->
```bash
kubectl patch deployment ${DEPLOYMENT_A} -n ${NAMESPACE} --type=merge \
  -p "spec: {template: {metadata: {labels: {kueue.x-k8s.io/queue-name: ${QUEUE_A}}}}}"
kubectl rollout status deployment/${DEPLOYMENT_A} -n ${NAMESPACE} --timeout=10m

kubectl patch deployment ${DEPLOYMENT_B} -n ${NAMESPACE} --type=merge \
  -p "spec: {template: {metadata: {labels: {kueue.x-k8s.io/queue-name: ${QUEUE_B}}}}}"
kubectl rollout status deployment/${DEPLOYMENT_B} -n ${NAMESPACE} --timeout=10m
```
<!-- guide:deploy.opt-in end -->

This does change the pod template, so every model server restarts once, one
replica at a time. Every replica the HPA creates from then on inherits the label
and becomes its own Workload. Deployments without the label are untouched by
Kueue.

One patch and one rollout wait per model, written out: with a third model, copy
the pair and point it at that Deployment and its `LocalQueue`.

## Step 4: Verify

Assert first that a controller is reconciling these queues and that the cohort
survived the apply. Without an `Active` condition nothing is gated and every
command below still exits 0; without a cohort the floors cannot be lent or
borrowed, which is the whole point:

<!-- guide:verify.tests.admission start -->
```bash
kubectl get clusterqueues -l ${QUOTA_LABEL} -o custom-columns='NAME:.metadata.name,ACTIVE:.status.conditions[?(@.type=="Active")].status,COHORT:.spec.cohortName'

kubectl get clusterqueues -l ${QUOTA_LABEL} -o json | jq -e '
  [.items[]
   | select(any(.status.conditions[]?; .type == "Active" and .status == "True"))
   | select((.spec.cohortName // "") != "")] | length == 2' > /dev/null \
  || { echo "expected 2 Active ClusterQueues sharing a cohort - is the Kueue controller running?" >&2; false; }
```
<!-- guide:verify.tests.admission end -->

`length == 2` is the number of models this guide budgets for — raise it if you
add a `ClusterQueue`.

`ClusterQueue` status is the whole picture — admitted GPUs per model, how many
of them are borrowed from the cohort, and how many workloads are waiting:

<!-- guide:verify.tests.queues start -->
```bash
kubectl get clusterqueues -l ${QUOTA_LABEL} -o wide
kubectl get localqueues -n ${NAMESPACE}
```
<!-- guide:verify.tests.queues end -->

There is one Workload per replica pod. Pods beyond the budget stay `Pending`
behind a scheduling gate instead of being scheduled onto GPUs that do not
exist:

<!-- guide:verify.tests.workloads start -->
```bash
kubectl get workloads -n ${NAMESPACE}
kubectl get pods -n ${NAMESPACE} \
  -l kueue.x-k8s.io/queue-name --show-labels
kubectl get pods -n ${NAMESPACE} -o json \
  | jq -r '.items[] | select((.spec.schedulingGates // []) | length > 0)
           | "gated: \(.metadata.name) \([.spec.schedulingGates[].name] | join(","))"'
echo "workloads: $(kubectl get workloads -n ${NAMESPACE} --no-headers 2>/dev/null | wc -l | tr -d ' ')"
```
<!-- guide:verify.tests.workloads end -->

A gated pod explains itself through its Workload — which queue it is in, and
which resource the cohort could not satisfy:

<!-- guide:verify.tests.gated start -->
```bash
kubectl get workloads -n ${NAMESPACE} -o json | jq -r '
  .items[]
  | select(any(.status.conditions[]?; .type == "QuotaReserved" and .status != "True"))
  | "workload: \(.metadata.name)  queue: \(.spec.queueName)",
    (.status.conditions[]? | "  \(.type)=\(.status) reason=\(.reason)\n    \(.message)")'
```
<!-- guide:verify.tests.gated end -->

```text
QuotaReserved=False  reason=Pending
  couldn't assign flavors to pod set main: insufficient unused quota for
  nvidia.com/gpu in flavor llm-d-gpu-default, 1 more needed
```

Preemption is visible in namespace events, and names both sides of the cohort:

```bash
kubectl get events -n ${NAMESPACE} --field-selector reason=Preempted
```

```text
Preempted to accommodate a workload ... due to reclamation within the cohort;
preemptor path: /llm-d-gpu/llm-d-model-b-cq;
preemptee path: /llm-d-gpu/llm-d-model-a-cq
```

## How It Interacts With the HPA

| Layer | Actor | What it controls |
| --- | --- | --- |
| Demand signal | Prometheus / EPP metrics | when to scale |
| Replica count | HPA (owned by KEDA) | how many replicas are requested |
| GPU budget | Kueue | which of those replicas get a GPU now |

The HPA still makes every scaling decision. Kueue admits the resulting pods in
budget order, so `maxReplicas` becomes a physical ceiling that nothing rewrites.

Two consequences worth planning for:

- **Desired and ready replicas legitimately differ.** Alerts on
  "replicas != desired" now fire by design. Retarget them at
  `kueue_pending_workloads`.
- **A preempted replica is a deleted serving pod.** Check that
  `terminationGracePeriodSeconds` is long enough to drain in-flight requests.

KEDA's `AverageValue` triggers divide an aggregate metric by the per-replica
target rather than by live pod count, so gated pods do not skew the HPA's
arithmetic. Re-check this if you switch a trigger to `Utilization`.

## Perceived Effects

| Condition | Kueue |
| --- | --- |
| Demand rises, budget full | extra pods are created and gated |
| Demand drops | pods are deleted, quota is released within seconds |
| One model idle | the other ClusterQueue borrows above its own floor |
| Idle model wakes up | preempts a borrowed replica immediately |

## Why Contention Settles on the Floors

The rule Kueue enforces is: **you may preempt to reach your own floor, never to
go beyond it.** Above your floor you can only take what is idle. Two separate
settings produce that:

- `reclaimWithinCohort: Any` applies only when the incoming pod fits inside its
  own `nominalQuota`. That is what lets a model evict the borrowed replicas of a
  peer to get back to its own floor.
- `borrowWithinCohort` governs preemption *by* a pod that must itself borrow. It
  defaults to `Never` and this guide leaves it there, so a borrowing pod never
  evicts anyone — it waits for someone to go idle.

So when both models want more than their floor at once, and the floors already
sum to capacity, there is by definition nothing idle left to borrow: each model
sits on its floor and the surplus stays gated — the surplus queues instead of
the cluster being oversubscribed.

## Tuning

The floors are the dial. Beyond them:

- **Asymmetric floors** — a 7/3 split guarantees one model more capacity under
  contention while both still borrow freely when the other is idle.
- **`lendingLimit: 0`** on a latency-critical model's GPU quota means peers can
  never borrow its floor, so it scales out without waiting for a preemption
  round trip.
- **`WorkloadPriorityClass`** (for example `prod: 1000`, `dev: 100`) plus the
  `kueue.x-k8s.io/priority-class` label on the pod template chooses *which*
  replicas get evicted, instead of newest-first.
- **Fair sharing instead of floors** — put the whole budget on a `Cohort`
  object, give each ClusterQueue `nominalQuota: 0` and a
  `fairSharing.weight`, and enable `fairSharing` in the Kueue manager config.
  Fully elastic, with no guaranteed floor.

## Limitations

- **Nominal accounting.** Kueue admits against the quota you declare, not
  against live node capacity. Quota above physical capacity produces
  unschedulable pods rather than gated ones — a `FailedScheduling` event
  instead of an admission gate.
- **The queue-name label is one-way on a Deployment.** Kueue rejects removing
  `metadata.labels[kueue.x-k8s.io/queue-name]` from a Deployment at any replica
  count, and rejects changing it while any replica is ready. Opting a Deployment
  back out means recreating it. The pod-template label used above has no such
  restriction, which is why the steps use it.
- **Gated pods accumulate.** Keep `maxReplicaCount` at a sane physical ceiling;
  every replica the HPA asks for and cannot place stays as a `Pending` pod.
- **Rollouts need slack.** With `maxSurge: 0` a rollout terminates before it
  creates, which is slower; the alternative is to leave one replica of budget
  free.
- **Same-namespace routing needs one LocalQueue per model.** ClusterQueue
  `namespaceSelector` cannot distinguish two models in one namespace.

## Cleanup

<!-- guide:cleanup start -->
```bash
kubectl patch deployment ${DEPLOYMENT_A} ${DEPLOYMENT_B} -n ${NAMESPACE} --type=merge \
  -p 'spec: {template: {metadata: {labels: {kueue.x-k8s.io/queue-name: null}}}}'
kubectl rollout status deployment/${DEPLOYMENT_A} -n ${NAMESPACE} --timeout=10m
kubectl rollout status deployment/${DEPLOYMENT_B} -n ${NAMESPACE} --timeout=10m

kubectl delete -k ${QUOTA_ROOT}/base --ignore-not-found=true
```
<!-- guide:cleanup end -->

The first patch rolls every model server once more, one replica at a time; pods
created after it are invisible to Kueue. It has to come first, so that no
replica is left gated with no ClusterQueue to admit it and so the Workloads
holding the `kueue.x-k8s.io/resource-in-use` finalizer are gone before the
delete blocks on it.

If you also labelled a Deployment at install time, this does not opt it back
out — that label cannot be removed, so recreate the Deployment without it.
`helm uninstall kueue -n ${KUEUE_NAMESPACE}` removes the webhook enforcing the
rule, after which the leftover label is inert.
