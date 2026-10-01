#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

# Nightly deploy for the multitenant async-processing guide on GKE.
#
# Invoked as the custom_deploy_script of reusable-nightly-e2e-gke.yaml by
# .github/workflows/nightly-e2e-async-multitenant-gke-acc-gpu-vllm-x.yaml.
# Runs from the repo root; the reusable exports NAMESPACE and has already
# created the namespace and the llm-d-hf-token secret.
#
# The guide has no guide.yaml, so the commands below mirror README.md steps
# 1-3 (vLLM, InferenceObjectives, router, Redis, llm-d-async) verbatim, with
# the same render() placeholder substitution the README documents. Every
# deviation from the guide is a CI-only override marked below and asserted
# after substitution so a guide edit cannot silently no-op it.
#
# The nightly also deploys the guide's optional step 4, the llm-d-router
# coordinator with its async-broker step, using the guide's own manifests and
# values. It is what lets the validator produce llm-d-async traffic over HTTP
# (X-AP-Mode: wait) from the shared benchmark harness.
#
# Environment knobs (all optional): OUTPUT_DIR (rendered files, default
# /tmp/async-multitenant.ci), ASYNC_VERSION (chart, default v0.10.0),
# COORDINATOR_IMAGE or COORDINATOR_TAG (default: ROUTER_COORDINATOR_* from
# guides/env.sh), CRD_RETRY_DELAY,
# ROLLOUT_TIMEOUT, SKIP_CRDS=true (cluster already carries the CRDs),
# VLLM_NODE_SELECTOR=key=value (GPU node selector, required on Autopilot),
# COORD_WORKERS (dispatchers in the coordinator queues' pool, default 16).

set -euo pipefail

: "${NAMESPACE:?NAMESPACE must be exported by the calling workflow}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Script-relative rather than `git rev-parse`: the deploy-script contract test
# runs this from a checkout that may not be a git work tree. guides/env.sh
# honours a pre-set REPO_ROOT.
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/../../../../.." && pwd)}"
export REPO_ROOT
# shellcheck source=guides/env.sh
source "${REPO_ROOT}/guides/env.sh"

GUIDE_NAME="async-multitenant"
MT="${REPO_ROOT}/guides/batch-serving/asynchronous-processing/multitenant"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/${GUIDE_NAME}.ci}"

ASYNC_VERSION="${ASYNC_VERSION:-v0.10.0}"
ASYNC_CHART="${ASYNC_CHART:-oci://ghcr.io/llm-d/charts/llm-d-async}"
COORDINATOR_TAG="${COORDINATOR_TAG:-${ROUTER_COORDINATOR_VERSION}}"
COORDINATOR_IMAGE="${COORDINATOR_IMAGE:-${ROUTER_COORDINATOR_IMAGE}:${COORDINATOR_TAG}}"
CRD_RETRY_DELAY="${CRD_RETRY_DELAY:-10}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-300s}"
PRIORITY_CLASS="nightly-gpu-critical"

# Single router release, hence a single InferencePool, exactly as the guide's
# single-router demo (POOL_A = POOL_B = POOL_NAME = llm-d-router).
POOL_NAME="llm-d-router"
# DNS rather than the ClusterIP the README captures interactively: stable
# across router restarts and readable in the rendered values.
IGW_HOST="llm-d-router-epp.${NAMESPACE}.svc.cluster.local"

mkdir -p "${OUTPUT_DIR}"

# Settings for the validator step. e2e-validate.sh forwards only -n and -m to
# e2e-validate-async-multitenant.sh, so AMT_* / LLMDBENCH_* variables given to
# this script (for example on a workflow's custom_deploy_script line) are
# recorded here for run.py to load. Credential-like names are never written.
env | grep -E '^(AMT|LLMDBENCH)_[A-Za-z0-9_]+=' | grep -Ev '^[^=]*(TOKEN|SECRET|PASSWORD|KEY)[^=]*=' \
  > "${OUTPUT_DIR}/validator.env" || true

