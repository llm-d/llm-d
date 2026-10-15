#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

# Nightly deploy for the flow-control guide on GKE GPU or AMD ROCm.
#
# Invoked as the custom_deploy_script by the GKE and AMD flow-control nightly
# workflows. Runs from the repo root; the reusable exports NAMESPACE and has
# already created the namespace and the llm-d-hf-token secret. The workflow
# selects the optimized-baseline model server overlay with ACCELERATOR_TYPE
# (gpu or amd) and INFRA_PROVIDER (gke or base).
#
# The deploy commands come from guides/flow-control/guide.yaml via
# `scripts/guide.py emit`, the same source the README is rendered from, so a
# guide fix reaches the nightly without a second edit. Every deviation
# from the guide defaults is a CI-only override marked below, and none of them
# change what the guide documents for users. Two of them (low maxConcurrency,
# single decode replica) exist to force the queue contention that the guide's
# Use Case 2 produces by hand.

set -euo pipefail

: "${NAMESPACE:?NAMESPACE must be exported by the calling workflow}"
: "${ACCELERATOR_TYPE:?ACCELERATOR_TYPE must be exported by the calling workflow}"
: "${INFRA_PROVIDER:?INFRA_PROVIDER must be exported by the calling workflow}"

case "${ACCELERATOR_TYPE}" in
  gpu | amd) ;;
  *)
    echo "ERROR: unsupported ACCELERATOR_TYPE: ${ACCELERATOR_TYPE}" >&2
    exit 1
    ;;
esac

REPO_ROOT=$(git rev-parse --show-toplevel)
# shellcheck source=guides/env.sh
source "${REPO_ROOT}/guides/env.sh"

GUIDE_NAME="flow-control"
GUIDE_DIR="${REPO_ROOT}/guides/${GUIDE_NAME}"

# guide.py needs pyyaml; GitHub runners ship python3 but not always the module.
# PIP_BREAK_SYSTEM_PACKAGES: a PEP 668 externally-managed python3 (Ubuntu
# 24.04 images, most self-hosted runners) rejects --user installs outright;
# old pips ignore the variable, so it is safe on every image.
python3 -c 'import yaml' 2>/dev/null \
  || PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --user --quiet 'pyyaml==6.*'

# CI-only (1 of 3): force a low concurrency-detector maxConcurrency so the
# saturation gate closes under the validate script's modest burst. Without it
# the backpressure assertions race. Match on the key rather than the guide's
# current value, so a future retune cannot silently no-op this, and fail loudly
# if the substitution does not take.
# Also increase EPP (--v=4) and Envoy (--log-level info) logging verbosity to
# aid debugging CI failures.
CI_VALUES="/tmp/${GUIDE_NAME}.ci.values.yaml"
awk '
  { sub(/maxConcurrency:[[:space:]]+[0-9]+/, "maxConcurrency: 4") }
  /^  epp:/ {
    print
    print "    flags:"
    print "      v: 4"
    next
  }
  { print }
' "${GUIDE_DIR}/router/${GUIDE_NAME}.values.yaml" > "${CI_VALUES}"
if ! grep -qE '^[[:space:]]*maxConcurrency:[[:space:]]+4$' "${CI_VALUES}"; then
  echo "ERROR: failed to override maxConcurrency in ${GUIDE_NAME} values" >&2
  exit 1
fi
cat << 'EOF' >> "${CI_VALUES}"
  proxy:
    args:
      - "--service-node"
      - "envoy-sidecar"
      - "--log-level"
      - "info"
      - "--concurrency"
      - "8"
      - "--drain-strategy"
      - "immediate"
      - "--drain-time-s"
      - "60"
      - "-c"
      - "/etc/envoy/envoy.yaml"
EOF

# emit: shared wrapper for every guide.py emit call — pins the ci context and
# the nightly's --var overrides. NAMESPACE and INFRA_PROVIDER are plumbing,
# ROUTER_VALUES carries CI-only (1 of 3) from above, and EXTRA_HELM_ARGS is
# CI-only (2 of 3): it disables EPP metrics token auth so the validate
# script's curl pod can scrape /metrics without a bearer token. The guide
# default (auth on) is unchanged for users, who grant scrape RBAC instead
# (verify.tests.metrics_rbac in guide.yaml, which the nightly never emits).
emit() {
  python3 "${REPO_ROOT}/scripts/guide.py" emit "${GUIDE_DIR}" \
    --context ci \
    --var NAMESPACE="${NAMESPACE}" \
    --var INFRA_PROVIDER="${INFRA_PROVIDER}" \
    --var ROUTER_VALUES="${CI_VALUES}" \
    --var EXTRA_HELM_ARGS="--set router.monitoring.prometheus.auth.enabled=false" \
    "$@"
}

echo "=== Installing CRDs (GAIE InferencePool + llm-d.ai InferenceObjective) ==="
# The apply commands come from guide.yaml, whose URLs resolve through
# guides/env.sh (GAIE_URL / ROUTER_RELEASE_URL). Run with -x so the xtrace
# records the resolved URLs the job fetched: a CRD fetch failure usually has
# no llm-d commit in the blame window (an upstream release moved), and the
# log is the only place the resolved tag survives.
CRDS_SCRIPT="/tmp/${GUIDE_NAME}.ci.crds.sh"
emit env prerequisites.crds > "${CRDS_SCRIPT}"
# Retry transient GitHub/network failures; kubectl apply is idempotent.
for attempt in 1 2 3; do
  if bash -x "${CRDS_SCRIPT}"; then
    break
  fi
  if [ "${attempt}" -eq 3 ]; then
    echo "ERROR: CRD install failed after ${attempt} attempts" >&2
    exit 1
  fi
  echo "CRD install failed (attempt ${attempt}); retrying in 10s..." >&2
  sleep 10
done

echo "=== Deploying the router (standalone mode) ==="
emit env deploy.standalone | bash

echo "=== Deploying the ${ACCELERATOR_TYPE} model server ==="
# Mirrors deploy.modelserver in guide.yaml (kustomize | sed | apply), with the
# one CI-only (3 of 3) deviation the guide's pipeline cannot express: a single
# replica instead of the selected overlay's default. Pool dispatch capacity is
# maxConcurrency x ready replicas (see the README's "Proof of Queuing"), so
# multiple replicas can leave too little queue wait for the QoS assertion to
# measure. One replica leaves 4 slots and queues the remaining requests. Patch
# before apply rather than scaling after so surplus pods never consume GPUs or
# get caught by the reusable's `kubectl wait pod --all`.
MS_MANIFEST="/tmp/${GUIDE_NAME}.ci.modelserver.yaml"
kubectl kustomize "${REPO_ROOT}/guides/optimized-baseline/modelserver/${ACCELERATOR_TYPE}/vllm/${INFRA_PROVIDER}/" \
  | sed "s/optimized-baseline/${GUIDE_NAME}/g" \
  | sed -E 's/^  replicas: [0-9]+$/  replicas: 1/' > "${MS_MANIFEST}"
if ! grep -qE '^  replicas: 1$' "${MS_MANIFEST}"; then
  echo "ERROR: failed to force a single replica in the ${ACCELERATOR_TYPE} modelserver overlay" >&2
  exit 1
fi
kubectl apply -n "${NAMESPACE}" -f "${MS_MANIFEST}"

echo "=== Applying priority-band InferenceObjectives ==="
# These define the bands the validate script targets; without them, tagged
# traffic falls back to band 0 and the per-band assertions are meaningless.
emit env deploy.objectives | bash

echo "=== Deploy complete ==="
kubectl get pods -n "${NAMESPACE}" || true
