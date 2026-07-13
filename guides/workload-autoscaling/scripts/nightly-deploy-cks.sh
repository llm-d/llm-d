#!/usr/bin/env bash
# Deploy the WVA + optimized-baseline stack on CKS (CoreWeave Kubernetes) in a single namespace.
#
# NOTE: OpenShift (nightly-deploy-ocp.sh) is the supported/priority path; this script is
# best-effort. Two assumptions here are unverified on the CKS nightly cluster:
#   - KEDA is installed (checked below, fails loudly if not).
#   - A monitoring stack serves Prometheus at prometheus-operated.llm-d-monitoring:9090 —
#     the endpoint wva-config/platform/k8s pins PROMETHEUS_BASE_URL to. Override with
#     PROMETHEUS_ADDRESS if that is wrong.
#
# Environment variables:
#   NAMESPACE             target namespace for ALL resources (set by the nightly workflow)
#   WVA_TAG               WVA controller image tag override (default: unset = upstream default)
#   OUTPUT_DIR            where to write the generated overlay (default: mktemp -d)
#   PROMETHEUS_ADDRESS    Prometheus endpoint KEDA queries
#   ROUTER_CHART_VERSION  EPP router chart version (default: set by guides/env.sh)

set -euo pipefail

if command -v grealpath &>/dev/null; then
  _realpath=grealpath          # macOS: brew install coreutils
elif realpath --version &>/dev/null 2>&1; then
  _realpath=realpath           # Linux GNU coreutils
else
  echo "ERROR: GNU realpath not found. On macOS install it with: brew install coreutils" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${REPO_ROOT}/guides/env.sh"

NAMESPACE="${NAMESPACE:-llm-d-optimized-baseline}"
SCALEDOBJECT=optimized-baseline-nvidia-gpu-vllm-decode-scaler
WVA_TAG="${WVA_TAG:-}"
OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d -t nightly-deploy-cks.XXXXXX)}"
PROMETHEUS_ADDRESS="${PROMETHEUS_ADDRESS:-https://prometheus-operated.llm-d-monitoring.svc.cluster.local:9090}"

mkdir -p "${OUTPUT_DIR}"

# Nightly-only model server tweaks: GPU priority class, writable Triton cache, 2 replicas.
yq '.spec.template.spec.priorityClassName="nightly-gpu-critical"' -i guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
yq '.spec.template.spec.volumes += {"name": "triton-cache", "emptyDir": {}}' -i guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
yq '.spec.template.spec.containers[0].volumeMounts += {"mountPath": "/.triton", "name": "triton-cache"}' -i guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
yq '.spec.replicas=2' -i guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
kubectl apply -k guides/optimized-baseline/modelserver/gpu/vllm/base -n "${NAMESPACE}"

helm install workload-variant-autoscaler-inferencepool-standalone \
  "${ROUTER_STANDALONE_CHART}" \
  -f guides/recipes/router/base.values.yaml \
  -f guides/optimized-baseline/router/optimized-baseline.values.yaml \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

# kustomize rejects absolute resource paths, so reference the repo relative to OUTPUT_DIR.
REL="$("${_realpath}" --relative-to="${OUTPUT_DIR}" "${REPO_ROOT}")"

# platform/k8s pins namespace llm-d-optimized-baseline; wrap it so everything lands in NAMESPACE.
cat > "${OUTPUT_DIR}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ${NAMESPACE}
resources:
  - ${REL}/guides/workload-autoscaling/wva-config/platform/k8s/
  - ${REL}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda/k8s/