die() { echo "ERROR: $*" >&2; exit 1; }

# require_pattern <file> <ERE> <message> / forbid_pattern <file> <ERE> <message>
require_pattern() { grep -qE -- "$2" "$1" || die "$3 (pattern '$2' missing from $1)"; }
forbid_pattern() { if grep -qE -- "$2" "$1"; then die "$3 (pattern '$2' present in $1)"; fi; }
# require_fixed <file> <string> <message>
require_fixed() { grep -qF -- "$2" "$1" || die "$3 ('$2' missing from $1)"; }

for tool in kubectl helm yq sed awk; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required"
done
yq --version 2>&1 | grep -q mikefarah || die "yq must be mikefarah/yq v4 (the reusable installs it via helpers/client-setup/install-deps.sh)"

# render <overlay> -> stdout. Same substitutions as the README's render()
# helper (SAT_CAP/PROM_URL are only used by the saturation overlays, which
# this lane does not deploy).
render() {
  sed -e "s/NAMESPACE/${NAMESPACE}/g" -e "s#IGW_HOST#${IGW_HOST}#g" \
      -e "s/POOL_A/${POOL_NAME}/g" -e "s/POOL_B/${POOL_NAME}/g" \
      -e "s/POOL_NAME/${POOL_NAME}/g" "$1"
}

echo "=== Installing CRDs (GAIE InferencePool + llm-d.ai InferenceObjective) ==="
# Same two applies as guides/flow-control/guide.yaml prerequisites.crds, with
# URLs resolved through guides/env.sh. Run with -x so the log records the
# resolved URLs, and retry transient GitHub/network failures (apply is
# idempotent).
CRDS_SCRIPT="${OUTPUT_DIR}/crds.sh"
cat > "${CRDS_SCRIPT}" <<EOF
set -euo pipefail
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
kubectl apply -f https://github.com/llm-d/llm-d-router/${ROUTER_RELEASE_URL}/manifests.yaml
EOF
if [ "${SKIP_CRDS:-false}" = "true" ]; then
  # For shared/dev clusters whose CRDs are managed elsewhere (the nightly
  # cluster always runs the install).
  echo "SKIP_CRDS=true: not applying CRDs (script kept at ${CRDS_SCRIPT})"
else
  for attempt in 1 2 3; do
    if bash -x "${CRDS_SCRIPT}"; then
      break
    fi
    if [ "${attempt}" -eq 3 ]; then
      die "CRD install failed after ${attempt} attempts"
    fi
    echo "CRD install failed (attempt ${attempt}); retrying in ${CRD_RETRY_DELAY}s..." >&2
    sleep "${CRD_RETRY_DELAY}"
  done
fi

echo "=== Deploying the vLLM model server ==="
# CI-only (1 of 3): manifests/vllm.yaml hardcodes the guide namespace, reads
# the HF token from a secret named hf-secret/token, and carries no
# PriorityClass. The nightly namespace, the reusable's llm-d-hf-token/HF_TOKEN
# secret and nightly-gpu-critical (created by the reusable when it exists) are
# substituted here. The llm-d.ai/inferenceServing label is what the benchmark
# harness's metrics collector selects on; it does not affect the EPP selector
# (app: vllm-1).
VLLM_MANIFEST="${OUTPUT_DIR}/vllm.yaml"
HAS_PRIORITY_CLASS=false
if kubectl get priorityclass "${PRIORITY_CLASS}" >/dev/null 2>&1; then
  HAS_PRIORITY_CLASS=true
