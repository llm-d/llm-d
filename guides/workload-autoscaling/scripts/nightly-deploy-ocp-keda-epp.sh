#!/usr/bin/env bash
# Deploy the queue-based KEDA + EPP autoscaling path (README.hpa-epp.md) on
# OpenShift in a single namespace. Same code path for CI nightly runs and local
# development.
#
# This is the EPP-direct sibling of nightly-deploy-ocp.sh (the WVA path). It
# deploys the SAME optimized-baseline modelserver + EPP router, but drives
# autoscaling directly from EPP queue metrics via KEDA — there is NO WVA
# controller on this path. KEDA reads `llm_d_epp_flow_control_queue_size` (and
# `llm_d_epp_request_running`) from Thanos and scales the decode Deployment.
#
# Environment variables:
#   NAMESPACE     target namespace for ALL resources (default: keda-epp-queue-nightly-XXXX)
#   OUTPUT_DIR    where to write the generated overlay (default: mktemp -d)
#   EPP_SERVICE   override the EPP service name in the trigger queries
#                 (default: auto-discovered from the namespace after the router install)
#   MODEL_NAME    model_name label in the trigger queries (default: Qwen/Qwen3-32B)
#   ROUTER_CHART_VERSION_OVERRIDE  EPP router chart version. Default v0.9.0 (a
#                 released tag), deliberately NOT env.sh's mutable `v0` — see the
#                 note at the ROUTER_CHART_VERSION assignment below for why.

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

NAMESPACE="${NAMESPACE:-keda-epp-queue-nightly-$(printf '%04x' $RANDOM)}"
SCALEDOBJECT=optimized-baseline-keda-epp
DECODE_DEPLOYMENT=optimized-baseline-nvidia-gpu-vllm-decode
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"
# EPP router Helm release. Must be UNIQUE on the cluster: `optimized-baseline`
# collides with the many existing optimized-baseline InferencePools on the shared
# nightly cluster and sends the EPP into a hot reconcile loop (NOT_SERVING). The
# EPP Service name is release-derived, so we discover it at runtime rather than
# hardcoding it (the ScaledObject query is templated to whatever is discovered).
ROUTER_RELEASE=keda-epp-queue
# Short hash used as a suffix on the ClusterRoleBinding to make it unique per namespace.
NS_HASH="$(printf '%s' "${NAMESPACE}" | sha256sum | cut -c1-8)"
OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d -t nightly-deploy-ocp-keda-epp.XXXXXX)}"
# Pin to the latest RELEASED router chart rather than env.sh's mutable `v0` tag.
# `v0` currently carries llm-d-router#1681, which breaks EPP Service creation when
# flowControl/monitoring are enabled (helm reports deployed but the -epp Service is
# never created), leaving the queue metric unscrapeable. `v0.9.0` predates #1681 and
# works. Tracked in llm-d#2207 (pin guides repo-wide) and llm-d-router#2323 (chart
# fix). Override once the rolling tag is fixed.
ROUTER_CHART_VERSION="${ROUTER_CHART_VERSION_OVERRIDE:-v0.9.0}"

mkdir -p "${OUTPUT_DIR}"
REL="$("${_realpath}" --relative-to="${OUTPUT_DIR}" "${REPO_ROOT}")"

echo "==> Deploying queue-based KEDA+EPP path"
echo "  NAMESPACE: ${NAMESPACE}"

# KEDA is the external metrics provider. On OpenShift it is operator-managed
# (Custom Metrics Autoscaler); this path never installs it.
echo "==> Checking for KEDA"
if ! kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  echo "ERROR: CRD scaledobjects.keda.sh not found." >&2
  echo "       KEDA must be installed on the cluster (Custom Metrics Autoscaler operator on OpenShift)." >&2
  exit 1
fi

echo "==> Ensuring namespace ${NAMESPACE} exists"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
# Enable user-workload-monitoring to scrape this namespace's ServiceMonitors so
# the EPP metrics reach Thanos (matches the WVA path's OpenShift setup).
kubectl label namespace "${NAMESPACE}" openshift.io/user-monitoring=true --overwrite

echo "==> Installing EPP router via Helm (release: ${ROUTER_RELEASE})"
# Values are layered: base -> optimized-baseline -> monitoring -> keda-epp-queue.
#   monitoring.values.yaml     enables the router's Prometheus ServiceMonitor.
#   keda-epp-queue router.values.yaml enables the flowControl feature gate — the
#     source of llm_d_epp_flow_control_queue_size — plus the queue-scoring EPP
#     plugins. Without these two the queue metric is never emitted or scraped.
helm install "${ROUTER_RELEASE}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
  -f "${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml" \
  -f "${REPO_ROOT}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-queue/router.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

