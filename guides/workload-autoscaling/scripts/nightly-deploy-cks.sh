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
  - ${REL}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda/
patches:
  # The namespace and Prometheus endpoint live inside KEDA trigger strings, so the kustomize
  # namespace transformer cannot reach them — rewrite them explicitly.
  - patch: |-
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: |
          wva_desired_replicas{
            variant_name="optimized-baseline-nvidia-gpu-vllm-decode",
            namespace="${NAMESPACE}"
          }
      - op: replace
        path: /spec/triggers/0/metadata/serverAddress
        value: ${PROMETHEUS_ADDRESS}
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
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
kubectl wait scaledobject/optimized-baseline-nvidia-gpu-vllm-decode-scaler \
  -n "${NAMESPACE}" --for=condition=Ready --timeout=300s

echo "==> Listing autoscaling resources"
kubectl get scaledobject,hpa -n "${NAMESPACE}"