fi
# VLLM_NODE_SELECTOR="key=value" adds a pod nodeSelector. GKE Autopilot rejects
# GPU pods at admission unless they select an accelerator or a GPU compute
# class (e.g. cloud.google.com/compute-class=<class>); the nightly's Standard
# cluster needs nothing.
VLLM_NODE_SELECTOR="${VLLM_NODE_SELECTOR:-}"  # optional; bash 5 under set -u rejects an unset ${VAR%%...}
NODE_SELECTOR_KEY="${VLLM_NODE_SELECTOR%%=*}"
NODE_SELECTOR_VALUE="${VLLM_NODE_SELECTOR#*=}"
if [ -n "${VLLM_NODE_SELECTOR:-}" ] && { [ -z "${NODE_SELECTOR_KEY}" ] || [ "${NODE_SELECTOR_KEY}" = "${VLLM_NODE_SELECTOR}" ]; }; then
  die "VLLM_NODE_SELECTOR must be key=value, got '${VLLM_NODE_SELECTOR}'"
fi
awk -v ns="${NAMESPACE}" -v pc="${PRIORITY_CLASS}" -v has_pc="${HAS_PRIORITY_CLASS}" \
    -v ns_key="${NODE_SELECTOR_KEY}" -v ns_value="${NODE_SELECTOR_VALUE}" -v has_ns="${VLLM_NODE_SELECTOR:+true}" '
  { sub(/^  namespace: llm-d-async$/, "  namespace: " ns) }
  { sub(/name: hf-secret$/, "name: llm-d-hf-token") }
  { sub(/key: token$/, "key: HF_TOKEN") }
  /^        inference_pool: llm-d-router$/ {
    print
    print "        llm-d.ai/inferenceServing: \"true\""
    next
  }
  /^    spec:$/ {
    print
    if (has_pc == "true") { print "      priorityClassName: " pc }
    if (has_ns == "true") {
      print "      nodeSelector:"
      print "        " ns_key ": " ns_value
    }
    next
  }
  { print }
' "${MT}/manifests/vllm.yaml" > "${VLLM_MANIFEST}"
if [ -n "${VLLM_NODE_SELECTOR:-}" ]; then
  require_fixed "${VLLM_MANIFEST}" "        ${NODE_SELECTOR_KEY}: ${NODE_SELECTOR_VALUE}" "failed to add the vLLM nodeSelector"
fi
require_pattern "${VLLM_MANIFEST}" "^  namespace: ${NAMESPACE}$" "failed to set the vLLM namespace"
forbid_pattern "${VLLM_MANIFEST}" "llm-d-async" "vLLM manifest still references the guide namespace"
require_pattern "${VLLM_MANIFEST}" "name: llm-d-hf-token$" "failed to point vLLM at the llm-d-hf-token secret"
require_pattern "${VLLM_MANIFEST}" "key: HF_TOKEN$" "failed to set the HF token secret key"
forbid_pattern "${VLLM_MANIFEST}" "hf-secret" "vLLM manifest still references hf-secret"
require_pattern "${VLLM_MANIFEST}" '^        llm-d.ai/inferenceServing: "true"$' "failed to add the inferenceServing label"
if [ "${HAS_PRIORITY_CLASS}" = "true" ]; then
  require_pattern "${VLLM_MANIFEST}" "^      priorityClassName: ${PRIORITY_CLASS}$" "failed to set priorityClassName"
fi
kubectl apply -n "${NAMESPACE}" -f "${VLLM_MANIFEST}"

echo "=== Applying the six lane InferenceObjectives ==="
OBJECTIVES="${OUTPUT_DIR}/inferenceobjectives.yaml"
render "${MT}/manifests/inferenceobjectives.yaml" > "${OBJECTIVES}"
forbid_pattern "${OBJECTIVES}" "NAMESPACE|POOL_NAME" "placeholders left in the InferenceObjectives"
OBJECTIVE_COUNT=$(grep -c '^kind: InferenceObjective$' "${OBJECTIVES}" || true)
[ "${OBJECTIVE_COUNT}" -eq 6 ] || die "expected 6 InferenceObjectives, rendered ${OBJECTIVE_COUNT}"
kubectl apply -f "${OBJECTIVES}"

