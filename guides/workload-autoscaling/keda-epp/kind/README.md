# WVA-backed Kind contract

This directory contains the llm-d-owned inputs for the CPU-only Kind contract:

`EPP -> HTTPS Prometheus -> KEDA external metrics -> generated HPA`

The path-scoped workflow at
`.github/workflows/ci-keda-epp-kind.yaml` checks out the WVA repository at an
immutable revision and uses its reusable Kind, monitoring, KEDA, polling,
diagnostic, request-cleanup, and isolated-cluster lifecycle. The llm-d
repository remains the source of truth for this Kustomize overlay, the
canonical `ScaledObject` and `TriggerAuthentication`, and the router,
monitoring, and KEDA values.

## Effective KEDA 2.20 CA trust

Stock KEDA `2.20.0` ignores the CA value in the current CA-only
`TriggerAuthentication` when `authModes` is empty. The canonical
`TriggerAuthentication` and both ScaledObject `authenticationRef` entries
remain present, but they do **not** provide the effective runtime server trust
in this Kind test.

The effective trust path is the stock KEDA operator-wide custom CA mechanism:

- Secret `keda/llmd-prometheus-ca` contains only `ca.crt`;
- the Secret is mounted read-only at `/custom/ca` in the KEDA operator;
- the operator starts with `--ca-dir=/custom/ca`;
- stock KEDA loads that PEM into its global root pool and verifies the
  canonical Prometheus service certificate through that pool.

HTTPS therefore succeeds through the operator-wide global root pool, not
through the canonical per-ScaledObject CA field. This is a Kind-only
compatibility mechanism and does not change the canonical ScaledObject or
TriggerAuthentication.

## Execution

The workflow uses two separate checkouts:

1. the current llm-d revision, which supplies every guide input;
2. `llm-d/llm-d-workload-variant-autoscaler` at the immutable revision recorded
   in the workflow, which supplies the lifecycle and
   `make test-e2e-keda-epp-guide`.

The caller performs these ordered operations:

1. create a fresh uniquely named one-node CPU Kind cluster through the WVA
   Kind setup;
2. install GAIE CRDs and generate the Prometheus server certificate using the
   llm-d certificate generator;
3. create the caller-owned Prometheus TLS Secret, the Kind-only global KEDA CA
   Secret, and the canonical CA-only authentication Secret;
4. install Prometheus and stock KEDA through the WVA lifecycle using
   `monitoring.values.yaml` and `keda.values.yaml`;
5. install the router using the canonical values layering plus
   `router.values.yaml`, then apply this directory's Kustomize output;
6. run only the WVA Ginkgo spec labeled `keda-epp-guide` with
   `DEPLOY_WVA=false`;
7. collect focused diagnostics and delete only the uniquely created cluster
   through the WVA teardown.

Once equivalent infrastructure and the llm-d guide resources are already
installed in a disposable Kind cluster, the contract entrypoint is:

```bash
make -C /path/to/llm-d-workload-variant-autoscaler \
  test-e2e-keda-epp-guide \
  KUBECONFIG=/path/to/disposable-kind.kubeconfig
```

The WVA target supplies the canonical guide namespaces and selects only the
direct-KEDA spec. Its Go test timeout is 40 minutes. Individual Kubernetes API
calls are bounded at 10 seconds, request clients at 900 seconds, and readiness
and scale-up waits by the shared WVA e2e configuration. Infrastructure setup
is outside the Go test timeout and is bounded independently by the workflow's
Helm and job timeouts.

## Contract boundary

| Contract | What the test proves | What it does not prove |
|---|---|---|
| Canonical resources | The already-applied guide ScaledObject reaches `Ready=True`; the generated HPA is owned by it, targets the intended Deployment, and contains the two live guide triggers as distinct External metrics using `AverageValue`. | It does not create or validate a replacement ScaledObject. |
| Effective Kind trust | Stock KEDA mounts `keda/llmd-prometheus-ca` read-only at `/custom/ca`, starts with `--ca-dir=/custom/ca`, and reaches HTTPS Prometheus without `x509`, unknown-authority, or `TriggerError` evidence. | It does not prove that KEDA `2.20.0` consumes the CA-only TriggerAuthentication CA or that a production installation without the global mount works. |
| Initial state | The generated HPA and target Deployment are stable at one replica before stimulus. | It is not scale-from-zero or scale-down evidence. |
| Bounded stimulus | Three sequential request Pods produce a running external metric of `1`, a raw queue external metric of `2`, and the first desired transition `1 -> 2`. | It is not sustained, performance, or arbitrary-load evidence. |
| Generated HPA actuation | HPA and Deployment state reach exactly two replicas for three stable samples, and every observed HPA or Deployment replica value above two fails the test. | It does not prove post-scale inference, growth beyond two, or promotion readiness. |

The direct spec terminates its request Pods after exact-two evidence. It does
not wait for or assert scale-down and does not send post-scale inference.

## Diagnostics and cleanup

The workflow retains the WVA test output, Kubernetes resources and events,
KEDA operator and metrics-server logs, Helm releases, rendered Kustomize
output, cluster inventories, and sanitized Secret metadata. It scans the KEDA
operator log for `x509`, `unknown authority`, and `TriggerError`.

Secret artifacts contain only identity, type, and key-name metadata. The
workflow rejects artifacts containing PEM blocks, private keys, kubeconfigs,
Secret `.data`, Secret `.stringData`, or annotations. Generated certificates,
private keys, and the temporary kubeconfig stay outside the artifact directory
and are removed during cleanup.

The fresh uniquely named Kind cluster is the final cleanup boundary. The
workflow records the cluster inventory before creation, validates the cluster
name before teardown, calls the WVA Kind teardown, and requires the final
cluster inventory to match the original inventory.

## Explicit exclusions

This test does not use unauthenticated HTTP, `unsafeSsl`, mTLS, client
certificates, dummy credentials, `authModes: tls`, or a patched KEDA image. It
does not establish the canonical CA-only TriggerAuthentication as a working
per-ScaledObject trust mechanism in KEDA `2.20.0`.

Nightly and promotional integration, OpenShift and environment-specific
behavior, Thanos or bearer authentication, sustained or performance load,
scale-down, post-scale inference, growth beyond two replicas, and
stable-promotion evidence remain outside this contract.
