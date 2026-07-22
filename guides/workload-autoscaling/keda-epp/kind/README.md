# Local Kind metric-contract test

This opt-in CPU-only test validates the canonical direct autoscaling signal
path:

`EPP -> HTTPS Prometheus -> KEDA external metrics -> generated HPA`

## Important: effective KEDA 2.20 CA trust

Stock KEDA `2.20.0` ignores the CA value in the current CA-only
`TriggerAuthentication` when `authModes` is empty. The canonical
`TriggerAuthentication` and both ScaledObject `authenticationRef` entries are
still present and are static-validated exactly, but they do **not** provide the
effective runtime server trust in this Kind test.

The effective trust path is the stock KEDA operator-wide custom CA mechanism:

- Secret `keda/llmd-prometheus-ca` contains only `ca.crt`;
- the Secret is mounted read-only at `/custom/ca` in the KEDA operator;
- the operator starts with `--ca-dir=/custom/ca`;
- stock KEDA loads that PEM into its global root pool, and its default HTTPS
  client verifies the canonical Prometheus service certificate through that
  pool.

HTTPS therefore succeeds through the operator-wide global root pool, not
through the canonical per-ScaledObject CA field. This is a Kind-only support
mechanism and does not change the canonical ScaledObject or
TriggerAuthentication.

## Run

From the repository root:

```bash
guides/workload-autoscaling/scripts/test-keda-epp-kind.sh
```

Use `--static-only` to render and validate all checked-in inputs and diagnostic
sanitization fixtures without creating a cluster.

The full test creates a uniquely named one-node Kind cluster and temporary
kubeconfig, installs only pinned dependencies, records evidence below a unique
`/tmp/llmd-keda-epp-kind-artifacts.*` directory, and deletes only that cluster
on success, failure, SIGINT, or SIGTERM. Existing clusters are never used or
modified. The checked-in monitoring overlay targets the approximately 6.3 GB
Docker environment used during development.

Only the KEDA Helm installation has a 15-minute bounded wait. A cold ARM64 run
observed the KEDA operator image take about 5 minutes 20 seconds to pull, with
the metrics-apiserver pull starting near the end of the former 8-minute window.
Monitoring and router retain their 8-minute waits. This setup allowance does
not change any request, metric, stable-sample, HPA, replica, or scale-transition
assertion.

The runtime is deliberately ordered. Phase A holds one simulator request and
queues one request at `maxConcurrency: 1`, proves both canonical metrics equal
`1` through EPP, Prometheus, KEDA, and a stable `1/1` HPA. Phase B then submits
exactly one additional request, retains direct and Prometheus running `1` / queue
`2` plus raw KEDA queue `2`, and accepts only one bounded `1 -> 2` transition.
Every Phase B sample rejects any HPA or target Deployment replica value above
two. After three stable exact-two samples, all three requests are terminated;
the test does not wait for scale-down or send post-scale inference.

A dedicated path-scoped pull-request workflow runs the same command when this
contract or its canonical router and observability inputs change. It has no
schedule or promotion role, and always uploads the runner's sanitized artifact
directory after its unique-cluster cleanup safeguard.

## Proof and limitation matrix

| Contract | What the test proves | What it does not prove |
|---|---|---|
| Canonical resources | The CA-only TriggerAuthentication, both auth refs, HTTPS FQDN, exact PromQL, thresholds, bounds, behavior, and target remain present and static-validated. | It does not prove that KEDA `2.20.0` consumes the CA-only TriggerAuthentication CA. |
| Effective Kind trust | Stock KEDA mounts `keda/llmd-prometheus-ca` read-only at `/custom/ca`, starts with `--ca-dir=/custom/ca`, and reaches HTTPS Prometheus through its global root pool. | It is not isolated per ScaledObject and does not validate a deployment without the operator-wide mount. |
| Metric path | A specifically selected EPP pod is UP in Prometheus; both exact queries and both KEDA external metrics are non-empty and numeric; both appear in HPA `currentMetrics`. | It is not sustained, performance, or production-environment load evidence. |
| Deterministic stimulus | Phase A proves stable running `1` / queue `1`; Phase B proves the initial three-request state as running `1` / queue `2` and raw KEDA queue `2`. | It is not sustained, performance, or arbitrary-load evidence. |
| Generated HPA | Ownership, target, min/max, behavior, selectors, targets, both current metrics, stable initial `1/1`, and one exact `1 -> 2` HPA/Deployment transition are validated. Every observed value above two fails immediately. | It does not prove scale-down, post-scale inference, growth beyond two, or promotion readiness. |
| Local feasibility | Phase A passed in the approximately 6.3 GB Docker development environment. Phase B feasibility is established only by a successful complete run. | Other hosts, cold image pulls, registries, and production resource envelopes may differ. |

A successful run requires the complete current KEDA operator log to be retained
before cluster deletion in `keda-operator-full.log`, with capture status in
`keda-operator-log-capture.txt` and a direct categorized scan in
`keda-operator-log-scan.txt` for `x509`, `unknown authority`, and scaler errors.
If that success-path capture fails, the status and scan artifacts are still
written as `failed-required` and the authoritative run fails. Capture during an
already failing run is `failed-optional` and cannot mask the original failure.
Secret artifacts are allowlisted metadata only: identity, type, and key names;
they never retain Secret payloads, PEM, certificate/key values, or last-applied
annotations.

## Explicit non-proof and exclusions

This test does not use or validate unauthenticated HTTP, `unsafeSsl`, mTLS,
client certificates, copied private keys, `authModes: tls`, or a patched KEDA
image. It does not establish the canonical CA-only TriggerAuthentication as a
working per-ScaledObject trust mechanism in KEDA `2.20.0`.

Nightly and promotional integration, OpenShift and environment-specific
behavior, Thanos or bearer authentication, sustained load, scale-down,
post-scale inference, growth beyond two replicas, and stable-promotion evidence
remain deferred to #1326. The path-scoped PR artifact upload is only evidence
for this bounded #1431 contract; it does not transfer nightly or promotional
artifact ownership.
