#!/usr/bin/env bash
# Deploy the queue-based KEDA + EPP autoscaling path (keda-epp/README.md) on the AMD
# ROCm CI cluster in a single namespace. ROCm sibling of nightly-deploy-ocp-keda-epp.sh,
# using the k8s-queue overlay against the cluster's in-cluster Prometheus.
#
# Environment variables:
#   NAMESPACE             target namespace (default: keda-epp-queue-rocm-XXXX)
#   OUTPUT_DIR            where to write the generated overlay (default: mktemp -d)
#   MONITORING_NAMESPACE  namespace running Prometheus (default: from the Prometheus CR)
#   PROMETHEUS_ADDRESS    Prometheus endpoint KEDA queries (default: prometheus-operated)
#   EPP_SERVICE           EPP service name (default: discovered after the router install)
#   MODEL_NAME            model_name label in the trigger queries (default: Qwen/Qwen3-32B)
#   ROUTER_CHART_VERSION_OVERRIDE  EPP router chart version (default: v0.9.0)

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

NAMESPACE="${NAMESPACE:-keda-epp-queue-rocm-$(printf '%04x' $RANDOM)}"
SCALEDOBJECT=optimized-baseline-keda-epp
DECODE_DEPLOYMENT=optimized-baseline-rocm-vllm-decode
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"
# Must be unique on the cluster, since a second `optimized-baseline` InferencePool
# collides with the other nightlies. The workflow's gateway_host is derived from it.
ROUTER_RELEASE=keda-epp-rocm
OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d -t nightly-deploy-amd-keda-epp.XXXXXX)}"
# env.sh's rolling `v0` tag carries llm-d-router#1681, which drops the EPP Service when
# flowControl or monitoring is enabled. Pinned as in the OCP sibling.
ROUTER_CHART_VERSION="${ROUTER_CHART_VERSION_OVERRIDE:-v0.9.0}"

MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-$(kubectl get prometheus -A \
  -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || true)}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-llm-d-monitoring}"
if [[ -z "${PROMETHEUS_ADDRESS:-}" ]]; then
  if ! kubectl get service prometheus-operated -n "${MONITORING_NAMESPACE}" >/dev/null 2>&1; then
    echo "ERROR: no prometheus-operated Service in ${MONITORING_NAMESPACE}." >&2
    echo "       Set MONITORING_NAMESPACE or PROMETHEUS_ADDRESS." >&2
    exit 1
  fi
  PROMETHEUS_ADDRESS="http://prometheus-operated.${MONITORING_NAMESPACE}.svc.cluster.local:9090"
fi

mkdir -p "${OUTPUT_DIR}"
REL="$("${_realpath}" --relative-to="${OUTPUT_DIR}" "${REPO_ROOT}")"

echo "==> Deploying queue-based KEDA+EPP path (AMD ROCm)"
echo "  NAMESPACE:  ${NAMESPACE}"
echo "  PROMETHEUS: ${PROMETHEUS_ADDRESS}"

echo "==> Checking for KEDA"
if ! kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  echo "ERROR: CRD scaledobjects.keda.sh not found. KEDA must be installed on the cluster." >&2
  exit 1
fi

echo "==> Ensuring namespace ${NAMESPACE} exists"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "==> Installing EPP router via Helm (release: ${ROUTER_RELEASE})"
helm upgrade --install "${ROUTER_RELEASE}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
  -f "${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml" \
  -f "${REPO_ROOT}/guides/workload-autoscaling/keda-epp/optimized-baseline/router.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

if [[ -z "${EPP_SERVICE:-}" ]]; then
  echo "==> Discovering EPP Service name"
  for _ in $(seq 1 30); do
    EPP_SERVICE="$(kubectl get svc -n "${NAMESPACE}" -o name 2>/dev/null \
      | sed 's#^service/##' | grep -E -- '-epp$' | head -1 || true)"
    [[ -n "${EPP_SERVICE}" ]] && break
    sleep 10
  done
fi
if [[ -z "${EPP_SERVICE:-}" ]]; then
  echo "ERROR: no EPP Service (name ending in -epp) appeared in ${NAMESPACE} after 5m." >&2
  kubectl get all,inferencepool -n "${NAMESPACE}" >&2 2>&1 || true
  exit 1
fi
echo "  EPP_SERVICE: ${EPP_SERVICE}"
echo "  MODEL_NAME:  ${MODEL_NAME}"

# Start at the ScaledObject floor so the scale-event validator sees desiredReplicas == min.
cat > "${OUTPUT_DIR}/patch-vllm.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${DECODE_DEPLOYMENT}
spec:
  replicas: 1
EOF