echo "=== Deploying the router (standalone mode, Flow Control) ==="
# README step 2. CI-only (2 of 3): EPP log verbosity 4 for debuggable CI
# failures, and the guide's router ServiceMonitor only when the cluster has
# the Prometheus Operator CRD (the chart renders it unconditionally when
# router.monitoring.prometheus.enabled is true and the install fails without
# the CRD). Metrics auth is already off in the guide values, so the validator
# scrapes llm-d-router-epp:9090 directly either way.
ROUTER_ARGS=(upgrade --install "${POOL_NAME}" "${ROUTER_STANDALONE_CHART}"
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml"
  -f "${MT}/values/router/flow-control.yaml"
  --set router.epp.flags.v=4)
if kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1; then
  echo "ServiceMonitor CRD present; keeping the guide's router ServiceMonitor"
else
  echo "ServiceMonitor CRD absent; disabling router.monitoring.prometheus (CI-only)"
  ROUTER_ARGS+=(--set router.monitoring.prometheus.enabled=false)
fi
ROUTER_ARGS+=(-n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}")
printf '%s\n' "${ROUTER_ARGS[@]}" > "${OUTPUT_DIR}/helm-router.args"
helm "${ROUTER_ARGS[@]}"

echo "=== Deploying Redis ==="
# CI-only (3 of 3): keyspace notifications so the coordinator's wait mode is
# woken by Redis instead of polling (wakeup_mode: auto degrades to polling
# without them). The guide's step 4 sets them with redis-cli CONFIG SET; a
# container argument survives Redis restarts. Persistence stays off as in the
# guide.
REDIS_MANIFEST="${OUTPUT_DIR}/redis.yaml"
sed -E 's/^([[:space:]]*args: \["--save", "", "--appendonly", "no")\]$/\1, "--notify-keyspace-events", "Kl"]/' \
  "${MT}/manifests/redis.yaml" > "${REDIS_MANIFEST}"
require_fixed "${REDIS_MANIFEST}" '"--notify-keyspace-events", "Kl"' "failed to enable Redis keyspace notifications"
kubectl apply -n "${NAMESPACE}" -f "${REDIS_MANIFEST}"
kubectl rollout status deploy/redis -n "${NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}"

echo "=== Deploying llm-d-async ==="
# README steps 3 and 4: the quota-only overlay (Scenarios A+B) with the
# optional coordinator's two queues and their own worker pool, i.e.
# values/redis/quota-only-coordinator.yaml. COORD_WORKERS resizes that pool.
AP_VALUES="${OUTPUT_DIR}/llm-d-async.values.yaml"
COORD_WORKERS="${COORD_WORKERS:-16}"
render "${MT}/values/redis/quota-only-coordinator.yaml" > "${AP_VALUES}"
yq -i "(.ap.workerPools[] | select(.id == \"coord\")).workers = ${COORD_WORKERS}" "${AP_VALUES}"
forbid_pattern "${AP_VALUES}" "IGW_HOST|NAMESPACE|POOL_A|POOL_B" "placeholders left in the llm-d-async values"
QUEUE_COUNT=$(yq '.ap.transportConfig.queues | length' "${AP_VALUES}")
[ "${QUEUE_COUNT}" -eq 8 ] || die "expected 8 llm-d-async queues (6 guide + 2 coordinator), got ${QUEUE_COUNT}"
POOL_COUNT=$(yq '.ap.workerPools | length' "${AP_VALUES}")
[ "${POOL_COUNT}" -eq 3 ] || die "expected 3 llm-d-async worker pools (model-a, model-b, coord), got ${POOL_COUNT}"
COORD_QUEUE_POOLS=$(yq '[.ap.transportConfig.queues[] | select(.queue_name | test("^coord-")) | select(.worker_pool_id == "coord")] | length' "${AP_VALUES}")
[ "${COORD_QUEUE_POOLS}" -eq 2 ] || die "coordinator queues must dispatch from the coord pool"
IGW_COUNT=$(yq "[.ap.transportConfig.queues[] | select(.igw_base_url == \"http://${IGW_HOST}:80\")] | length" "${AP_VALUES}")
[ "${IGW_COUNT}" -eq 8 ] || die "expected every queue to dispatch to http://${IGW_HOST}:80, got ${IGW_COUNT}"
TTL_COUNT=$(yq '[.ap.transportConfig.queues[] | select(.queue_name | test("^coord-")) | select(.result_ttl_seconds > 0)] | length' "${AP_VALUES}")
[ "${TTL_COUNT}" -eq 2 ] || die "expected result_ttl_seconds on both coordinator queues, got ${TTL_COUNT}"
RQ_COUNT=$(yq '[.ap.transportConfig.queues[] | select(.queue_name | test("^coord-")) | select(has("result_queue_name"))] | length' "${AP_VALUES}")
[ "${RQ_COUNT}" -eq 0 ] || die "coordinator queues must not set result_queue_name"
AP_ARGS=(upgrade --install llm-d-async "${ASYNC_CHART}" -f "${AP_VALUES}"
  -n "${NAMESPACE}" --version "${ASYNC_VERSION}")
