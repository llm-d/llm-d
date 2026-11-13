#!/usr/bin/env bash
# Deploy the queue-based KEDA + EPP autoscaling path (keda-epp/README.md) on the
# GKE e2e cluster in a single namespace. GKE sibling of nightly-deploy-amd-keda-epp.sh:
# the overlays/k8s/queue ScaledObject against Google Managed Prometheus (GMP).
#
# Monitoring on GKE: the router is installed with recipes/router/features/
# monitoring-gke.values.yaml, so the chart creates an authenticated GMP
# PodMonitoring for the EPP (managed collection scrapes it; no Prometheus Operator
# ServiceMonitor). KEDA queries GMP through its standalone Prometheus frontend --
# a stateless in-cluster proxy that authenticates to the Cloud Monitoring API on
# the cluster's behalf -- over plain HTTP, so the ScaledObject needs no
# TriggerAuthentication and no GCP project ID. Like KEDA itself, the frontend is
# shared cluster infrastructure (see fast-model-actuation-keda/README.md, "GKE"
# note under step 6, for the one-time admin steps).
#
# Environment variables:
#   NAMESPACE             target namespace (default: keda-epp-queue-gke-XXXX)
#   OUTPUT_DIR            where to write the generated overlay (default: mktemp -d)
#   MONITORING_NAMESPACE  namespace running the GMP frontend (default: llm-d-monitoring)
#   PROMETHEUS_ADDRESS    Prometheus-compatible endpoint KEDA queries
#                         (default: http://frontend.<MONITORING_NAMESPACE>.svc.cluster.local:9090)
#   EPP_SERVICE           EPP service name (default: <router release>-epp)
#   MODEL_NAME            model_name label in the trigger queries (default: Qwen/Qwen3-32B)

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

NAMESPACE="${NAMESPACE:-keda-epp-queue-gke-$(printf '%04x' $RANDOM)}"
SCALEDOBJECT=optimized-baseline-keda-epp
DECODE_DEPLOYMENT=optimized-baseline-nvidia-gpu-vllm-decode
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"
# Must be unique on the cluster, since a second `optimized-baseline` InferencePool
# collides with the other nightlies. The workflow's gateway_host is derived from it.
ROUTER_RELEASE=keda-epp-gke
OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d -t nightly-deploy-gke-keda-epp.XXXXXX)}"

MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-llm-d-monitoring}"
FRONTEND_ADDRESS="http://frontend.${MONITORING_NAMESPACE}.svc.cluster.local:9090"
PROMETHEUS_ADDRESS="${PROMETHEUS_ADDRESS:-${FRONTEND_ADDRESS}}"

mkdir -p "${OUTPUT_DIR}"
REL="$("${_realpath}" --relative-to="${OUTPUT_DIR}" "${REPO_ROOT}")"

echo "==> Deploying queue-based KEDA+EPP path (GKE, Google Managed Prometheus)"
echo "  NAMESPACE:  ${NAMESPACE}"
echo "  PROMETHEUS: ${PROMETHEUS_ADDRESS}"

echo "==> Checking cluster prerequisites"
if ! kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  echo "ERROR: CRD scaledobjects.keda.sh not found. KEDA must be installed on the cluster." >&2
  exit 1
fi
if ! kubectl get crd podmonitorings.monitoring.googleapis.com >/dev/null 2>&1; then
  echo "ERROR: CRD podmonitorings.monitoring.googleapis.com not found. Enable GMP managed collection on the cluster." >&2
  exit 1
fi
# The frontend is only a prerequisite when KEDA queries it.
if [[ "${PROMETHEUS_ADDRESS}" == "${FRONTEND_ADDRESS}" ]] \
  && ! kubectl get service frontend -n "${MONITORING_NAMESPACE}" >/dev/null 2>&1; then
  echo "ERROR: no GMP frontend Service in ${MONITORING_NAMESPACE} (expected for ${PROMETHEUS_ADDRESS})." >&2
  echo "       Deploy the GMP standalone Prometheus frontend there, or set PROMETHEUS_ADDRESS." >&2
  exit 1
fi

echo "==> Ensuring namespace ${NAMESPACE} exists"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "==> Installing EPP router via Helm (release: ${ROUTER_RELEASE})"
helm upgrade --install "${ROUTER_RELEASE}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
  -f "${REPO_ROOT}/guides/recipes/router/features/monitoring-gke.values.yaml" \
  -f "${REPO_ROOT}/guides/workload-autoscaling/keda-epp/optimized-baseline/router.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

