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
SCALEDOBJECT=optimized-baseline-nvidia-gpu-vllm-decode-scaler
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
  - ${REL}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda/ocp/
patches:
  # The namespace lives inside a PromQL string, so the kustomize namespace transformer above
  # cannot reach it — rewrite the query explicitly. maxReplicaCount is capped at the GPU budget
  # the nightly reserves (2), rather than the guide's default of 10.
  # NB: variant_name is the ScaledObject's name (-scaler suffix), not the Deployment's.
  - patch: |-
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: |
          wva_desired_replicas{
            variant_name="optimized-baseline-nvidia-gpu-vllm-decode-scaler",
            namespace="${NAMESPACE}"
          }
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
      # The deployment starts at 2 replicas while WVA, seeing no traffic yet, asks for 1. KEDA
      # would scale down within the 60s guide default — mid-startup, while the workflow is still
      # waiting on the pods it listed before the scale-down, which then fails on a NotFound.
      # vLLM needs ~6 min to become ready here, so hold scale-down off until the stack is up and
      # the benchmark is driving load.
      # TODO: interim. The real fix is to start the deployment at 1 replica (the floor WVA asks
      # for when idle) and let the benchmark drive scale-up, rather than pinning 2 and delaying
      # the scale-down that follows.
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleDown/stabilizationWindowSeconds
        value: 900
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
# Ready only means KEDA accepted the trigger and created its HPA. It does NOT mean the metric
# pipeline works — see the Fallback check below.
kubectl wait scaledobject/"${SCALEDOBJECT}" \
  -n "${NAMESPACE}" --for=condition=Ready --timeout=300s

# Assert the metric pipeline is live: vLLM/EPP -> Prometheus -> WVA -> wva_desired_replicas ->
# KEDA -> HPA. If KEDA cannot query Prometheus (401, wrong label, no such series) it suppresses
# the error and serves `fallback: replicas`, so the deployment comes up healthy, the HPA reports
# a plausible metric value, and the ScaledObject still says Ready=True and Active=True. The only
# signal that any of it is real is Fallback=False. Without this check the nightly passes green
# while autoscaling is dead.
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
  kubectl get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" \
    -o jsonpath='{range .status.conditions[*]}  {.type}={.status} ({.reason}: {.message}){"\n"}{end}' >&2
  echo "--- KEDA operator errors for this ScaledObject ---" >&2
  kubectl logs -n openshift-keda -l app=keda-operator --tail=200 2>/dev/null \
    | grep -i "${NAMESPACE}" | tail -10 >&2 || true
  exit 1
fi

echo "==> Listing autoscaling resources"
# KEDA owns the HPA (wva-keda-hpa-*); we no longer create one ourselves.
kubectl get scaledobject,hpa -n "${NAMESPACE}"