printf '%s\n' "${AP_ARGS[@]}" > "${OUTPUT_DIR}/helm-async.args"
helm "${AP_ARGS[@]}"

echo "=== Deploying the router coordinator (async-broker step) ==="
COORD_DIR="${OUTPUT_DIR}/coordinator"
mkdir -p "${COORD_DIR}"
# README step 4: the coordinator config as a ConfigMap, then the coordinator.
render "${MT}/manifests/coordinator/config.yaml" > "${COORD_DIR}/coordinator.yaml"
forbid_pattern "${COORD_DIR}/coordinator.yaml" "NAMESPACE" "placeholder left in the coordinator config"
require_fixed "${COORD_DIR}/coordinator.yaml" "redis://redis.${NAMESPACE}.svc.cluster.local:6379" "coordinator config does not point at the namespace Redis"
require_fixed "${COORD_DIR}/coordinator.yaml" "http://${IGW_HOST}:80" "coordinator config does not point at the router"
require_fixed "${COORD_DIR}/coordinator.yaml" "type: async-broker" "coordinator config lacks the async-broker step"
{
  echo "apiVersion: v1"
  echo "kind: ConfigMap"
  echo "metadata:"
  echo "  name: llm-d-coordinator-config"
  echo "data:"
  echo "  coordinator.yaml: |"
  sed -e 's/^\(.\)/    \1/' "${COORD_DIR}/coordinator.yaml"
} > "${COORD_DIR}/configmap.yaml"
sed -e "s#image: COORDINATOR_IMAGE#image: ${COORDINATOR_IMAGE}#" \
  "${MT}/manifests/coordinator/coordinator.yaml" > "${COORD_DIR}/coordinator-manifests.yaml"
require_fixed "${COORD_DIR}/coordinator-manifests.yaml" "image: ${COORDINATOR_IMAGE}" "failed to set the coordinator image"
forbid_pattern "${COORD_DIR}/coordinator-manifests.yaml" "image: COORDINATOR_IMAGE" "coordinator image placeholder left"
kubectl apply -n "${NAMESPACE}" -f "${COORD_DIR}/configmap.yaml"
kubectl apply -n "${NAMESPACE}" -f "${COORD_DIR}/coordinator-manifests.yaml"
# Not fatal: the reusable's pod wait (30m) is the authority on readiness, and
# the coordinator may legitimately trail the model load. A short wait here
# surfaces image-pull and config errors early in the log.
if ! kubectl rollout status deploy/llm-d-coordinator -n "${NAMESPACE}" --timeout="${ROLLOUT_TIMEOUT}"; then
  echo "WARNING: coordinator not ready after ${ROLLOUT_TIMEOUT}; leaving it to the pod wait" >&2
  kubectl describe deploy/llm-d-coordinator -n "${NAMESPACE}" 2>/dev/null | tail -n 20 || true
fi

echo "=== Deploy complete (rendered files in ${OUTPUT_DIR}) ==="
kubectl get pods -n "${NAMESPACE}" || true
