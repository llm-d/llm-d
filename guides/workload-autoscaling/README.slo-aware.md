# SLO-Aware Autoscaling with KEDA and Predicted Latency

Autoscale a vLLM inference pool against **latency SLOs** — for example, "keep P90
time-to-first-token under 3 s and P90 time-per-output-token under 100 ms, with as
few GPUs as possible" — using only off-the-shelf components:

- **llm-d-router (EPP)** emits per-request latency histograms —
  the pool's **estimated** P90 TTFT/TPOT, sourced either from the EPP's
  online-trained ML latency predictor (*predicted* latency) or, when that's not
  enabled, aggregated from the actual measured latencies in real time,
- **Prometheus recording rules** turn those into a single saturation signal,
- **KEDA** (with an [expr-lang](https://expr-lang.org/) formula) computes the
  desired replica count and drives a standard HPA.

No custom controller, no CRDs beyond KEDA's ScaledObject. The **control law** —
the rule that maps the current saturation signal to a desired replica count — is
~5 lines of math. The predicted signal reacts a little earlier, but either
source drives the same loop — the guide uses "predicted" throughout because
it's what we benchmarked, but read it as **estimated latency** wherever it
matters.

> [!NOTE]
> **Scope: one variant, one SLO pair.** The recording rules bake in a single
> TTFT/TPOT SLO target and aggregate the EPP's latency histograms pool-wide, so
> this guide assumes **one inference pool serving one model variant against one
> SLO pair**. It does not yet handle multiple variants of the same model (e.g.
> the same model on two accelerator types, or split across separate inference
> pools) or multiple SLO tiers — those would need per-variant recording rules
> and a way to identify the variant at the EPP level. For a
> disaggregated (prefill/decode) deployment, TTFT would drive the prefill pool
> and TPOT the decode pool; adapting the signal chain for that is future work.

## Prerequisites

1. **A running llm-d serving stack.** Follow the
   [optimized-baseline guide](../optimized-baseline) (vLLM inference pool +
   EPP). Then, to feed this autoscaler the *predicted*-latency signal, add the
   **`predicted-latency-producer`** plugin to the EPP's `EndpointPickerConfig`
   (the exact config we benchmarked is in `slo-aware/epp-plugins-configmap.yaml`):

   ```yaml
   plugins:
   - type: predicted-latency-producer
     parameters:
       streamingMode: true
   ```

   *This plugin is optional*: the recording rules below fall back to the EPP's
   **actual**-latency histograms when the predicted series are absent —
   predicted latency just reacts earlier (it rises as pressure builds instead
   of after queueing has already happened).

2. **The EPP's metrics scraped by Prometheus** — this is the whole input to
   the loop; without it the signal is empty and the pool sits at
   minReplicas. The router chart's **`monitoring`** feature wires this up —
   deploy the router with that values file, e.g.:

   ```sh
   helm upgrade <release> $ROUTER_STANDALONE_CHART \
     -f ../recipes/router/base.values.yaml \
     -f ../predicted-latency-routing/router/predicted-latency.values.yaml \
     -f ../recipes/router/features/monitoring.values.yaml \   # <-- exposes port 9090 + ServiceMonitor
     ... -n <ns> --version $ROUTER_CHART_VERSION
   ```

   It exposes the EPP's metrics port (9090) and creates a ServiceMonitor so
   Prometheus discovers it. Verify with
   `kubectl get servicemonitor -n <ns>` and, in Prometheus,
   `up{job=~".*epp.*"} == 1`. Scrape interval ≤ 15 s (we use 5 s) to match
   the 1 m rate windows below.

3. **kube-state-metrics.** The control law reads the target Deployment's
   provisioned and ready replica counts, which kube-state-metrics exposes to
   Prometheus. Any standard install works; if you don't already run it, a
   minimal deployments-only install is in
   [`slo-aware/kube-state-metrics.yaml`](#files).

4. **KEDA** ≥ 2.15 (we ran 2.20.1): `kubectl apply --server-side -f https://github.com/kedacore/keda/releases/download/v2.20.1/keda-2.20.1.yaml`.

## How it works — the control law

Five steps, evaluated continuously. Steps 1–2 are the parts you deploy
(recording rules + formula); 3–4 are standard HPA/Kubernetes mechanics that
shape the response; 5 is why it converges.

### 1. Signal chain (recording rules, every 15 s)

The saturation signal is the pool's P90 latency measured against your SLOs —
the worst of the two targets:

```math
s_{raw} = \max\!\left(\frac{\text{P90 TTFT}}{S_{ttft}},\ \frac{\text{P90 TPOT}}{S_{tpot}}\right), \qquad \text{NaN/no traffic} \mapsto 0
```

Each P90 is `histogram_quantile(0.9, ...)` over 1-minute bucket rates,
preferring the predicted histogram and falling back to the actual one (the
`>= 0` filter drops a present-but-dead predicted series, whose quantile is
NaN). `s_raw < 1` means the tail is within SLO; `> 1` means the SLO is
breached; an idle pool reads 0.

Then clamp and smooth:

```math
s = \text{avg\_over\_time}\big(\min(s_{raw},\ C)\big)[45s], \qquad C = 2.0
```

Don't skip the clamp: at the queueing knee, predicted latency
can spike to 10–90× SLO in one sample. Anything above the cap already means
"add capacity at the maximum rate" — letting the raw magnitude through only
poisons the moving average so it stays inflated long after the pool recovers.

### 2. Desired replicas (the KEDA formula)

With `s` = smoothed saturation, `n` = provisioned replicas, `r` = ready
replicas, and thresholds `θ_up = 0.55`, `θ_dn = 0.40` (in the ScaledObject these
are the `saturation`, `replicas`, and `readyReplicas` triggers):

```math
d = \begin{cases} n + \left\lceil n \left( \frac{s \cdot c}{\theta_{up}} - 1 \right) \right\rceil & s > \theta_{up} \quad\text{(scale up, credited)} \\ n - \left\lfloor n \left( 1 - \frac{s}{\theta_{dn}} \right) \right\rfloor & s < \theta_{dn} \quad\text{(scale down, uncredited)} \\ n & \text{otherwise} \quad\text{(hysteresis band)} \end{cases}
```

where the **in-flight credit** `c = r/n` (when `0 < r < n`, else 1) is applied
on the scale-up branch only. Three deliberate asymmetries, each worth its
weight:

- **Hysteresis band (0.40–0.55):** the pool holds steady between the
  boundaries instead of flapping around a single set-point.
- **Credit:** the signal is measured on ready pods only. While a scale-up is
  in flight the ready pods over-report the post-warmup load; scaling the
  demand by `r/n` makes the ask cover only the deficit beyond pods already
  warming, instead of racing to `maxReplicas` every cycle.
- **Scale-down uses the uncredited signal:** the credit discounts demand, so
  during warmup it could push an over-threshold signal below the scale-down
  boundary and shed replicas mid-scale-up. A pool may only shed when the
  measured signal itself has headroom.

**Why the thresholds sit low:** under healthy load the P90-of-predicted-latency
signal sits around 0.2–0.45 of the SLO and then goes *vertical* at the
queueing knee. A threshold near 1.0 fires only after the SLO budget is nearly
burned; 0.55 fires on the pre-knee rise. If your SLO is close to your base
latency, raise the band.

In the ScaledObject, `d` is the `scalingModifiers` formula output, and the
composite target is `1` (AverageValue) — a pass-through: the formula output
*is* the desired replica count.

### 3. HPA layer (rate limiting and stabilization)

The KEDA-generated HPA clips `d` to `[minReplicaCount, maxReplicaCount]` and
applies the `behavior` block: scale-up bursts at `max(100 %, 4 pods)` per
60 s with a 30 s stabilization window (min over window); scale-down walks 1
pod per 120 s with a 180 s window (max over window). Fast up, deliberate
down.

### 4. Ready replicas (pod warmup)

Additions take a warmup time $`T_w`$ to become ready (model load +
torch-compile); removals are immediate:

```math
R(t) = \min_{\tau \in [t - T_w,\ t]} N_{spec}(\tau)
```

$`T_w`$ is why the credit in step 2 exists, and it is the knob most worth
engineering: pod warmup is roughly *half* the scale-up transient, so cutting it
directly shrinks the violations paid at every scale-up. A torch-compile cache
volume + a fast startupProbe took our pod-ready time from ~2 m 15 s to ~100 s;
that patch ships as
[`slo-aware/decode-warmup-patch.yaml`](slo-aware/decode-warmup-patch.yaml)
(recommended — apply it to the decode Deployment, see Deploy below). This is the
patch used to produce the benchmark results below.

### 5. Closing the loop

More ready replicas → lower per-pod load → the predicted P90 falls → `s`
re-enters the band and the ask stops. Under-provisioning raises `s` and the
loop adds capacity; over-provisioning drops `s` below 0.40 and the slow
drain begins. The system settles wherever `s` lands inside the band —
empirically ~1.4–1.5 rps per H100 TP=2 replica for a 4000-token-prefill
workload at these SLOs.

## Deploy

The manifests ship with our example names — **adjust them to your cluster
before applying:**

- in `slo-aware/scaledobject.yaml`: the target Deployment
  (`optimized-baseline-nvidia-gpu-vllm-decode`), its namespace
  (`llm-d-optimized-baseline`), and the Prometheus address
  (`prometheus-server.monitoring.svc:80`) — in the `scaleTargetRef` and the
  three trigger queries;
- in `slo-aware/prometheus-rule.yaml`: the SLO targets (3 s TTFT / 100 ms TPOT)
  if yours differ, the `namespace` (it ships in the workload namespace
  `llm-d-optimized-baseline`, not `monitoring` — the rules belong to the tenant
  they scale), and the `labels` so your Prometheus's `ruleSelector` picks it up.

```sh
kubectl apply --server-side -f https://github.com/kedacore/keda/releases/download/v2.20.1/keda-2.20.1.yaml   # see APIService caveat above
kubectl apply -f slo-aware/kube-state-metrics.yaml            # skip if you already run KSM
kubectl apply -f slo-aware/prometheus-rule.yaml               # recording rules (match its labels to your ruleSelector)
kubectl apply -f slo-aware/scaledobject.yaml
```

The recording rules ship as a Prometheus Operator `PrometheusRule` in the
workload namespace. For the operator to load it, its namespace must match your
Prometheus's `ruleNamespaceSelector` and its labels your `ruleSelector` (see the
file's comment for how to inspect both). The recorded series are global in
Prometheus either way — the namespace choice is about ownership, not metric
isolation (one rule-set per Prometheus for now — see the **Scope** note above).
On a plain (non-operator) Prometheus, merge its `spec.groups` into your
`rule_files` config and reload instead.

The moment the ScaledObject is created, KEDA's HPA owns the Deployment's
replica count: an idle pool drains to `minReplicaCount`.

**Recommended — fast pod warmup.** Pod-ready time is about half the scale-up
transient, so shortening it is the highest-leverage tuning after the control
law itself. Apply the warmup patch to your decode Deployment (cuts pod-ready
from ~2 m 15 s to ~100 s):

```sh
kubectl -n <serving-namespace> patch deploy <decode-deployment> \
  --type=strategic --patch-file slo-aware/decode-warmup-patch.yaml
```

## Tunables

| Parameter | Where | Benchmarked value | Meaning |
|---|---|---|---|
| $`S_{ttft}, S_{tpot}`$ | recording rules | 3000 ms / 100 ms | the SLOs; the signal is latency ÷ SLO |
| latency quantile | recording rules | P90 (`histogram_quantile(0.9, …)`) | which tail of the TTFT/TPOT distribution drives the signal |
| $`C`$ (clamp) | recording rules | 2.0 | max signal per sample; must exceed θ_up |
| smoothing window | recording rules | 45 s | absorbs single-sample noise |
| $`\theta_{up}, \theta_{dn}`$ | formula | 0.55 / 0.40 | scale-up trigger / scale-down boundary |
| min/max replicas | ScaledObject | 3 / 8 | pool bounds |
| HPA behavior | ScaledObject | up max(100 %, 4)/60 s; down 1/120 s | burst up, drain slow |

## What it looks like

On H100 (Qwen3-32B, TP=2), staged ramp 2→10→1 rps, bounds 3–8, the loop
averages **95.8 % combined SLO attainment at 5.93 average replicas** across
repeated runs (a static pool sized for peak is 100 % at 8). One episode is
plotted below; the annotations trace each governing parameter to the behavior
it produces — trigger, burst, warmup, drain.

![One scored episode: offered load, TTFT/TPOT p90 vs SLO, and replicas over the run](slo-aware/benchmark-results/scored-run-overview.png)

The pool holds at the floor while healthy, bursts to the cap when the signal
crosses `θ_up`, then drains a pod at a time once load falls. When residual
violations appear they cluster at the first capacity crossing, where a load step
lands on an under-provisioned pool before the new pods finish warming — the
structural cost of any reactive-signal + slow-actuation loop; the episode
plotted caught one such crossing. Full methodology and breakdown:
[`benchmark-templates/BENCHMARK.md`](slo-aware/benchmark-templates/BENCHMARK.md).

## Files

| File | What |
|---|---|
| `slo-aware/` | the autoscaler: kube-state-metrics, recording rules (signal, step 1), ScaledObject (control law + HPA behavior, steps 2–3), and the recommended `decode-warmup-patch.yaml`. KEDA itself installs from its upstream release (see step 4). |
| `slo-aware/epp-plugins-configmap.yaml` | the EPP plugin config we ran, incl. `predicted-latency-producer` |
| `slo-aware/benchmark-templates/` | reusable benchmark harness: methodology (`BENCHMARK.md`), load profiles, and the plot+score script |
| `slo-aware/benchmark-results/` | our run's output: episode plots and the scored numbers |

## Revert

```sh
kubectl delete -f slo-aware/scaledobject.yaml    # hands replica control back to you immediately
# if another external-metrics adapter (e.g. prometheus-adapter) was displaced
# by the KEDA install, re-apply its APIService, or just remove KEDA:
kubectl delete -f https://github.com/kedacore/keda/releases/download/v2.20.1/keda-2.20.1.yaml -f slo-aware/kube-state-metrics.yaml
```

Before touching a shared cluster, snapshot anything you overwrite —
`kubectl get apiservice v1beta1.external.metrics.k8s.io -o yaml > /somewhere`
and your Prometheus/EPP ConfigMaps — so you can restore them.
