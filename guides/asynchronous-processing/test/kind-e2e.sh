#!/usr/bin/env bash
#
# kind-e2e.sh — install the asynchronous-processing guide from zero in a
# throwaway kind cluster, run the Redis smoke test, and tear everything down.
#
# Fully isolated from any existing setup:
#   * creates its own kind cluster
#   * uses a dedicated KUBECONFIG in a temp dir (your current kubecontext is
#     never read or modified)
#
# The optimized-baseline CPU modelserver defaults to a gated 3B model requesting
# 64 CPU / 64Gi, which will not fit a laptop. This script patches it down to a
# tiny ungated model (default: facebook/opt-125m) with small resources so the
# whole chain runs on a developer machine.
#
# Usage:
#   ./kind-e2e.sh                 # create cluster, test, destroy
#   KEEP=1 ./kind-e2e.sh          # leave the cluster up for inspection on exit
#   MODEL=Qwen/Qwen2.5-0.5B-Instruct ./kind-e2e.sh
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (override via environment)
# ---------------------------------------------------------------------------
CLUSTER_NAME="${CLUSTER_NAME:-async-e2e}"
NAMESPACE="${NAMESPACE:-llm-d-async}"
BASELINE_NAMESPACE="${BASELINE_NAMESPACE:-llm-d-optimized-baseline}"
BASELINE_GUIDE="${BASELINE_GUIDE:-optimized-baseline}"

MODEL="${MODEL:-facebook/opt-125m}"        # tiny, ungated, CPU-friendly
ASYNC_VERSION="${ASYNC_VERSION:-0.7.4}"     # chart + image are public under ghcr.io/llm-d

BASELINE_TIMEOUT="${BASELINE_TIMEOUT:-1800}"  # seconds to wait for vLLM ready
ASYNC_POLL_TRIES="${ASYNC_POLL_TRIES:-120}"   # smoke-test result polls
ASYNC_POLL_INTERVAL="${ASYNC_POLL_INTERVAL:-5}"

KEEP="${KEEP:-0}"

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"

# Source the shared guide environment so this harness tests the same llm-d Router
# release as everything else (GAIE_VERSION, ROUTER_STANDALONE_CHART,
# ROUTER_CHART_VERSION). guides/env.sh is the single source of truth.
# shellcheck source=/dev/null
source "${REPO_ROOT}/guides/env.sh"

# Dedicated kubeconfig so we never touch the user's in-use context.
WORKDIR="$(mktemp -d)"
export KUBECONFIG="${WORKDIR}/kubeconfig"

# Isolated, empty registry config: pull the public charts/images anonymously and
# ignore any stale ghcr credentials in the user's ~/.docker/config.json. Set
# REGISTRY_AUTH=1 to instead use the existing docker credentials (e.g. for
# private mirrors).
if [ "${REGISTRY_AUTH:-0}" != "1" ]; then
  export DOCKER_CONFIG="${WORKDIR}/docker"
  mkdir -p "${DOCKER_CONFIG}"
  echo '{}' > "${DOCKER_CONFIG}/config.json"
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; }

cleanup() {
  local rc=$?
  if [ "$KEEP" = "1" ]; then
    log "KEEP=1 set — leaving cluster '${CLUSTER_NAME}' up."
    log "Inspect with: KUBECONFIG=${KUBECONFIG} kubectl get pods -A"
    log "Destroy with: kind delete cluster --name ${CLUSTER_NAME}"
  else
    log "Tearing down kind cluster '${CLUSTER_NAME}'..."
    kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
    rm -rf "${WORKDIR}"
  fi
  exit $rc
}
trap cleanup EXIT