# Same name the EPP rollout wait below and the workflow's gateway_host use.
EPP_SERVICE="${EPP_SERVICE:-${ROUTER_RELEASE}-epp}"
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
  - ${REL}/guides/optimized-baseline/modelserver/gpu/vllm/gke/
  - ${REL}/guides/workload-autoscaling/keda-epp/optimized-baseline/overlays/k8s/queue/
patches:
  # The trigger queries and serverAddress are opaque strings the namespace transformer
  # cannot reach. GMP managed collection labels PodMonitoring targets with
  # job=<PodMonitoring name> (the router release) and adds no "service" label, which
  # is why the selector differs from the ServiceMonitor-based k8s/ocp paths.
  # maxReplicaCount matches the GPUs the nightly reserves. The shorter stabilization
  # windows let the validator's short burst drive a scale-up and a scale-down within
  # the run. The guide defaults are 300s.
  - patch: |-
      - op: replace
        path: /metadata/name
        value: ${SCALEDOBJECT}
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/name
        value: ${SCALEDOBJECT}-hpa
      - op: replace
        path: /spec/scaleTargetRef/name
        value: ${DECODE_DEPLOYMENT}
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: >-
          sum(llm_d_epp_flow_control_queue_size{namespace="${NAMESPACE}",job="${ROUTER_RELEASE}",model_name="${MODEL_NAME}"})
      - op: replace
        path: /spec/triggers/0/metadata/serverAddress
        value: ${PROMETHEUS_ADDRESS}
      - op: replace
        path: /spec/triggers/1/metadata/query
        value: >-
          sum(llm_d_epp_request_running{namespace="${NAMESPACE}",job="${ROUTER_RELEASE}",model_name="${MODEL_NAME}"})
      - op: replace
        path: /spec/triggers/1/metadata/serverAddress
        value: ${PROMETHEUS_ADDRESS}
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleUp/stabilizationWindowSeconds
        value: 0
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleDown/stabilizationWindowSeconds
        value: 60
    target:
      kind: ScaledObject
  - path: patch-vllm.yaml
    target:
      kind: Deployment
      name: ${DECODE_DEPLOYMENT}
EOF

echo "==> Validating kustomization"
RENDERED="$(kubectl kustomize "${OUTPUT_DIR}")"
if grep -nE '\$\{[A-Z_][A-Z0-9_]*\}' <<<"${RENDERED}" >&2; then
  echo "ERROR: unresolved \${...} placeholders in the rendered manifests." >&2
  exit 1
fi

echo "==> Applying kustomize overlay"
kubectl apply -k "${OUTPUT_DIR}"

# Model and image are pulled cold on a fresh node.
echo "==> Waiting for the decode modelserver to become ready"
kubectl rollout status deployment/"${DECODE_DEPLOYMENT}" \
  -n "${NAMESPACE}" --timeout=40m

# EPP creates the per-model series on the first request, not at idle -- and only
# in the EPP process that served it, so the EPP must be the one that stays up.
# Wait for its rollout (a helm upgrade on re-run replaces the pod) before warming up.
echo "==> Waiting for the EPP to be ready"
kubectl rollout status deployment/"${ROUTER_RELEASE}-epp" -n "${NAMESPACE}" --timeout=10m

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
# GMP ingests scrapes with a short delay on top of the PodMonitoring interval; the 5m
# budget below covers it.
echo "==> Verifying Prometheus has the series both triggers query"
SELECTOR="{namespace=\"${NAMESPACE}\",job=\"${ROUTER_RELEASE}\",model_name=\"${MODEL_NAME}\"}"
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
      if [ -z "$missing" ]; then exit 0; fi
      i=$((i + 1))
      sleep 10
    done
    echo "  missing after 5m:$missing"
    exit 1'; then
  echo "ERROR: Prometheus at ${PROMETHEUS_ADDRESS} has no series matching ${SELECTOR}." >&2
  kubectl get podmonitoring,endpoints -n "${NAMESPACE}" >&2 2>&1 || true
  exit 1
fi
# Printed here, not in the pod: `kubectl run -i` can miss the output of a pod
# that exits right away, which left the log without the confirmation.
echo "  both series present"

# Without spec.fallback, KEDA reports trigger query errors as Ready=False.
echo "==> Waiting for the ScaledObject to be Ready"
kubectl wait scaledobject/"${SCALEDOBJECT}" \
  -n "${NAMESPACE}" --for=condition=Ready --timeout=300s

kubectl get scaledobject,hpa -n "${NAMESPACE}"
