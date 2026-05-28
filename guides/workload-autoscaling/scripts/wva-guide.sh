#!/usr/bin/env bash
# wva-guide.sh — install, generate, or delete the WVA + optimized-baseline stack
# in a single namespace.
#
# Prerequisites (must be in place before running 'apply'):
#   - Gateway API Inference Extension CRDs installed
#   - llm-d Router (EPP) deployed via Helm in ${NAMESPACE}
#   - Prometheus Adapter installed (see README.wva.md)
#   - OpenShift only: User Workload Monitoring enabled for ${NAMESPACE}
#
# Usage:
#   ./wva-guide.sh generate      — write kustomize overlay to OUTPUT_DIR
#   ./wva-guide.sh apply         — generate + kubectl apply
#   ./wva-guide.sh delete        — generate + kubectl delete
#   ./wva-guide.sh build-push    — build & push WVA controller image from local repo
#   ./wva-guide.sh build-apply   — build-push + apply (uses the freshly built image)
#
# Environment variables:
#   NAMESPACE      — target namespace for all resources (default: llm-d-wva-<random-6-chars>)
#   PLATFORM       — WVA platform overlay: ocp | generic | gke  (default: ocp)
#   INFRA_PROVIDER — model server overlay:  base | gke           (default: base)
#   OUTPUT_DIR     — where to write the generated overlay        (default: <script-dir>/.generated-wva)
#   WVA_REPO_DIR   — path to llm-d-workload-variant-autoscaler clone (default: <repo-root>/../llm-d-workload-variant-autoscaler)
#   WVA_IMAGE_TAG  — override WVA controller image tag in the generated overlay (set automatically by build-push)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

NAMESPACE="${NAMESPACE:-llm-d-wva-$(set +o pipefail; LC_ALL=C tr -dc 'a-z0-9' < /dev/urandom | head -c 6)}"
PLATFORM="${PLATFORM:-ocp}"
INFRA_PROVIDER="${INFRA_PROVIDER:-base}"
OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/.generated-wva}"
WVA_REPO_DIR="${WVA_REPO_DIR:-${REPO_ROOT}/../llm-d-workload-variant-autoscaler}"
_wva_sha="$(git -C "${WVA_REPO_DIR}" rev-parse --short=8 HEAD 2>/dev/null || true)"
WVA_IMAGE_TAG="${WVA_IMAGE_TAG:-${_wva_sha:+dev-${_wva_sha}}}"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

die() { echo "ERROR: $*" >&2; exit 1; }

validate_args() {
  case "${PLATFORM}" in
    ocp|generic|gke) ;;
    *) die "PLATFORM must be one of: ocp, generic, gke (got '${PLATFORM}')" ;;
  esac
  case "${INFRA_PROVIDER}" in
    base|gke) ;;
    *) die "INFRA_PROVIDER must be one of: base, gke (got '${INFRA_PROVIDER}')" ;;
  esac
}

# ---------------------------------------------------------------------------
# generate: write the kustomize overlay to OUTPUT_DIR
# ---------------------------------------------------------------------------

cmd_generate() {
  validate_args
  mkdir -p "${OUTPUT_DIR}"

  # Relative path from OUTPUT_DIR to REPO_ROOT (kustomize requires relative paths)
  REL="$(python3 -c "import os,sys; print(os.path.relpath(sys.argv[1],sys.argv[2]))" "${REPO_ROOT}" "${OUTPUT_DIR}")"

  echo "Generating kustomize overlay in ${OUTPUT_DIR}"
  echo "  NAMESPACE:      ${NAMESPACE}"
  echo "  PLATFORM:       ${PLATFORM}"
  echo "  INFRA_PROVIDER: ${INFRA_PROVIDER}"
  if [[ -n "${WVA_IMAGE_TAG}" ]]; then
    echo "  WVA_IMAGE_TAG:  ${WVA_IMAGE_TAG}"
  fi
  echo ""

  # kustomization.yaml
  cat > "${OUTPUT_DIR}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ${NAMESPACE}
resources:
  - ${REL}/guides/workload-autoscaling/wva-config/platform/${PLATFORM}/
  - ${REL}/guides/optimized-baseline/modelserver/gpu/vllm/${INFRA_PROVIDER}/
  - ${REL}/guides/workload-autoscaling/optimized-baseline-autoscaling/
components:
  - ${REL}/guides/recipes/modelserver/components/monitoring
patches:
  - path: patch-watch-namespace.yaml
    target:
      kind: Deployment
      name: workload-variant-autoscaler-controller-manager
  - path: patch-servicemonitor-ns.yaml
    target:
      kind: ServiceMonitor
      name: workload-variant-autoscaler-controller-manager-metrics-monitor
  - path: patch-hpa-exported-ns.yaml
    target:
      kind: HorizontalPodAutoscaler
      name: optimized-baseline-nvidia-gpu-vllm-decode
EOF

  if [[ -n "${WVA_IMAGE_TAG}" ]]; then
    cat >> "${OUTPUT_DIR}/kustomization.yaml" <<EOF
images:
  - name: controller
    newName: ghcr.io/llm-d/llm-d-workload-variant-autoscaler
    newTag: ${WVA_IMAGE_TAG}
EOF
  fi

  # patch-watch-namespace.yaml — fix --watch-namespace= arg in the WVA controller
  cat > "${OUTPUT_DIR}/patch-watch-namespace.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: workload-variant-autoscaler-controller-manager
spec:
  template:
    spec:
      containers:
        - name: manager
          args:
            - --metrics-bind-address=:8443
            - --metrics-secure=true
            - --leader-elect=true
            - --health-probe-bind-address=:8081
            - --watch-namespace=${NAMESPACE}
EOF

  # patch-servicemonitor-ns.yaml — fix ServiceMonitor namespaceSelector
  cat > "${OUTPUT_DIR}/patch-servicemonitor-ns.yaml" <<EOF
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: workload-variant-autoscaler-controller-manager-metrics-monitor
spec:
  namespaceSelector:
    matchNames:
      - ${NAMESPACE}
EOF

  # patch-hpa-exported-ns.yaml — fix exported_namespace metric label on the HPA
  cat > "${OUTPUT_DIR}/patch-hpa-exported-ns.yaml" <<EOF
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: optimized-baseline-nvidia-gpu-vllm-decode
spec:
  metrics:
    - type: External
      external:
        metric:
          name: wva_desired_replicas
          selector:
            matchLabels:
              variant_name: optimized-baseline-nvidia-gpu-vllm-decode
              exported_namespace: ${NAMESPACE}
        target:
          type: AverageValue
          averageValue: "1"
EOF

  echo "Generated. Inspect with: kubectl kustomize ${OUTPUT_DIR}"
}

