#!/usr/bin/env bash
# Deploy the inflight-based KEDA + EPP autoscaling path (keda-epp/README.md) on
# OpenShift in a single namespace. Same code path for CI nightly runs and local
# development.
#
# This is the EPP-direct sibling of nightly-deploy-ocp.sh (the WVA path). It
# deploys the SAME optimized-baseline modelserver + EPP router, but drives
# autoscaling directly from the stock EPP inflight-requests metric via KEDA —
# there is NO WVA controller on this path and NO EPP Flow Control is required.
# The optimized-baseline EPP already ships the inflight-load-producer plugin
# that emits llm_d_epp_inflight_requests, so the router gets only the monitoring
# feature values; no flow-control router values are applied.
#
# This script is flow-control-off: it scales on llm_d_epp_inflight_requests
# alone, without llm_d_epp_request_running (the queue path uses both) and
# without any flow-control subsystem at all. The inflight signal is more
# responsive than queue (lagging backlog) or saturation (sampled utilization)
# because inflight requests rise the moment a new replica accepts traffic.
#
# Environment variables:
#   NAMESPACE     target namespace for ALL resources (default: keda-epp-inflight-nightly-XXXX)
#   OUTPUT_DIR    where to write the generated overlay (default: mktemp -d)
#   EPP_SERVICE   override the EPP service name in the trigger queries
#                 (default: auto-discovered from the namespace after the router install)
#   MODEL_NAME    model_name label in the trigger queries (default: Qwen/Qwen3-32B)
#   ROUTER_CHART_VERSION_OVERRIDE  EPP router chart version. Defaults to env.sh's
#                 ROUTER_CHART_VERSION (`v0`, the rolling latest), matching the
#                 optimized-baseline guide. Set this to test a specific version.

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

NAMESPACE="${NAMESPACE:-keda-epp-inflight-nightly-$(printf '%04x' $RANDOM)}"
SCALEDOBJECT=optimized-baseline-keda-epp
DECODE_DEPLOYMENT=optimized-baseline-nvidia-gpu-vllm-decode
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"
# EPP router Helm release. Must be UNIQUE on the cluster: `optimized-baseline`
# collides with the many existing optimized-baseline InferencePools on the shared
# nightly cluster and sends the EPP into a hot reconcile loop (NOT_SERVING). The
# EPP Service name is release-derived, so we discover it at runtime rather than
# hardcoding it (the ScaledObject query is templated to whatever is discovered).
ROUTER_RELEASE=keda-epp-inflight
# Short hash used as a suffix on the ClusterRoleBinding to make it unique per namespace.
NS_HASH="$(printf '%s' "${NAMESPACE}" | sha256sum | cut -c1-8)"
OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d -t nightly-deploy-ocp-keda-epp-inflight.XXXXXX)}"
# Track env.sh's ROUTER_CHART_VERSION (default `v0`) so this matches the
# optimized-baseline guide.
ROUTER_CHART_VERSION="${ROUTER_CHART_VERSION_OVERRIDE:-${ROUTER_CHART_VERSION}}"

mkdir -p "${OUTPUT_DIR}"
REL="$("${_realpath}" --relative-to="${OUTPUT_DIR}" "${REPO_ROOT}")"

echo "==> Deploying inflight-based KEDA+EPP path (flow-control-off)"
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
# Values are layered: base -> optimized-baseline -> monitoring.
# Unlike the queue/saturation paths, we do NOT add the keda-epp router.values.yaml
# here because that file turns on EPP Flow Control, and inflight needs none:
# every optimized-baseline EPP already emits llm_d_epp_inflight_requests via its
# inflight-load-producer plugin. The helm install below therefore stays at the
# monitoring feature values that the optimized-baseline guide already deploys.
helm install "${ROUTER_RELEASE}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
  -f "${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

# Discover the EPP Service name the chart created (release-derived; release
# `keda-epp-inflight` yields Service `keda-epp-inflight-epp`). The inflight metric is
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
  - ${REL}/guides/workload-autoscaling/keda-epp/optimized-baseline/overlays/ocp/inflight/
patches:
  # The leaf carries \${...} placeholders that apply -k cannot resolve, so pin
  # the target Deployment and the trigger query here. maxReplicaCount is
  # capped at the GPU budget the nightly reserves (2), rather than the guide's
  # default of 8. Both scaleUp and scaleDown stabilizationWindowSeconds are
  # lowered 300 -> 60 so the scale-event validator can observe the scale-UP and
  # scale-DOWN decisions within the run; at the guide default of 300 the HPA
  # holds scale-up for up to 5 min (ScaleUpStabilized), past the validator's
  # timeout. Nightly only; the guide default stays 300.
  - patch: |-
      - op: replace
        path: /spec/scaleTargetRef/name
        value: ${DECODE_DEPLOYMENT}
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: >-
          sum(llm_d_epp_inflight_requests{namespace="${NAMESPACE}",service="${EPP_SERVICE}",model_name="${MODEL_NAME}"})
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleUp/stabilizationWindowSeconds
        value: 60
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleDown/stabilizationWindowSeconds
        value: 60
    target:
      kind: ScaledObject
      name: ${SCALEDOBJECT}
  # ClusterRoleBindings are cluster-scoped; suffix a namespace hash so concurrent
  # deployments do not collide. The subject namespace is pinned too, or the
  # binding grants the wrong namespace and KEDA's Thanos queries get 401/403.
  # Match the leaf's already renamed binding by name prefix.
  - patch: |-
      - op: replace
        path: /metadata/name
        value: keda-epp-metrics-reader-monitoring-view-${NS_HASH}
      - op: replace
        path: /subjects/0/namespace
        value: ${NAMESPACE}
    target:
      kind: ClusterRoleBinding
      name: keda-epp-metrics-reader-monitoring-view.*
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

# llm_d_epp_inflight_requests is emitted by the inflight-load-producer plugin
# that is part of every optimized-baseline EPP; unlike the queue metric (which is
# flow-control subsystem and lazy-on-first-request), inflight_requests is a
# direct load signal from the EPP itself and appears as soon as the decode modelserver
# is ready, even at idle (values rise from 0 as soon as traffic arrives). So wait
# for the modelserver before the Fallback check below; the HPA needs a ready
# backend endpoint to target. vLLM startup (image pull + model load) commonly
# takes several minutes.
echo "==> Waiting for the decode modelserver to become ready (vLLM startup)"
kubectl rollout status deployment/"${DECODE_DEPLOYMENT}" \
  -n "${NAMESPACE}" --timeout=20m

# Best-effort warmup: send a single request so the EPP exercises the full request
# path and the inflight_requests metric rises above 0. Runs in-cluster (a curl
# pod) because the ClusterIP EPP Service isn't reachable from the CI runner
# directly. If the warmup can't reach the EPP the Fallback gate below still catches
# a dead pipeline.
echo "==> Warming up the EPP to register live inflight requests"
kubectl run keda-epp-warmup -n "${NAMESPACE}" --image=curlimages/curl:8.10.1 \
  --restart=Never --rm -i --quiet --command -- sh -c '
    for i in 1 2 3 4 5; do
      curl -sS --max-time 30 -o /dev/null -w "  warmup req $i: HTTP %{http_code} inflight_requests should rise\n" \
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
  echo "       the EPP inflight_requests metric. Replica count is coming from spec.fallback." >&2
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
