#!/usr/bin/env bash
# Deploy the direct KEDA+EPP optimized-baseline stack on OpenShift.
# This path is deliberately independent of WVA; initial-state assertions live
# in .github/scripts/e2e/e2e-validate-keda-epp.sh.
#
# Environment variables:
#   NAMESPACE             target namespace
#   OUTPUT_DIR            generated nightly overlay directory
#   ROUTER_CHART_VERSION  EPP router chart version (default: guides/env.sh)
#   RENDER_ONLY           generate and validate the overlay without cluster changes
# The reusable nightly workflow creates the namespace and enables user-workload
# monitoring before invoking this script.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck source=guides/env.sh
source "${REPO_ROOT}/guides/env.sh"

NAMESPACE="${NAMESPACE:-keda-epp-nightly-optimized-baseline-$(printf '%04x' "$RANDOM")}"
SCALEDOBJECT=optimized-baseline-keda-epp
MODEL_DEPLOYMENT=optimized-baseline-nvidia-gpu-vllm-decode
AUTH_CRB_BASE=keda-epp-prometheus-cluster-monitoring-view
OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d -t nightly-deploy-keda-epp-ocp.XXXXXX)}"
RENDER_ONLY="${RENDER_ONLY:-false}"

namespace_hash() {
  if command -v sha256sum &>/dev/null; then
    printf '%s' "$1" | sha256sum | cut -c1-8
  else
    printf '%s' "$1" | shasum -a 256 | cut -c1-8
  fi
}

NS_HASH="$(namespace_hash "${NAMESPACE}")"
AUTH_CRB="${AUTH_CRB_BASE}-${NS_HASH}"

deployment_diagnostics() {
  local status="$1" line="$2"
  trap - ERR
  set +e

  echo "ERROR: direct KEDA+EPP deployment failed at line ${line} (status ${status})" >&2
  echo "--- Namespaced resources ---" >&2
  kubectl get deployment,replicaset,pod,service,endpoints,servicemonitor,podmonitor,scaledobject,triggerauthentication,hpa \
    -n "${NAMESPACE}" -o wide >&2 2>/dev/null || true
  echo "--- ScaledObject conditions ---" >&2
  kubectl get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" \
    -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}: {.message}){"\n"}{end}' \
    >&2 2>/dev/null || true
  echo "--- Authentication metadata (Secret data intentionally omitted) ---" >&2
  kubectl get serviceaccount/keda-epp-prometheus triggerauthentication/keda-prometheus-auth \
    -n "${NAMESPACE}" -o yaml >&2 2>/dev/null || true
  kubectl get secret/keda-prometheus-auth -n "${NAMESPACE}" -o json 2>/dev/null \
    | jq 'del(.data, .stringData)' >&2 || true
  kubectl get "clusterrolebinding/${AUTH_CRB}" -o yaml >&2 2>/dev/null || true
  echo "--- Namespace events ---" >&2
  kubectl get events -n "${NAMESPACE}" --sort-by='.lastTimestamp' >&2 2>/dev/null || true
  echo "--- KEDA operator messages for this deployment ---" >&2
  kubectl logs -n openshift-keda -l app=keda-operator --all-containers --tail=200 2>/dev/null \
    | grep -E "${NAMESPACE}|${SCALEDOBJECT}" >&2 || true

  exit "${status}"
}
trap 'deployment_diagnostics "$?" "$LINENO"' ERR

mkdir -p "${OUTPUT_DIR}"
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is required to construct the generated overlay paths" >&2
  exit 1
fi
REL="$(python3 -c 'import os, sys; print(os.path.relpath(os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])))' \
  "${REPO_ROOT}" "${OUTPUT_DIR}")"
RENDERED_MANIFEST="${OUTPUT_DIR}/rendered.yaml"

echo "Generating direct KEDA+EPP OpenShift overlay in ${OUTPUT_DIR}"
echo "  NAMESPACE: ${NAMESPACE}"
echo "  AUTH_CRB:  ${AUTH_CRB}"

cat > "${OUTPUT_DIR}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ${NAMESPACE}
resources:
  - ${REL}/guides/optimized-baseline/modelserver/gpu/vllm/base/
  - ${REL}/guides/workload-autoscaling/keda-epp/
components:
  - ${REL}/guides/recipes/modelserver/components/monitoring/
  - ${REL}/guides/workload-autoscaling/keda-epp/ocp/
patches:
  - patch: |-
      - op: replace
        path: /spec/replicas
        value: 1
      - op: add
        path: /spec/template/spec/priorityClassName
        value: nightly-gpu-critical
    target:
      group: apps
      version: v1
      kind: Deployment
      name: ${MODEL_DEPLOYMENT}
  - patch: |-
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
      - op: add
        path: /spec/fallback
        value:
          failureThreshold: 3
          replicas: 1
          behavior: currentReplicasIfHigher
      - op: test
        path: /spec/triggers/0/name
        value: epp-queue-size
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: >-
          sum(llm_d_epp_flow_control_queue_size{namespace="${NAMESPACE}",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})
      - op: test
        path: /spec/triggers/1/name
        value: epp-running-requests
      - op: replace
        path: /spec/triggers/1/metadata/query
        value: >-
          sum(llm_d_epp_request_running{namespace="${NAMESPACE}",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})
    target:
      group: keda.sh
      version: v1alpha1
      kind: ScaledObject
      name: ${SCALEDOBJECT}
  - patch: |-
      - op: replace
        path: /metadata/name
        value: ${AUTH_CRB}
      - op: replace
        path: /metadata/annotations/llm-d.ai~1owner-namespace
        value: ${NAMESPACE}
      - op: add
        path: /metadata/labels/llm-d.ai~1nightly-namespace
        value: ${NAMESPACE}
      - op: replace
        path: /subjects/0/namespace
        value: ${NAMESPACE}
    target:
      group: rbac.authorization.k8s.io
      version: v1
      kind: ClusterRoleBinding
      name: ${AUTH_CRB_BASE}
EOF

echo "==> Validating generated kustomization"
kubectl kustomize "${OUTPUT_DIR}" > "${RENDERED_MANIFEST}"

if [[ "${RENDER_ONLY}" == "true" ]]; then
  echo "Render-only validation complete: ${OUTPUT_DIR}"
  trap - ERR
  exit 0
fi

echo "==> Checking for KEDA"
if ! kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  echo "ERROR: CRD scaledobjects.keda.sh not found." >&2
  echo "       Install the OpenShift Custom Metrics Autoscaler operator before running this nightly." >&2
  exit 1
fi

echo "==> Removing stale cluster-scoped authentication binding"
kubectl delete clusterrolebinding "${AUTH_CRB}" --ignore-not-found

echo "==> Installing EPP router via Helm"
helm upgrade --install optimized-baseline \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
  -f "${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml" \
  -f "${REPO_ROOT}/guides/workload-autoscaling/keda-epp/router.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

echo "==> Applying model, monitoring, authentication, and direct ScaledObject"
kubectl apply -f "${RENDERED_MANIFEST}"

echo "==> Direct KEDA+EPP resources applied"
kubectl get deployment/${MODEL_DEPLOYMENT} deployment/optimized-baseline-epp \
  scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}"
echo "ClusterRoleBinding: ${AUTH_CRB}"

trap - ERR
