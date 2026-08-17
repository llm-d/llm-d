# SLO-Aware Autoscaling with KEDA — the control law

This is the full derivation of the control law used by the
[SLO-aware autoscaling guide](../../../../guides/workload-autoscaling/slo-aware/README.md):
how the EPP's latency histograms become a single saturation signal, how the
KEDA formula maps that signal to a desired replica count, and why each
asymmetry in the design exists. For the deployable manifests, tunables, and
benchmark results, use the guide; read this when you want to understand or
modify the loop itself.

Five steps, evaluated continuously. Steps 1–2 are the parts you deploy
(recording rules + formula); 3–4 are standard HPA/Kubernetes mechanics that
shape the response; 5 is why it converges.

## 1. Signal chain (recording rules, every 15 s)

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

## 2. Desired replicas (the KEDA formula)

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

## 3. HPA layer (rate limiting and stabilization)

The KEDA-generated HPA clips `d` to `[minReplicaCount, maxReplicaCount]` and
applies the `behavior` block: scale-up bursts at `max(100 %, 4 pods)` per
60 s with a 30 s stabilization window (min over window); scale-down walks 1
pod per 120 s with a 180 s window (max over window). Fast up, deliberate
down.

## 4. Ready replicas (pod warmup)

Additions take a warmup time $`T_w`$ to become ready (model load +
torch-compile); removals are immediate:

```math
R(t) = \min_{\tau \in [t - T_w,\ t]} N_{spec}(\tau)
```

$`T_w`$ is why the credit in step 2 exists, and it is the knob most worth
engineering: pod warmup is roughly *half* the scale-up transient, so cutting it
directly shrinks the violations paid at every scale-up. A torch-compile cache
volume + a fast startupProbe took our pod-ready time from ~2 m 15 s to ~100 s;
that patch ships with the guide as
[`slo-aware/decode-warmup-patch.yaml`](../../../../guides/workload-autoscaling/slo-aware/decode-warmup-patch.yaml)
(recommended — apply it to the decode Deployment, see the guide's Deploy
section). This is the patch used to produce the guide's benchmark results.

## 5. Closing the loop

More ready replicas → lower per-pod load → the predicted P90 falls → `s`
re-enters the band and the ask stops. Under-provisioning raises `s` and the
loop adds capacity; over-provisioning drops `s` below 0.40 and the slow
drain begins. The system settles wherever `s` lands inside the band —
empirically ~1.4–1.5 rps per H100 TP=2 replica for a 4000-token-prefill
workload at these SLOs.