echo "==> Generating overlay in ${OUTPUT_DIR}"
cat > "${OUTPUT_DIR}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ${NAMESPACE}
resources:
  - ${REL}/guides/optimized-baseline/modelserver/amd/vllm/base/
  - ${REL}/guides/workload-autoscaling/keda-epp/optimized-baseline/k8s-queue/
patches:
  # The trigger queries and serverAddress are opaque strings the namespace transformer
  # cannot reach. maxReplicaCount matches the GPUs the nightly reserves, and the shorter
  # scale-down window lets the validator observe scale-down within the run.
  - patch: |-
      - op: replace
        path: /spec/scaleTargetRef/name
        value: ${DECODE_DEPLOYMENT}
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: >-
          sum(llm_d_epp_flow_control_queue_size{namespace="${NAMESPACE}",service="${EPP_SERVICE}",model_name="${MODEL_NAME}"})
      - op: replace
        path: /spec/triggers/0/metadata/serverAddress
        value: ${PROMETHEUS_ADDRESS}
      - op: replace
        path: /spec/triggers/1/metadata/query
        value: >-
          sum(llm_d_epp_request_running{namespace="${NAMESPACE}",service="${EPP_SERVICE}",model_name="${MODEL_NAME}"})
      - op: replace
        path: /spec/triggers/1/metadata/serverAddress
        value: ${PROMETHEUS_ADDRESS}
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleDown/stabilizationWindowSeconds
        value: 60
    target:
      kind: ScaledObject
      name: ${SCALEDOBJECT}
  - path: patch-vllm.yaml
    target:
      kind: Deployment
      name: ${DECODE_DEPLOYMENT}
EOF

echo "==> Validating kustomization"
kubectl kustomize "${OUTPUT_DIR}" >/dev/null

echo "==> Applying kustomize overlay"
kubectl apply -k "${OUTPUT_DIR}"

# Model and image are pulled cold on a fresh node.
echo "==> Waiting for the decode modelserver to become ready"
kubectl rollout status deployment/"${DECODE_DEPLOYMENT}" \
  -n "${NAMESPACE}" --timeout=40m

# The v0.9.0 EPP creates the per-model series on the first request, not at idle.
echo "==> Warming up the EPP to register the queue metrics"
kubectl run keda-epp-warmup -n "${NAMESPACE}" --image=curlimages/curl:8.10.1 \
  --restart=Never --rm -i --quiet --command -- sh -c '
    for i in 1 2 3 4 5; do
      curl -sS --max-time 30 -o /dev/null -w "  warmup req $i: HTTP %{http_code}\n" \
        -X POST "http://'"${EPP_SERVICE}"':80/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"'"${MODEL_NAME}"'\",\"prompt\":\"warmup\",\"max_tokens\":1}" || true
      sleep 3
    done' || echo "  (warmup pod did not complete cleanly, continuing)"

# KEDA reads a query that matches no series as 0 (ignoreNullValues defaults to true), so
# a wrong label or an unscraped EPP still leaves the ScaledObject Ready but unable to scale.
echo "==> Verifying Prometheus has the series both triggers query"
SELECTOR="{namespace=\"${NAMESPACE}\",service=\"${EPP_SERVICE}\",model_name=\"${MODEL_NAME}\"}"
if ! kubectl run keda-epp-promcheck -n "${NAMESPACE}" --image=curlimages/curl:8.10.1 \
  --restart=Never --rm -i --quiet \
  --env="PROM=${PROMETHEUS_ADDRESS}" --env="SELECTOR=${SELECTOR}" \
  --command -- sh -c '
    i=0
    while [ "$i" -lt 30 ]; do
      missing=""
      for m in llm_d_epp_request_running llm_d_epp_flow_control_queue_size; do
        curl -sS --max-time 10 -G --data-urlencode "query=${m}${SELECTOR}" \
          "${PROM}/api/v1/query" 2>/dev/null | grep -q "\"result\":\[{" || missing="$missing $m"
      done
      if [ -z "$missing" ]; then echo "  both series present"; exit 0; fi
      i=$((i + 1))
      sleep 10
    done
    echo "  missing after 5m:$missing"
    exit 1'; then
  echo "ERROR: Prometheus at ${PROMETHEUS_ADDRESS} has no series matching ${SELECTOR}." >&2
  kubectl get servicemonitor,endpoints -n "${NAMESPACE}" >&2 2>&1 || true
  exit 1
fi

# Without spec.fallback, KEDA reports trigger query errors as Ready=False.
echo "==> Waiting for the ScaledObject to be Ready"
kubectl wait scaledobject/"${SCALEDOBJECT}" \
  -n "${NAMESPACE}" --for=condition=Ready --timeout=300s

kubectl get scaledobject,hpa -n "${NAMESPACE}"