patches:
  # The namespace and Prometheus endpoint live inside KEDA trigger strings, so the kustomize
  # namespace transformer cannot reach them — rewrite them explicitly.
  - patch: |-
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: |
          wva_desired_replicas{
            variant_name="optimized-baseline-nvidia-gpu-vllm-decode-scaler",
            namespace="${NAMESPACE}"
          }
      - op: replace
        path: /spec/triggers/0/metadata/serverAddress
        value: ${PROMETHEUS_ADDRESS}
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
      # The deployment starts at 2 replicas while WVA, seeing no traffic yet, asks for 1. KEDA
      # would scale down within the 60s guide default — mid-startup, while the workflow is still
      # waiting on the pods it listed before the scale-down, which then fails on a NotFound.
      # Hold scale-down off until the stack is up and the benchmark is driving load.
      # TODO: interim. The real fix is to start the deployment at 1 replica (the floor WVA asks
      # for when idle) and let the benchmark drive scale-up, rather than pinning 2 and delaying
      # the scale-down that follows.
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleDown/stabilizationWindowSeconds
        value: 900
    target:
      kind: ScaledObject
      name: optimized-baseline-nvidia-gpu-vllm-decode-scaler
EOF

if [ -n "${WVA_TAG}" ]; then
  cat >> "${OUTPUT_DIR}/kustomization.yaml" <<EOF
images:
  - name: ghcr.io/llm-d/llm-d-workload-variant-autoscaler
    newTag: ${WVA_TAG}
EOF
fi

echo "==> Validating kustomization"
kubectl kustomize "${OUTPUT_DIR}" >/dev/null

# KEDA is the external metrics provider (Prometheus Adapter was retired upstream in
# llm-d-workload-variant-autoscaler#1399). WVA only registers its ScaledObject reconciler if
# the KEDA CRD exists when the controller starts, so check before deploying the controller.
echo "==> Checking for KEDA"
if ! kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  echo "ERROR: CRD scaledobjects.keda.sh not found — install KEDA on the cluster before WVA." >&2
  exit 1
fi

echo "==> Applying WVA + autoscaling assets"
kubectl apply -k "${OUTPUT_DIR}"

echo "==> Waiting for WVA controller to become Available"
kubectl wait deployment/wva-controller-manager \
  -n "${NAMESPACE}" --for=condition=Available --timeout=300s

echo "==> Waiting for the ScaledObject to be Ready"
# Ready only means KEDA accepted the trigger and created its HPA — not that the metric works.
kubectl wait scaledobject/"${SCALEDOBJECT}" \
  -n "${NAMESPACE}" --for=condition=Ready --timeout=300s

# If KEDA cannot query Prometheus it suppresses the error and serves `fallback: replicas`, so the
# stack comes up healthy and the ScaledObject still reports Ready=True/Active=True. Fallback=False
# is the only signal that the replica count actually comes from WVA.
# Require Fallback=False to HOLD: it reads False before KEDA's first poll, and WVA needs a
# scrape cycle to publish the metric, so early readings are meaningless in both directions.
echo "==> Verifying KEDA is scaling on the real metric (not fallback)"
streak=0
for _ in $(seq 1 30); do
  fallback="$(kubectl get scaledobject/"${SCALEDOBJECT}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.conditions[?(@.type=="Fallback")].status}' 2>/dev/null || true)"
  if [[ "${fallback}" == "False" ]]; then
    streak=$((streak + 1))
    [[ "${streak}" -ge 3 ]] && break
  else
    streak=0
  fi
  sleep 10
done

if [[ "${streak}" -lt 3 ]]; then
  echo "ERROR: ScaledObject is in fallback (Fallback=${fallback:-unknown}) — KEDA is NOT reading" >&2
  echo "       wva_desired_replicas. Replica count is coming from spec.fallback, not from WVA." >&2
  kubectl get scaledobject/"${SCALEDOBJECT}" -n "${NAMESPACE}" \
    -o jsonpath='{range .status.conditions[*]}  {.type}={.status} ({.reason}: {.message}){"\n"}{end}' >&2
  echo "--- KEDA operator errors for this ScaledObject ---" >&2
  kubectl logs -A -l app=keda-operator --tail=200 2>/dev/null \
    | grep -i "${NAMESPACE}" | tail -10 >&2 || true
  exit 1
fi

echo "==> Listing autoscaling resources"
kubectl get scaledobject,hpa -n "${NAMESPACE}"