# ---------------------------------------------------------------------------
# build-push: build and push the WVA controller image from a local clone
# ---------------------------------------------------------------------------

cmd_build_push() {
  [[ -d "${WVA_REPO_DIR}" ]] || die "WVA repo not found at ${WVA_REPO_DIR} (set WVA_REPO_DIR to override)"

  local sha
  sha=$(git -C "${WVA_REPO_DIR}" rev-parse --short=8 HEAD)
  WVA_IMAGE_TAG="dev-${sha}"
  local full_image="ghcr.io/llm-d/llm-d-workload-variant-autoscaler:${WVA_IMAGE_TAG}"

  echo "Building WVA controller image: ${full_image}"
  make -C "${WVA_REPO_DIR}" docker-build IMG="${full_image}"
  make -C "${WVA_REPO_DIR}" docker-push  IMG="${full_image}"
  echo "Pushed: ${full_image}"
}

# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

cmd_ensure_image() {
  [[ -z "${WVA_IMAGE_TAG}" ]] && return
  local full_image="ghcr.io/llm-d/llm-d-workload-variant-autoscaler:${WVA_IMAGE_TAG}"
  echo "Checking if image exists: ${full_image}"
  if docker manifest inspect "${full_image}" > /dev/null 2>&1; then
    echo "Image already pushed, skipping build."
  else
    echo "Image not found in registry, building..."
    cmd_build_push
  fi
}

cmd_apply() {
  cmd_ensure_image
  cmd_generate

  echo "Creating namespace '${NAMESPACE}' (idempotent)..."
  kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

  echo "Applying kustomize overlay..."
  kubectl apply -k "${OUTPUT_DIR}"
}

# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

cmd_delete() {
  cmd_generate

  echo "Deleting namespace-scoped resources from '${NAMESPACE}'..."
  # Cluster-scoped kinds (Namespace, CRD, ClusterRole, ClusterRoleBinding) are excluded
  # so that shared infrastructure is not torn down by a single-namespace delete.
  kubectl kustomize "${OUTPUT_DIR}" | python3 -c "
import sys
CLUSTER = {'Namespace','CustomResourceDefinition','ClusterRole','ClusterRoleBinding'}
doc = []
for line in sys.stdin:
    if line.strip() == '---':
        text = ''.join(doc)
        if text.strip() and not any('kind: '+k in text for k in CLUSTER):
            sys.stdout.write('---\n' + text)
        doc = []
    else:
        doc.append(line)
text = ''.join(doc)
if text.strip() and not any('kind: '+k in text for k in CLUSTER):
    sys.stdout.write('---\n' + text)
" | kubectl delete --ignore-not-found -f -
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

COMMAND="${1:-}"
case "${COMMAND}" in
  generate)     cmd_generate ;;
  apply)        cmd_apply ;;
  delete)       cmd_delete ;;
  build-push)   cmd_build_push ;;
  build-apply)  cmd_build_push; cmd_apply ;;
  *)
    echo "Usage: $(basename "$0") <generate|apply|delete|build-push|build-apply>"
    echo ""
    echo "Environment variables:"
    echo "  NAMESPACE      (default: llm-d-wva-<random-6-chars>)"
    echo "  PLATFORM       ocp | generic | gke  (default: ocp)"
    echo "  INFRA_PROVIDER base | gke            (default: base)"
    echo "  OUTPUT_DIR     (default: <script-dir>/.generated-wva)"
    echo "  WVA_REPO_DIR   (default: <repo-root>/../llm-d-workload-variant-autoscaler)"
    echo "  WVA_IMAGE_TAG  override WVA controller image tag (set automatically by build-push)"
    exit 1
    ;;
esac
