# Runtime AI Inventory

Every well-lit path in this repository deploys AI workloads that
security and compliance teams increasingly need to account for: which
models are serving, on which runtimes, from which images. This guide
adds runtime inventory to any llm-d deployment using
[k8s-aibom](https://github.com/GoogleCloudPlatform/k8s-aibom), an
open-source, unprivileged controller that generates a
[CycloneDX 1.6 ML-BOM](https://cyclonedx.org/capabilities/mlbom/) per
AI workload, with evidence and a confidence tier on every attribute.
llm-d and vLLM are natively detected runtimes.

This composes with every guide here — it observes via the Kubernetes
API only (no sidecars, no privileged DaemonSet, no pod-spec changes)
and never modifies scheduling or serving behavior.

## Install

```bash
helm install k8s-aibom oci://ghcr.io/googlecloudplatform/charts/k8s-aibom \
  --version 1.5.0 \
  --namespace k8s-aibom-system --create-namespace
```

The published chart pins the controller image by digest, and every
release ships provenance and SBOM attestations.

## Opt in your serving namespace

Inventory is namespace-opt-in by design — nothing is recorded until an
operator labels a namespace:

```bash
kubectl label namespace ${NAMESPACE} aibom.k8saibom.dev/enabled=true
```

## See what is actually serving

Deploy any well-lit path as usual. Each vLLM / llm-d workload gets an
`AIBOM` resource:

```bash
kubectl get aiboms -n ${NAMESPACE}
```

The BOM records the served model identity (from vLLM `--model` args or
`HF_MODEL_ID`, confidence `declared`), the serving runtime and version
(confidence `inferred`, from the image), and resolved image digests —
each claim carrying an evidence locator pointing at the exact spec
field it came from. Documents can be shipped to external sinks (object
storage, webhook) for audit retention, and are byte-deterministic for
identical inputs, so they diff cleanly in GitOps workflows.

For signature verification of model artifacts (the `verified`
confidence tier) and the `kubectl aibom` plugin, see the
[k8s-aibom documentation](https://github.com/GoogleCloudPlatform/k8s-aibom#readme).
