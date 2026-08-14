#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

# Nightly deploy for the flow-control guide on GKE.
#
# Invoked as the custom_deploy_script of reusable-nightly-e2e-gke.yaml by
# .github/workflows/nightly-e2e-flow-control-gke-acc-gpu-vllm-x.yaml. Runs from
# the repo root; the reusable exports NAMESPACE and has already created the
# namespace and the llm-d-hf-token secret.
#
# This mirrors the guide README's install steps. Every deviation is marked
# "CI-only" below; there are three, and none of them change what the guide
# documents for users.

set -euo pipefail

: "${NAMESPACE:?NAMESPACE must be exported by the calling workflow}"

REPO_ROOT=$(git rev-parse --show-toplevel)
# shellcheck source=guides/env.sh
source "${REPO_ROOT}/guides/env.sh"

export GUIDE_NAME="flow-control"
INFRA_PROVIDER="gke"

echo "=== Installing CRDs (GAIE InferencePool + llm-d.ai InferenceObjective) ==="
# ROUTER_RELEASE_VERSION, not ROUTER_CHART_VERSION: the chart channel (v0)
# floats on the OCI registry and has no matching GitHub release, so the CRD
# bundle needs the release tag. Same pair the README's prerequisites use.
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"
kubectl apply -f "https://github.com/llm-d/llm-d-router/releases/download/${ROUTER_RELEASE_VERSION}/manifests.yaml"

# CI-only (1 of 3): force a low concurrency-detector maxConcurrency so the
# saturation gate closes under the validate script's modest burst. Without it
# the backpressure assertions race. Match on the key rather than the guide's
# current value, so a future retune cannot silently no-op this, and fail loudly
# if the substitution does not take.
CI_VALUES="/tmp/${GUIDE_NAME}.ci.values.yaml"
sed -E 's/(maxConcurrency:)[[:space:]]+[0-9]+/\1 4/' \
  "${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml" > "${CI_VALUES}"
if ! grep -qE '^[[:space:]]*maxConcurrency:[[:space:]]+4$' "${CI_VALUES}"; then
  echo "ERROR: failed to override maxConcurrency in ${GUIDE_NAME} values" >&2
  exit 1
fi

echo "=== Deploying the router (standalone mode) ==="
# CI-only (2 of 3): --set router.monitoring.prometheus.auth.enabled=false lets
# the validate script's curl pod scrape /metrics without a bearer token. It
# adds --metrics-endpoint-auth=false to the EPP; the guide default (auth on) is
# unchanged for users.
helm upgrade --install "${GUIDE_NAME}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${CI_VALUES}" \
  --set router.monitoring.prometheus.auth.enabled=false \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

echo "=== Deploying the model servers ==="
# Reuse optimized-baseline's model servers relabeled to flow-control, exactly as
# the guide's "Deploy the Model Server" step does — this guide deliberately
# keeps no modelserver/ overlay of its own.
kubectl kustomize "${REPO_ROOT}/guides/optimized-baseline/modelserver/gpu/vllm/${INFRA_PROVIDER}/" \
  | sed "s/optimized-baseline/${GUIDE_NAME}/g" \
  | kubectl apply -n "${NAMESPACE}" -f -

echo "=== Applying priority-band InferenceObjectives ==="
# These define the bands the validate script targets; without them, tagged
# traffic falls back to band 0 and the per-band assertions are meaningless.
kubectl apply -f "${REPO_ROOT}/guides/${GUIDE_NAME}/objectives.yaml" -n "${NAMESPACE}"

echo "=== Deploy complete ==="
kubectl get pods -n "${NAMESPACE}" || true
