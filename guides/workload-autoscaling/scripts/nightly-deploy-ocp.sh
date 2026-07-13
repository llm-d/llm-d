#!/usr/bin/env bash
# Deploy the WVA + optimized-baseline stack on OpenShift in a single namespace.
# Same code path for CI nightly runs and local development.
#
# Environment variables:
#   NAMESPACE             target namespace for ALL resources (default: llm-d-optimized-baseline)
#   WVA_TAG               WVA controller image tag override (default: unset = upstream default)
#   OUTPUT_DIR            where to write the generated overlay (default: mktemp -d)
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

NAMESPACE="${NAMESPACE:-wva-nightly-optimized-baseline-$(printf '%04x' $RANDOM)}"
# Short hash used as a suffix on ClusterRoleBindings to make them unique per namespace.
NS_HASH="$(printf '%s' "${NAMESPACE}" | sha256sum | cut -c1-8)"
WVA_TAG="${WVA_TAG:-}"
OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d -t nightly-deploy-ocp.XXXXXX)}"
ROUTER_CHART_VERSION="${ROUTER_CHART_VERSION}"

mkdir -p "${OUTPUT_DIR}"

cp "${SCRIPT_DIR}/../wva-config/base/patch-vllm.yaml" "${OUTPUT_DIR}/patch-vllm.yaml"

REL="$("${_realpath}" --relative-to="${OUTPUT_DIR}" "${REPO_ROOT}")"

echo "Generating overlay in ${OUTPUT_DIR}"
echo "  NAMESPACE: ${NAMESPACE}"
[[ -n "${WVA_TAG}" ]] && echo "  WVA_TAG:   ${WVA_TAG}"

cat > "${OUTPUT_DIR}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ${NAMESPACE}
resources:
  - ${REL}/guides/workload-autoscaling/wva-config/platform/ocp/
  - ${REL}/guides/optimized-baseline/modelserver/gpu/vllm/base/
  - ${REL}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda/
patches:
  # The namespace lives inside a PromQL string, so the kustomize namespace transformer above
  # cannot reach it — rewrite the query explicitly. maxReplicaCount is capped at the GPU budget
  # the nightly reserves (2), rather than the guide's default of 10.
  - patch: |-
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: |
          wva_desired_replicas{
            variant_name="optimized-baseline-nvidia-gpu-vllm-decode",
            namespace="${NAMESPACE}"
          }
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
    target:
      kind: ScaledObject
      name: optimized-baseline-nvidia-gpu-vllm-decode-scaler
  - path: patch-vllm.yaml
    target:
      kind: Deployment
      name: optimized-baseline-nvidia-gpu-vllm-decode
EOF

# ClusterRoleBindings are cluster-scoped; append a namespace hash so concurrent
# deployments to different namespaces do not collide on the same CRB name.
for crb in \
  wva-manager-clusterrolebinding \
  wva-metrics-auth-rolebinding \
  wva-epp-metrics-reader-clusterrolebinding \
  wva-manager-cluster-monitoring-view \
  wva-prometheus-cluster-monitoring-view; do
  cat >> "${OUTPUT_DIR}/kustomization.yaml" <<EOF
  - patch: |-
      - op: replace
        path: /metadata/name
        value: ${crb}-${NS_HASH}
    target:
      kind: ClusterRoleBinding
      name: ${crb}
EOF
done

if [[ -n "${WVA_TAG}" ]]; then
  # The upstream base kustomization already rewrites image name "controller" to
  # ghcr.io/llm-d/llm-d-workload-variant-autoscaler. Match the rewritten name here.
  cat >> "${OUTPUT_DIR}/kustomization.yaml" <<EOF
images:
  - name: ghcr.io/llm-d/llm-d-workload-variant-autoscaler
    newTag: ${WVA_TAG}
EOF
fi

echo "==> Validating kustomization"
kubectl kustomize "${OUTPUT_DIR}" >/dev/null

# KEDA is the external metrics provider (Prometheus Adapter was retired upstream in
# llm-d-workload-variant-autoscaler#1399). WVA only registers its ScaledObject reconciler
# if the KEDA CRD is present when the controller starts, so check before deploying it.
# On OpenShift, KEDA is expected to be operator-managed and is never installed from here.
echo "==> Checking for KEDA"
if ! kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  echo "ERROR: CRD scaledobjects.keda.sh not found." >&2
  echo "       KEDA must be installed on the cluster (Custom Metrics Autoscaler operator on OpenShift)" >&2
  echo "       before the WVA controller starts, or WVA will not watch ScaledObjects." >&2
  exit 1
fi

echo "==> Ensuring namespace ${NAMESPACE} exists"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "==> Installing EPP router via Helm"
helm install workload-variant-autoscaler-inferencepool-standalone \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

echo "==> Applying kustomize overlay"
kubectl apply -k "${OUTPUT_DIR}"

echo "==> Waiting for WVA controller to become Available"
kubectl wait deployment/wva-controller-manager \
  -n "${NAMESPACE}" --for=condition=Available --timeout=300s

echo "==> Waiting for the ScaledObject to be Ready"
# Ready means KEDA accepted the trigger and created its HPA. It does NOT mean WVA is
# publishing wva_desired_replicas yet — that needs a Prometheus scrape cycle.
kubectl wait scaledobject/optimized-baseline-nvidia-gpu-vllm-decode-scaler \
  -n "${NAMESPACE}" --for=condition=Ready --timeout=300s

echo "==> Listing autoscaling resources"
# KEDA owns the HPA (wva-keda-hpa-*); we no longer create one ourselves.
kubectl get scaledobject,hpa -n "${NAMESPACE}"