require() { command -v "$1" >/dev/null 2>&1 || { fail "'$1' is required but not installed."; exit 1; }; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
log "Preflight: checking tools"
require kind
require kubectl
require helm
require git
ok "kind / kubectl / helm / git present"

# ---------------------------------------------------------------------------
# 1. Throwaway cluster
# ---------------------------------------------------------------------------
log "Creating kind cluster '${CLUSTER_NAME}' (isolated kubeconfig: ${KUBECONFIG})"
kind create cluster --name "${CLUSTER_NAME}" --kubeconfig "${KUBECONFIG}" --wait 120s
ok "Cluster up"

# ---------------------------------------------------------------------------
# 2. Prerequisites (GAIE CRDs, namespaces, dummy HF token)
# ---------------------------------------------------------------------------
log "Installing Gateway API Inference Extension CRDs (${GAIE_VERSION})"
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"

kubectl create namespace "${BASELINE_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# Ungated model, but the deployment references this secret, so it must exist.
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=hf_dummy" \
  --namespace "${BASELINE_NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
ok "Prerequisites applied"

# ---------------------------------------------------------------------------
# 3. Backing inference stack (optimized-baseline, standalone, CPU)
# ---------------------------------------------------------------------------
log "Deploying optimized-baseline router (standalone)"
# The base values request 4 CPU each for the EPP and Envoy containers (8 CPU for
# the pod) — production sizing that will not schedule on a small CI runner. Shrink
# the requests for this smoke test; only requests affect scheduling.
helm install "${BASELINE_GUIDE}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/${BASELINE_GUIDE}/router/${BASELINE_GUIDE}.values.yaml" \
  --set router.epp.resources.requests.cpu=250m \
  --set router.epp.resources.requests.memory=512Mi \
  --set router.proxy.resources.requests.cpu=250m \
  --set router.proxy.resources.requests.memory=512Mi \
  -n "${BASELINE_NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

log "Deploying CPU modelserver (then patching to '${MODEL}' with small resources)"
# The optimized-baseline overlays split GPU by ${INFRA_PROVIDER} (gpu/vllm/base,
# gpu/vllm/gke); the CPU overlay has no such subfolder, so it is just cpu/vllm.
# This harness is CPU-only by design (no GPU in CI/dev kind).
kubectl apply -n "${BASELINE_NAMESPACE}" -k "${REPO_ROOT}/guides/${BASELINE_GUIDE}/modelserver/cpu/vllm"

# Shrink the modelserver to a tiny ungated model that fits a developer machine.
# args is a non-merge-key list (replaced wholesale); env merges by name so the
# existing HF_TOKEN entry is preserved.
kubectl patch deployment "${BASELINE_GUIDE}-cpu-vllm-decode" \
  -n "${BASELINE_NAMESPACE}" --type=strategic --patch "$(cat <<EOF
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: modelserver
          args:
            - ${MODEL}
            - --max_model_len=2048
            - --disable-access-log-for-endpoints=/health,/metrics,/v1/models
          env:
            - name: VLLM_CPU_KVCACHE_SPACE
              value: "4"
          resources:
            requests:
              cpu: "1"
              memory: 5Gi
            limits:
              cpu: "2"
              memory: 8Gi
EOF
)"

log "Waiting up to ${BASELINE_TIMEOUT}s for the baseline stack to become ready"
if ! kubectl wait --for=condition=available --timeout="${BASELINE_TIMEOUT}s" \
      deployment --all -n "${BASELINE_NAMESPACE}"; then
  fail "Baseline stack did not become ready"
  kubectl get pods -n "${BASELINE_NAMESPACE}"
  exit 1
fi
ok "Baseline stack ready"

# ---------------------------------------------------------------------------
# 4. Message queue (Redis)
# ---------------------------------------------------------------------------
log "Deploying Redis (no auth)"
helm repo add bitnami https://charts.bitnami.com/bitnami >/dev/null 2>&1 || true
helm repo update >/dev/null
# Standalone (single pod) keeps the CI footprint small; the guide itself uses
# the chart default (replication). The async-processor connects to the same
# redis-master service either way.
helm install redis bitnami/redis -n redis --create-namespace \
  --set auth.enabled=false --set architecture=standalone --wait
ok "Redis ready"

# ---------------------------------------------------------------------------
# 5. Async Processor
# ---------------------------------------------------------------------------
IP="$(kubectl get service "${BASELINE_GUIDE}-epp" -n "${BASELINE_NAMESPACE}" -o jsonpath='{.spec.clusterIP}')"
log "llm-d Router (EPP) endpoint: http://${IP}:80"

log "Deploying Async Processor (${ASYNC_VERSION}, redis backend)"
helm install async-processor \
  oci://ghcr.io/llm-d/charts/async-processor \
  -f "${REPO_ROOT}/guides/asynchronous-processing/redis/values.yaml" \
  --set ap.igwBaseURL="http://${IP}:80" \
  -n "${NAMESPACE}" --create-namespace --version "${ASYNC_VERSION}"

if ! kubectl wait --for=condition=available --timeout=300s \
      deployment --all -n "${NAMESPACE}"; then
  fail "Async Processor did not become ready"
  kubectl get pods -n "${NAMESPACE}"
  exit 1
fi
ok "Async Processor ready"

# ---------------------------------------------------------------------------
# 6. Smoke test: publish a request, poll for a result
# ---------------------------------------------------------------------------
REDIS_HOST="redis-master.redis.svc.cluster.local"

log "Publishing a request to the queue (model=${MODEL})"
kubectl run async-publish --rm -i --restart=Never --image=redis -- \
  redis-cli -h "${REDIS_HOST}" \
  ZADD request-sortedset 1999999999 \
  "{\"request_kind\":\"redis\",\"data\":{\"id\":\"smoketest\",\"deadline\":1999999999,\"payload\":{\"model\":\"${MODEL}\",\"prompt\":\"Hi, good morning \"}}}"

log "Polling result-list (${ASYNC_POLL_TRIES} x ${ASYNC_POLL_INTERVAL}s)"
if kubectl run async-smoketest --rm -i --restart=Never --image=redis -- \
    bash -c "for i in \$(seq 1 ${ASYNC_POLL_TRIES}); do R=\$(redis-cli -h ${REDIS_HOST} RPOP result-list); if [ -n \"\$R\" ]; then echo \"RESULT: \$R\"; exit 0; fi; sleep ${ASYNC_POLL_INTERVAL}; done; echo 'TIMEOUT: no async result'; exit 1"; then
  ok "Smoke test PASSED — async result returned"
else
  fail "Smoke test FAILED — no result on result-list"
  log "Async Processor logs (tail):"
  kubectl logs -n "${NAMESPACE}" deployment/async-processor --tail=50 2>/dev/null || true
  exit 1
fi

ok "End-to-end install from zero succeeded."
