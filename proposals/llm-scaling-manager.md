# llm-scaling-manager: fleet-wide autoscaling for llm-d

**Authors**: Evgeny Shindin (_IBM_)

**Status**: released as v0.10.0 and v0.10.1 (signed images); v0.10.2 is in
release candidate. Code and releases:
[ev-shindin/llm-scaling-manager](https://github.com/ev-shindin/llm-scaling-manager).

## Summary

llm-scaling-manager autoscales an llm-d fleet as a whole. One controller
decides the replica count of every model, across namespaces and accelerator
types, inside the GPUs the cluster actually has or the quotas each team was
given. When models peak at different times, it can share one GPU budget
between them and move GPUs to whichever model needs them now, and a warm pool
of loaded GPUs serves any model while its new replica starts.

To compare models and accelerators, it sizes each one from what a replica can
serve for the requests arriving now, instead of from a threshold on a raw
metric. It plugs into llm-d's existing KEDA as an external scaler: llm-d keeps
scaling through KEDA, and only the source of the replica counts changes.

It started as a fork of llm-d's Workload Variant Autoscaler
(`llm-d/llm-d-workload-variant-autoscaler`, still the module path in `go.mod`)
and has been developed separately since.

We propose moving the project into llm-d: the repository moves to the llm-d
GitHub organization and is maintained there as an llm-d autoscaling component.

Terms used below:

- **Variant**: one ScaledObject and the Deployment it scales. One model can run
  as several variants, for example on two accelerator types, or as separate
  prefill and decode Deployments.
- **Budget**: the GPUs the controller may use: the cluster's real inventory, a
  declared quota per namespace or cluster, or a quota read from Kueue.
- **Request shape**: the input and output token lengths of the requests.

## Motivation

llm-d autoscales today with one KEDA ScaledObject per Deployment, each deciding
alone on the router's metrics. That works for one model on dedicated GPUs. A
real cluster serves many models from teams in different namespaces, on more
than one accelerator type, from a limited number of GPUs:

- **Nothing sees the whole fleet.** Each ScaledObject scales its own Deployment.
  None of them can scale one model down to make room for another, or decide
  which model gets the last free GPUs.
- **Autoscalers ask for GPUs that are not there.** A scale-up past the cluster's
  GPUs or a team's quota leaves Pods pending, and the autoscaler keeps counting
  on them.
- **Static quotas waste GPUs.** Models that peak at different times each need a
  quota sized for their own peak, and that capacity sits idle the rest of the
  time.
- **With two accelerator types, someone has to choose.** Which Deployment of a
  model should grow is a cost decision that a per-Deployment threshold cannot
  make.
- **Raw metrics cannot be compared across models.** The cost of a request
  depends on its shape. At a constant 6 req/s where only the shape changed, one
  model had to grow from two replicas to three while KV-cache occupancy pointed
  at one ([run](https://github.com/ev-shindin/llm-scaling-manager/blob/main/docs/well-lit-paths/pd-disaggregation/measured.md)).
  A fleet-wide decision needs a demand figure that means the same thing for
  every model.

### Goals

- Decide all models and variants together, each cycle, across namespaces.
- Never ask for more GPUs than the budget allows, and say which model was held
  back and why.
- Share one budget between models that peak at different times, and share
  loaded GPUs between models to cover replica start time.
- Choose between accelerator types by cost per unit of capacity.
- Size every model from what one replica serves at the current request shape.
- Act only through KEDA.

### Non-Goals

- Node provisioning (the cluster autoscaler), admission and tenant arbitration
  (Kueue), and request routing (the gateway).
- Enforcing a quota. The controller stays inside its budget; a GPU
  `ResourceQuota` or Kueue is what stops Pods.
- SLA-target-driven scaling, scaling ahead of planned maintenance, and replacing
  failed replicas. None of these is built.

## Proposal

### One decision for the whole fleet

Each cycle the controller solves every model and variant it manages together.
It computes each one's demand (below), then allocates replicas across the
fleet. With no budget declared, every model gets what it needs. With a budget,
GPUs are split by each variant's priority, which comes from its policy tier
([workload classes](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/workload-classes)).
Every variant held back is recorded, in the `wva_decisions_limited_total` metric
and a `ResourceConstrained` Event.

### Limited resources

A budget comes from one of three sources, and they can be combined:

- **Real GPUs.** The controller counts the cluster's accelerators and never
  orders a replica that cannot be placed
  ([bound by GPUs](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/bound-by-gpus)).
- **Declared quotas,** per accelerator type, for the whole cluster or per
  namespace, so one team's autoscaling cannot spend the fleet
  ([tenant quotas](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/tenant-gpu-quotas)).
- **Kueue.** The quota is read from Kueue, so there is no second copy. Where it
  is safe, it includes what the namespace's ClusterQueue can borrow from its
  cohort
  ([Kueue quotas](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/kueue-bounded-quotas)).

### Heterogeneous accelerators

Deploy one model on two accelerator types or two tensor-parallel widths, give
each Deployment the same `modelID` and its price per replica (`variantCost`),
and the controller grows the variant that is cheaper per unit of capacity
([accelerator variants](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/accelerator-variants)).
The budget is counted per accelerator type.

### Multiple namespaces

One cluster-wide install manages every llm-d namespace. Where the controller
may not read the whole cluster, one namespace-scoped install manages a list of
namespaces, with a Role in each, under one budget
([guide](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/guides/utilization-share-namespaced)).

### Sharing one budget (utilization share)

For GPUs that are paid for whether used or not, an experimental optimizer
shares the budget on purpose instead of first come, first served. Each model
gets what it needs now; the spare GPUs are held as headroom, split between
models by weight; when demand shifts, it scales one model down so another can
scale up. The budget can be declared or read from Kueue, and can span
namespaces
([utilization share](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/utilization-share)).

### Sharing loaded GPUs between models (warm and retained pools)

A new replica takes about 41 s to start for an 8B model and 192 s for
GLM-5.2-FP8. A warm pool covers that for every model at once: its Pods hold a
GPU with models already loaded, and lend it to whichever model is scaling up
until that model's own replica is ready. A pool Pod switches models in about
437 ms ([warm pool](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/warm-pool-bridge)). For models too large to start on
demand, a retained pool keeps several of them on one set of GPUs, one awake at
a time (experimental,
[retained pool](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/retained-pool)).

### Sizing each model

Fleet decisions need a demand figure that compares across models. For each
model, and for prefill and decode separately, the minimum replica count is the
arrival rate divided by the rate one replica completed requests the last time
it was saturated. That rate depends on the request shape, so the count follows
the shape without re-tuning. The fleet then scales up above 0.85 utilization and
down below 0.70 (defaults in `scaling-policy-configmap.yaml`).

### Also included

- [Scale to and from zero](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/scale-to-zero).
- Prefill and decode scaled apart for
  [P/D-disaggregated](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/pd-disaggregation)
  models.

### What changes in llm-d

The controller is installed beside KEDA, and each model's ScaledObject points at
it as an external scaler. KEDA keeps owning the HPA. Nothing in the model
servers, the gateway or the EPP (llm-d's router) changes.

### Measured results

All runs on H200 nodes unless noted. Each link leads to the full run.

Sharing one budget between two 8B models
([measured](https://github.com/ev-shindin/llm-scaling-manager/blob/main/docs/well-lit-paths/utilization-share/measured.md)):

| Result | Measured |
| --- | --- |
| Model that bursts first | median TTFT 0.07 s vs 25.6 s with sharing planned but not acted on; the second model no worse |
| Both models P/D, 15 000-token prompts | p95 0.090 s vs 51 s and 6.0 s vs 96 s; a repeat agreed |
| Cost | 39 % and 43 % more GPU-seconds, held as spare capacity on purpose |

Sizing each model
([P/D path](https://github.com/ev-shindin/llm-scaling-manager/blob/main/docs/well-lit-paths/pd-disaggregation/measured.md)):

| Result | Measured |
| --- | --- |
| Shape change caught with no queue | third replica ordered about 4 min after the change; p95 TTFT 0.04–0.08 s after the first 5 min |
| Same window sized from KV occupancy | p95 TTFT 4.2 s |
| Order comes before the queue | second replica ordered at +53 s, before the first replica began preempting |
| Prefill and decode scale apart | decode 1 → 2 → 3, prefill never ordered |

Warm pool
([measured](https://github.com/ev-shindin/llm-scaling-manager/blob/main/docs/well-lit-paths/warm-pool-bridge/measured.md)):

| Result | Measured |
| --- | --- |
| p95 TTFT during a rise | 5.2–8.8 s without the pool, 0.11–0.83 s with it |
| Against a fixed minimum replica count | 12 % fewer GPU-seconds (14 138 vs 16 080), within 50 ms of its latency on three of four rises |

Caveats: the sharing and warm-pool results come from one or two runs each, on
1 GPU per replica. Sharing with whole-node or multi-GPU Pods and a cluster-wide
quota is not yet benchmarked. The sizing run used a small model (Qwen3-0.6B).

## Design Details

### Maturity

- **Releases.** v0.10.0 (after four release candidates) and v0.10.1, with
  v0.10.2 in release candidate. Images are multi-arch and signed keylessly with
  cosign from the release workflow. Fixes reach a release branch by
  cherry-pick, and each release lists its fixes and upgrade steps.
- **Tests on every pull request.** Lint, unit and envtest suites, docs and link
  checks, checks of the install scripts, and kind end-to-end suites with the
  llm-d inference simulator: smoke, full (98 specs) and Kueue borrowing.
- **Clusters.** Verified on GPU clusters, on Kubernetes and on OpenShift.
  Benchmarks run on H200 nodes with a benchmark suite kept in the repository,
  so every result can be rerun.
- **KEDA.** 2.18 to 2.21 supported, 2.21 recommended and tested in CI. On
  OpenShift, the Custom Metrics Autoscaler operator.
- **Process.** Every commit is signed off (DCO).

### What we have, and how it is tested

Each feature is documented as a well-lit path: what it gives, what it costs,
when not to use it, and a _How it is tested_ section naming its suites and
what they do not cover. "End-to-end" means the kind suites above.

| Feature | Status | How it is tested |
| --- | --- | --- |
| [Bound by GPUs](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/bound-by-gpus) | Stable | end-to-end suites |
| [Tenant quotas](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/tenant-gpu-quotas) | Stable (control path) | unit tests; end-to-end specs for the warm pool's refusal path, a Kueue-bounded quota and sharing under a spent quota. No end-to-end spec bounds the default optimizer |
| [Kueue quotas](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/kueue-bounded-quotas) | Experimental | unit and API-server tests; a live Kueue end-to-end suite in CI |
| [Sharing a budget](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/utilization-share) | Experimental | unit and end-to-end tests; one or two benchmark runs per configuration |
| [Accelerator variants](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/accelerator-variants) | Stable (control path) | which variant grows is asserted by unit specs only; one benchmark run on one GPU type |
| [Workload classes](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/workload-classes) | Stable | end-to-end suite, simulator only |
| [Scale on saturation](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/scale-on-saturation) | Stable | end-to-end suites; a July 2026 study of five scenarios against a KEDA threshold, one run each, on an older controller |
| [Scale to zero](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/scale-to-zero) | Stable | end-to-end suites; verified on a GPU cluster |
| [Warm pool](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/warm-pool-bridge) | Stable | end-to-end suites; one benchmark run per arm |
| [Retained pool](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/retained-pool) | Experimental | unit tests, one end-to-end spec (simulator only), a benchmark scenario |
| [P/D disaggregation](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/well-lit-paths/pd-disaggregation) | Experimental | benchmark run twice |

Moving GPUs between models by priority (`enableRescale`) is off by default and
unit-tested only.

### KEDA scale-down

The HPA that KEDA creates scales to the highest count reported during its
scale-down window, so one noisy reading would hold replicas for the whole
window. The controller therefore keeps a scale-down it has reported until
utilization would actually call for more (`WVA_STICKY_SCALE_DOWN`, on by
default).

### Known limits

- The budget is advisory: it stops the controller asking for GPUs, not Pods
  from taking them. Use a GPU `ResourceQuota` or Kueue as well.
- Choosing between accelerator types is only as good as the `variantCost`
  given to each.
- A new replica helps only if the router sends it traffic. With llm-d's default
  scoring, prefix-cache hits outweigh load, so with a shared-prefix dataset the
  busiest replica did 99.0 % of the work and each added one 0.3–2.0 %. A bug
  report is drafted in
  [upstream/](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/upstream).
- A request shape must saturate a replica once before its rate is known.
- It does not forecast; it reacts to current measurements.

Design detail:
[concepts/](https://github.com/ev-shindin/llm-scaling-manager/tree/main/docs/concepts).

## Alternatives

- **One KEDA ScaledObject per Deployment, as today.** Each decides alone, so
  none can trade GPUs between models, choose between accelerator types or
  stay inside a shared budget.
- **Kueue alone.** Kueue decides which Pods are admitted, not how many replicas
  each model should ask for. Without the controller reading Kueue's quota,
  scale-ups past it wait at Kueue's admission gate as pending Pods.
- **Static quotas per model.** Each model needs a quota sized for its own peak,
  and the GPUs sit idle between peaks. Sharing one budget costs 39–43 % more
  GPU-seconds than today's optimizer, which gives idle GPUs back, in exchange for serving the
  model that rises first at once.
- **A fixed minimum replica count per model** instead of a shared warm pool.
  Close on latency, but 33 % more GPU-seconds against the pool's 17 %. Better
  when a model is always busy.
- **KEDA thresholds on raw metrics.** In the shape-change run, KV occupancy
  pointed at one replica while the load needed two, then three. A direct
  comparison with plain KEDA thresholds is not yet measured.
