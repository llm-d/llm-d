# Kueue-Based Replica Rebalancing

When several model deployments share one GPU budget, their HPAs scale
independently and none of them knows what the others are consuming. Two models
scaling up at the same time collectively ask for more GPUs than the budget
holds.

The [experimental replica rebalancer](../replica-rebalancing/README.md) solves
this *above* the HPA: a control loop reads a `ResourceQuota` and patches
`spec.maxReplicas` on annotated HPAs so the ceilings always sum to the budget.

This guide solves it *below* the HPA with [Kueue](https://kueue.sigs.k8s.io/).
With the `deployment` integration, every replica pod becomes its own Kueue
`Workload`, and Kueue holds a pod at a scheduling gate until GPU quota is free
for it. The HPA is never touched, so it needs no annotation and KEDA keeps sole
ownership of the HPA it generates. Each model gets a guaranteed floor of GPUs,
lends what it is not using to the others, and preempts to take its floor back
when demand returns.

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

1. Multiple inference pools in one namespace, from the
   [Multi-Inference Pool Setup guide](../multi-inference-pool/README.md).
2. An autoscaler per pool — either
   [KEDA + EPP Metrics](../keda-epp-queue/README.md) or
   [KEDA + WVA Metrics](../wva/README.md). Nothing in this guide changes that
   configuration.
3. The [experimental replica rebalancer](../replica-rebalancing/README.md) is
   uninstalled. It enforces the same GPU budget from the other side of the
   HPA, so the two must never run together. This guide assumes it is absent,
   along with the hard `requests.nvidia.com/gpu` `ResourceQuota` it reads — a
   gated pod still counts against a `ResourceQuota`, so one left in place would
   stop the ReplicaSet from creating the very pod Kueue is meant to queue.
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
export QUOTA_ROOT=${REPO_ROOT}/guides/workload-autoscaling/kueue-rebalancing/optimized-baseline
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
  -n ${KUEUE_NAMESPACE} --create-namespace --wait
```
<!-- guide:prerequisites.kueue end -->

> [!NOTE]
> On OpenShift, Kueue is also available as the Red Hat build of Kueue operator.
> Install it instead of the upstream chart; the quota objects in this guide are
> the same either way.

Confirm the controller is running and that the `pod` and `deployment`
integrations are enabled — they are on by default in 0.19.x, and without them
the queue-name label is inert and no pod is ever gated:

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
|---|---|---|
| [`resourceflavor.yaml`](optimized-baseline/base/resourceflavor.yaml) | `ResourceFlavor` | the accelerator pool being rationed. One empty flavor for homogeneous nodes; one per GPU type, with `nodeLabels`, otherwise |
| [`clusterqueues.yaml`](optimized-baseline/base/clusterqueues.yaml) | `ClusterQueue` ×2 | one per model. `nominalQuota` is that model's guaranteed floor; a shared `cohortName` makes the floors lendable |
| [`localqueues.yaml`](optimized-baseline/base/localqueues.yaml) | `LocalQueue` ×2 | the namespaced handle each Deployment points at. A pod can only name a LocalQueue, never a ClusterQueue |

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

Apply them:

<!-- guide:deploy.quota start -->
```bash
kubectl apply -k ${QUOTA_ROOT}/base
```
<!-- guide:deploy.quota end -->

## Step 3: Opt Each Deployment In

One label per Deployment, replacing the rebalancer's HPA annotation:

<!-- guide:deploy.optin start -->
```bash
for pair in "${DEPLOYMENT_A}:${QUEUE_A}" "${DEPLOYMENT_B}:${QUEUE_B}"; do
  deployment=${pair%%:*}; queue=${pair##*:}
  kubectl patch deployment "${deployment}" -n ${NAMESPACE} --type=merge -p \
    "{\"metadata\":{\"labels\":{\"kueue.x-k8s.io/queue-name\":\"${queue}\"}},\"spec\":{\"template\":{\"metadata\":{\"labels\":{\"kueue.x-k8s.io/queue-name\":\"${queue}\"}}}}}"
done
```
<!-- guide:deploy.optin end -->

The label on the Deployment is Kueue's documented opt-in surface, and its
webhook copies the label to the pod template; the patch sets it on the template
too, so the opt-in is explicit in the object rather than a webhook side effect.
Every replica the HPA creates inherits it and becomes its own Workload.
Deployments without the label are untouched by Kueue.

To make the opt-in part of the model server's own Kustomize overlay rather than
a live patch, add it there instead:

```yaml
labels:
  - pairs:
      kueue.x-k8s.io/queue-name: model-a-lq
    includeSelectors: false   # the pod selector must not change
    includeTemplates: true    # every replica pod needs the label
```

Finally, roll out with no surge. At full quota a surge pod has no GPU to claim,
so it is gated and the rollout waits behind it:

<!-- guide:deploy.rollout_strategy start -->
```bash
for deployment in ${DEPLOYMENT_A} ${DEPLOYMENT_B}; do
  kubectl patch deployment "${deployment}" -n ${NAMESPACE} --type=merge \
    -p '{"spec":{"strategy":{"rollingUpdate":{"maxSurge":0,"maxUnavailable":1}}}}'
done
```
<!-- guide:deploy.rollout_strategy end -->

## Step 4: Verify

`ClusterQueue` status is the whole picture — admitted GPUs per model, how many
of them are borrowed from the cohort, and how many workloads are waiting:

<!-- guide:verify.tests.queues start -->
```bash
kubectl get clusterqueues -o wide
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
```
<!-- guide:verify.tests.workloads end -->

A gated pod explains itself through its Workload — which queue it is in, and
which resource the cohort could not satisfy:

<!-- guide:verify.tests.gated start -->
```bash
for w in $(kubectl get workloads -n ${NAMESPACE} -o json \
  | jq -r '.items[] | select((.status.conditions // [])
           | any(.type=="QuotaReserved" and .status!="True")) | .metadata.name'); do
  kubectl get workload "$w" -n ${NAMESPACE} -o json \
    | jq -r '"workload: \(.metadata.name)  queue: \(.spec.queueName)",
             (.status.conditions[]? | "  \(.type)=\(.status) reason=\(.reason)\n    \(.message)")'
done
```
<!-- guide:verify.tests.gated end -->

```text
QuotaReserved=False  reason=Pending
  couldn't assign flavors to pod set main: insufficient unused quota for
  nvidia.com/gpu in flavor gpu-default, 1 more needed
```

Preemption is visible in namespace events:

```bash
kubectl get events -n ${NAMESPACE} --field-selector reason=Preempted
```

## How It Interacts With the HPA

| Layer | Actor | What it controls |
|---|---|---|
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

| Condition | Replica rebalancer | Kueue |
|---|---|---|
| Demand rises, budget full | lowers `spec.maxReplicas` on the next loop | extra pods are created and gated |
| Demand drops | raises `maxReplicas` back toward the manifest value | pods are deleted, quota is released within seconds |
| One model idle | its unused GPUs raise the other's ceiling | the other ClusterQueue borrows above its own floor |
| Idle model wakes up | waits for the next loop, no preemption | preempts a borrowed replica immediately |

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
sits on its floor and the surplus stays gated. That is exactly the
over-provisioning the replica rebalancer existed to prevent, except the surplus
queues instead of the cluster being oversubscribed.

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
  unschedulable pods rather than gated ones.
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
for deployment in ${DEPLOYMENT_A} ${DEPLOYMENT_B}; do
  kubectl patch deployment "${deployment}" -n ${NAMESPACE} --type=merge -p \
    '{"metadata":{"labels":{"kueue.x-k8s.io/queue-name":null}},"spec":{"template":{"metadata":{"labels":{"kueue.x-k8s.io/queue-name":null}}}}}'
done

kubectl delete -k ${QUOTA_ROOT}/base --ignore-not-found=true
```
<!-- guide:cleanup end -->

Removing the label stops Kueue managing pods created from then on; pods already
admitted keep running.
