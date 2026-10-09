#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

# Nightly deploy for the multitenant async-processing guide on GKE.
#
# Invoked as the custom_deploy_script of reusable-nightly-e2e-gke.yaml by the
# .github/workflows/nightly-e2e-async-multitenant-*gke-acc-gpu-vllm-x.yaml
# lanes. Runs from the repo root; the reusable exports NAMESPACE and has
# already created the namespace and the llm-d-hf-token secret.
#
# The deploy commands come from the guide's guide.yaml via `scripts/guide.py
# emit`, the same source the README is rendered from, so a guide fix reaches
# the nightly without a second edit. That includes the guide's optional step 4,
# the llm-d-router coordinator, which lets the validator produce llm-d-async
# traffic over HTTP. Every deviation from the guide defaults is a CI-only
# override marked below.
#
# Environment knobs (all optional): OUTPUT_DIR (emitted scripts and rendered
# files, default /tmp/async-multitenant.ci), AMT_FLOW_CONTROL (the guide's
# FLOW_CONTROL: evictable, the guide's default, or holdback; the validator
# reads the same setting from validator.env), CRD_RETRY_DELAY, SKIP_CRDS=true (cluster
# already carries the CRDs).

set -euo pipefail

: "${NAMESPACE:?NAMESPACE must be exported by the calling workflow}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Script-relative rather than `git rev-parse`: the deploy-script contract test
# runs this from a checkout that may not be a git work tree. Passed to emit as
# REPO_ROOT for the same reason.
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/../../../../.." && pwd)}"

GUIDE_DIR="${REPO_ROOT}/guides/batch-serving/asynchronous-processing/multitenant"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/async-multitenant.ci}"
INFRA_PROVIDER="gke"
FLOW_CONTROL="${AMT_FLOW_CONTROL:-evictable}"
CRD_RETRY_DELAY="${CRD_RETRY_DELAY:-10}"
PRIORITY_CLASS="nightly-gpu-critical"

mkdir -p "${OUTPUT_DIR}"

# Settings for the validator step. e2e-validate.sh forwards only -n and -m to
# e2e-validate-async-multitenant.sh, so AMT_* / LLMDBENCH_* variables given to
# this script (for example on a workflow's custom_deploy_script line) are
# recorded here for run.py to load. Credential-like names are never written.
env | grep -E '^(AMT|LLMDBENCH)_[A-Za-z0-9_]+=' | grep -Ev '^[^=]*(TOKEN|SECRET|PASSWORD|KEY)[^=]*=' \
  > "${OUTPUT_DIR}/validator.env" || true

die() { echo "ERROR: $*" >&2; exit 1; }

for tool in kubectl helm yq python3; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required"
done

# guide.py needs pyyaml; GitHub runners ship python3 but not always the module.
# PIP_BREAK_SYSTEM_PACKAGES: a PEP 668 externally-managed python3 rejects
# --user installs outright; old pips ignore the variable.
python3 -c 'import yaml' 2>/dev/null \
  || PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --user --quiet 'pyyaml==6.*'

# emit: every guide.py emit call, with the ci context (drops the steps CI
# never takes, such as the HF token secret) and the nightly's --var overrides.
# NAMESPACE, REPO_ROOT and INFRA_PROVIDER are plumbing, and FLOW_CONTROL picks
# the router values under test (guide.py rejects anything but evictable or
# holdback). CI-only (1 of 2): EXTRA_ROUTER_HELM_ARGS raises the EPP log
# verbosity to 4 for debuggable CI failures.
emit() {
  python3 "${REPO_ROOT}/scripts/guide.py" emit "${GUIDE_DIR}" \
    --context ci \
    --var REPO_ROOT="${REPO_ROOT}" \
    --var NAMESPACE="${NAMESPACE}" \
    --var INFRA_PROVIDER="${INFRA_PROVIDER}" \
    --var FLOW_CONTROL="${FLOW_CONTROL}" \
    --var EXTRA_ROUTER_HELM_ARGS="--set router.epp.flags.v=4" \
    "$@"
}

echo "=== Installing CRDs (GAIE InferencePool + llm-d.ai InferenceObjective) ==="
# Run with -x so the log records the resolved URLs (they come from
# guides/env.sh), and retry transient GitHub/network failures (apply is
# idempotent).
CRDS_SCRIPT="${OUTPUT_DIR}/crds.sh"
emit env prerequisites.crds > "${CRDS_SCRIPT}"
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
# Mirrors deploy.modelserver in guide.yaml (the GPUS=2 overlay), with the one
# CI-only (2 of 2) deviation `kubectl apply -k` cannot express: the nightly
# PriorityClass the reusable creates for allow_gpu_preemption, so the pod can
# preempt default-priority GPU pods. Set before apply rather than patched
# after: a template change would roll out a second two-GPU pod.
VLLM_MANIFEST="${OUTPUT_DIR}/vllm.yaml"
kubectl kustomize "${GUIDE_DIR}/modelserver/gpu/vllm/${INFRA_PROVIDER}" > "${VLLM_MANIFEST}"
if kubectl get priorityclass "${PRIORITY_CLASS}" >/dev/null 2>&1; then
  PC="${PRIORITY_CLASS}" yq -i '(select(.kind == "Deployment") | .spec.template.spec.priorityClassName) = strenv(PC)' "${VLLM_MANIFEST}"
  [ "$(yq 'select(.kind == "Deployment") | .spec.template.spec.priorityClassName' "${VLLM_MANIFEST}")" = "${PRIORITY_CLASS}" ] \
    || die "failed to set priorityClassName on the model server"
fi
kubectl apply -n "${NAMESPACE}" -f "${VLLM_MANIFEST}"

echo "=== Deploying the router, Redis, llm-d-async and the coordinator ==="
# README steps 2 to 4, as guide.yaml has them.
DEPLOY_SCRIPT="${OUTPUT_DIR}/deploy.sh"
emit env deploy.router deploy.async deploy.coordinator > "${DEPLOY_SCRIPT}"
bash "${DEPLOY_SCRIPT}"

echo "=== Deploy complete (emitted scripts in ${OUTPUT_DIR}) ==="
kubectl get pods -n "${NAMESPACE}" || true