# Discover the EPP Service name the chart created (release-derived; release
# `keda-epp-queue` yields Service `keda-epp-queue-epp`). The queue metric is
# labelled by this `service` value, so the ScaledObject query must match it exactly.
# We discover rather than hardcode so the nightly still works if the release name or
# chart naming changes.
#
# The EPP Service is not present the instant `helm install` returns — the router/EPP
# resources take a few seconds to appear — so poll rather than check once.
if [[ -z "${EPP_SERVICE:-}" ]]; then
  echo "==> Discovering EPP Service name (waiting for the router/EPP to come up)"
  for _ in $(seq 1 30); do
    EPP_SERVICE="$(kubectl get svc -n "${NAMESPACE}" -o name 2>/dev/null \
      | sed 's#^service/##' | grep -E -- '-epp$' | head -1 || true)"
    [[ -n "${EPP_SERVICE}" ]] && break
    sleep 10
  done
fi
if [[ -z "${EPP_SERVICE:-}" ]]; then
  echo "ERROR: no EPP Service (name ending in -epp) appeared in ${NAMESPACE} after 5m." >&2
  echo "       Set EPP_SERVICE explicitly, or check the router install." >&2
  echo "--- resources in ${NAMESPACE} ---" >&2
  kubectl get all,inferencepool -n "${NAMESPACE}" >&2 2>&1 || true
  exit 1
fi
echo "  EPP_SERVICE: ${EPP_SERVICE}"
echo "  MODEL_NAME:  ${MODEL_NAME}"

# Nightly patch for the decode Deployment: GPU scheduling priority + triton cache,
# and start at 1 replica (the ScaledObject floor) so load — not a pinned count —
# drives scale-up, and there is no idle scale-down race during the ~6 min vLLM
# startup. maxReplicaCount is capped at the GPU budget the nightly reserves (2).
cat > "${OUTPUT_DIR}/patch-vllm.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${DECODE_DEPLOYMENT}
spec:
  replicas: 1
  template:
    spec:
      priorityClassName: nightly-gpu-critical
      volumes:
        - name: triton-cache
          emptyDir: {}
      containers:
        - name: modelserver
          volumeMounts:
            - mountPath: /.triton
              name: triton-cache
EOF

echo "==> Generating overlay in ${OUTPUT_DIR}"
cat > "${OUTPUT_DIR}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ${NAMESPACE}
resources:
  - ${REL}/guides/optimized-baseline/modelserver/gpu/vllm/base/
  - ${REL}/guides/workload-autoscaling/optimized-baseline-autoscaling/keda-epp-queue/ocp/
patches:
  # The namespace/service/model_name live inside opaque PromQL strings that the
  # kustomize namespace transformer cannot reach — rewrite both trigger queries
  # explicitly to match the deployed EPP. maxReplicaCount is capped at the GPU
  # budget the nightly reserves (2), rather than the guide's default of 8.
  # scaleDown.stabilizationWindowSeconds is lowered 300 -> 60 so the scale-event
  # validator can observe scale-DOWN within the run instead of waiting out the
  # production 5-min debounce. Nightly-overlay-only — the guide default stays 300
  # (the long window guards the vLLM-startup scale-down race in production).
  - patch: |-
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: >-
          sum(llm_d_epp_flow_control_queue_size{namespace="${NAMESPACE}",service="${EPP_SERVICE}",model_name="${MODEL_NAME}"})
      - op: replace
        path: /spec/triggers/1/metadata/query
        value: >-
          sum(llm_d_epp_request_running{namespace="${NAMESPACE}",service="${EPP_SERVICE}",model_name="${MODEL_NAME}"})
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleDown/stabilizationWindowSeconds
        value: 60
    target:
      kind: ScaledObject
      name: ${SCALEDOBJECT}
  # ClusterRoleBindings are cluster-scoped; suffix a namespace hash so concurrent
  # deployments to different namespaces do not collide on the same CRB name.
  # Also rewrite the subject namespace explicitly: kustomize's `namespace`
  # transformer does NOT rewrite a ClusterRoleBinding subject's namespace when an
  # inner overlay (ocp/, namespace llm-d-optimized-baseline) already set it, so the
  # binding would grant the SA in the WRONG namespace → KEDA's Thanos queries 401/403
  # → TriggerError (ScaledObject never Ready). Pin the subject to this deployment's ns.
  - patch: |-
      - op: replace
        path: /metadata/name
        value: keda-epp-metrics-reader-monitoring-view-${NS_HASH}
      - op: replace
        path: /subjects/0/namespace
        value: ${NAMESPACE}
    target:
      kind: ClusterRoleBinding
      name: keda-epp-metrics-reader-monitoring-view
  - path: patch-vllm.yaml
    target:
      kind: Deployment
      name: ${DECODE_DEPLOYMENT}
EOF

echo "==> Validating kustomization"
kubectl kustomize "${OUTPUT_DIR}" >/dev/null

echo "==> Applying kustomize overlay"
kubectl apply -k "${OUTPUT_DIR}"

# llm_d_epp_flow_control_queue_size is a PER-MODEL series that only exists once the
# EPP has a ready backend endpoint — i.e. the decode modelserver is up (verified on
# pokprod: an idle pool with ready_endpoints=1 and zero traffic still reports
# queue_size=0, but a pool with no ready endpoint reports no series at all). So wait
# for the modelserver before the Fallback check below, or KEDA would query an empty
# series and the gate would fail before the stack is even ready. vLLM startup (image
# pull + model load) commonly takes several minutes.
echo "==> Waiting for the decode modelserver to become ready (vLLM startup)"
kubectl rollout status deployment/"${DECODE_DEPLOYMENT}" \
  -n "${NAMESPACE}" --timeout=20m

# The v0.9.0 EPP creates the per-model `flow_control_queue_size` / `request_running`
# series LAZILY — on the first request through flow control, not at idle (even with a
# ready endpoint). Send a short warmup so the series exist before the Fallback gate
# queries Thanos for them; otherwise KEDA's trigger errors on an empty series and the
# ScaledObject never goes Ready. Best-effort (`|| true`): if the warmup can't reach the
# EPP, the Fallback gate below still catches a dead pipeline. Runs in-cluster (a curl
# pod) because the ClusterIP EPP Service isn't reachable from the CI runner directly.
echo "==> Warming up the EPP to register the queue metrics (v0.9.0 lazy series)"
kubectl run keda-epp-warmup -n "${NAMESPACE}" --image=curlimages/curl:8.10.1 \
  --restart=Never --rm -i --quiet --command -- sh -c '
    for i in 1 2 3 4 5; do
      curl -sS --max-time 30 -o /dev/null -w "  warmup req $i: HTTP %{http_code}\n" \
        -X POST "http://'"${EPP_SERVICE}"':80/v1/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"'"${MODEL_NAME}"'\",\"prompt\":\"warmup\",\"max_tokens\":1}" || true
      sleep 3
    done' || echo "  (warmup pod did not complete cleanly; continuing to the gate)"

echo "==> Waiting for the ScaledObject to be Ready"
# Ready only means KEDA accepted the trigger and created its HPA. It does NOT mean
# the metric pipeline works — see the Fallback check below.
kubectl wait scaledobject/"${SCALEDOBJECT}" \
  -n "${NAMESPACE}" --for=condition=Ready --timeout=300s

# Assert the metric pipeline is live: EPP -> user-workload-monitoring -> Thanos ->
# KEDA -> HPA. If KEDA cannot query Thanos (401, wrong label, no such series) it
# suppresses the error and serves `fallback: replicas`, so the ScaledObject still
# reports Ready=True while autoscaling is dead. The only signal that any of it is
# real is Fallback=False. Require it to HOLD: it reads False before KEDA's first
# poll, so a single early reading is meaningless.
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
  echo "       the EPP queue metric. Replica count is coming from spec.fallback." >&2
  kubectl get scaledobject/"${SCALEDOBJECT}" -n "${NAMESPACE}" \
    -o jsonpath='{range .status.conditions[*]}  {.type}={.status} ({.reason}: {.message}){"\n"}{end}' >&2
  echo "--- KEDA operator errors for this namespace ---" >&2
  kubectl logs -n openshift-keda -l app=keda-operator --tail=200 2>/dev/null \
    | grep -i "${NAMESPACE}" | tail -10 >&2 || true
  exit 1
fi

echo "==> Listing autoscaling resources"
# KEDA owns the HPA (keda-hpa-*); we do not create one ourselves.
kubectl get scaledobject,hpa -n "${NAMESPACE}"
