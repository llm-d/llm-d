#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

# Nightly deploy for the agentic-api guide on GKE.
#
# Invoked as the custom_deploy_script of reusable-nightly-e2e-gke.yaml by
# .github/workflows/nightly-e2e-agentic-api-gke-acc-gpu-vllm-x.yaml. Runs from
# the repo root; the reusable exports NAMESPACE and has already created the
# namespace and the llm-d-hf-token secret.
#
# agentic-api is an extension, so this stands up a base guide first:
# optimized-baseline in Standalone Mode, whose GKE GPU overlay (Qwen3-32B)
# already starts vLLM with the tool-calling flags the guide requires. Both
# guides are deployed from their guide.yaml via `scripts/guide.py emit`, the
# same source the READMEs are rendered from, so a guide fix reaches the nightly
# without a second edit. Every deviation from the guide defaults is a CI-only
# override marked below.
#
# Environment knobs (all optional): OUTPUT_DIR (emitted scripts and rendered
# files, default /tmp/agentic-api.ci), CRD_RETRY_DELAY, MODELSERVER_TIMEOUT
# (default 30m), POSTGRES_STORAGE_CLASS (default standard-rwo).

set -euo pipefail

: "${NAMESPACE:?NAMESPACE must be exported by the calling workflow}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Script-relative rather than `git rev-parse`, and passed to emit as REPO_ROOT,
# so the deploy-script contract test also works from a checkout that is not a
# git work tree.
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"

BASE_GUIDE_NAME="optimized-baseline"
BASE_GUIDE_DIR="${REPO_ROOT}/guides/${BASE_GUIDE_NAME}"
GUIDE_DIR="${REPO_ROOT}/guides/agentic-api"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/agentic-api.ci}"
INFRA_PROVIDER="gke"
CRD_RETRY_DELAY="${CRD_RETRY_DELAY:-10}"
MODELSERVER_TIMEOUT="${MODELSERVER_TIMEOUT:-30m}"
POSTGRES_STORAGE_CLASS="${POSTGRES_STORAGE_CLASS:-standard-rwo}"
PRIORITY_CLASS="nightly-gpu-critical"

mkdir -p "${OUTPUT_DIR}"

die() { echo "ERROR: $*" >&2; exit 1; }

for tool in kubectl helm yq envsubst openssl python3; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required"
done

# guide.py needs pyyaml; GitHub runners ship python3 but not always the module.
# PIP_BREAK_SYSTEM_PACKAGES: a PEP 668 externally-managed python3 rejects
# --user installs outright; old pips ignore the variable.
python3 -c 'import yaml' 2>/dev/null \
  || PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --user --quiet 'pyyaml==6.*'

# emit_base / emit: every guide.py emit call for the base guide and for this
# guide, with the ci context (drops the steps CI never takes, such as the HF
# token secret) and the nightly's --var overrides. NAMESPACE, REPO_ROOT and
# INFRA_PROVIDER are plumbing; BASE_GUIDE_NAME is what this guide calls the
# base guide's helm release.
emit_base() {
  python3 "${REPO_ROOT}/scripts/guide.py" emit "${BASE_GUIDE_DIR}" \
    --context ci \
    --var REPO_ROOT="${REPO_ROOT}" \
    --var NAMESPACE="${NAMESPACE}" \
    --var INFRA_PROVIDER="${INFRA_PROVIDER}" \
    "$@"
}
emit() {
  python3 "${REPO_ROOT}/scripts/guide.py" emit "${GUIDE_DIR}" \
    --context ci \
    --var REPO_ROOT="${REPO_ROOT}" \
    --var NAMESPACE="${NAMESPACE}" \
    --var BASE_GUIDE_NAME="${BASE_GUIDE_NAME}" \
    "$@"
}

echo "=== Installing the GAIE InferencePool CRDs (${BASE_GUIDE_NAME} prerequisites) ==="
# Run with -x so the log records the resolved URL (it comes from
# guides/env.sh), and retry transient GitHub/network failures (apply is
# idempotent).
CRDS_SCRIPT="${OUTPUT_DIR}/crds.sh"
emit_base env prerequisites.gaie > "${CRDS_SCRIPT}"
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

echo "=== Deploying the ${BASE_GUIDE_NAME} router (standalone mode) ==="
BASE_ROUTER_SCRIPT="${OUTPUT_DIR}/base-router.sh"
emit_base env deploy.router_values deploy.standalone > "${BASE_ROUTER_SCRIPT}"
bash "${BASE_ROUTER_SCRIPT}"

echo "=== Deploying the ${BASE_GUIDE_NAME} model server ==="
# Mirrors deploy.modelserver.standard in the base guide.yaml (kubectl apply -k
# of the gpu/vllm/gke overlay), with the two CI-only deviations `apply -k`
# cannot express, both set before apply so no second rollout is triggered:
#   1. one replica instead of the overlay's two, which halves the GPU demand
#      to the workflow's declared required_gpus (one TP=2 replica). Every
#      agentic-api check sends one request at a time, so one replica is enough.
#   2. the nightly PriorityClass the reusable creates for allow_gpu_preemption,
#      so the pod can preempt default-priority GPU pods.
MS_MANIFEST="${OUTPUT_DIR}/modelserver.yaml"
kubectl kustomize "${BASE_GUIDE_DIR}/modelserver/gpu/vllm/${INFRA_PROVIDER}" > "${MS_MANIFEST}"
yq -i '(select(.kind == "Deployment") | .spec.replicas) = 1' "${MS_MANIFEST}"
[ "$(yq 'select(.kind == "Deployment") | .spec.replicas' "${MS_MANIFEST}")" = "1" ] \
  || die "failed to force a single model server replica"
if kubectl get priorityclass "${PRIORITY_CLASS}" >/dev/null 2>&1; then
  PC="${PRIORITY_CLASS}" yq -i '(select(.kind == "Deployment") | .spec.template.spec.priorityClassName) = strenv(PC)' "${MS_MANIFEST}"
  [ "$(yq 'select(.kind == "Deployment") | .spec.template.spec.priorityClassName' "${MS_MANIFEST}")" = "${PRIORITY_CLASS}" ] \
    || die "failed to set priorityClassName on the model server"
fi
kubectl apply -n "${NAMESPACE}" -f "${MS_MANIFEST}"

# The guide's prerequisite is a base guide that is "deployed and serving", and
# its pre-flight check reads the running model server pods for the
# tool-calling flags, so wait for vLLM to load the model before going on.
MS_DEPLOYMENT="$(yq 'select(.kind == "Deployment") | .metadata.name' "${MS_MANIFEST}")"
kubectl rollout status -n "${NAMESPACE}" "deployment/${MS_DEPLOYMENT}" --timeout="${MODELSERVER_TIMEOUT}"

echo "=== Creating the PostgreSQL credentials ==="
# The guide marks this step skip_in: [ci], as guides do for secrets, but it
# needs nothing from CI: it generates its own password. So emit it without the
# ci context and run the guide's own command.
SECRETS_SCRIPT="${OUTPUT_DIR}/secrets.sh"
python3 "${REPO_ROOT}/scripts/guide.py" emit "${GUIDE_DIR}" \
  --var REPO_ROOT="${REPO_ROOT}" \
  --var NAMESPACE="${NAMESPACE}" \
  --var BASE_GUIDE_NAME="${BASE_GUIDE_NAME}" \
  env prerequisites.secrets > "${SECRETS_SCRIPT}"
bash "${SECRETS_SCRIPT}"

echo "=== Confirming the base guide (README step 2) ==="
PREFLIGHT_SCRIPT="${OUTPUT_DIR}/preflight.sh"
emit env prerequisites.base_guide > "${PREFLIGHT_SCRIPT}"
bash "${PREFLIGHT_SCRIPT}"

echo "=== Deploying PostgreSQL ==="
# Mirrors deploy.postgres in guide.yaml, with the one CI-only deviation a plain
# `kubectl apply -f` cannot express: an explicit StorageClass on the PVC. The
# manifest leaves it unset, and the nightly cluster's default class is
# Filestore (standard-rwx), which provisions a 1 TiB minimum instance and takes
# minutes, past the guide's 120s rollout timeout. standard-rwo is a zonal
# persistent disk, the GKE default.
PG_MANIFEST="${OUTPUT_DIR}/postgres.yaml"
SC="${POSTGRES_STORAGE_CLASS}" yq '(select(.kind == "PersistentVolumeClaim") | .spec.storageClassName) = strenv(SC)' \
  "${GUIDE_DIR}/manifests/postgres.yaml" > "${PG_MANIFEST}"
[ "$(yq 'select(.kind == "PersistentVolumeClaim") | .spec.storageClassName' "${PG_MANIFEST}")" = "${POSTGRES_STORAGE_CLASS}" ] \
  || die "failed to set storageClassName on the PostgreSQL PVC"
kubectl apply -n "${NAMESPACE}" -f "${PG_MANIFEST}"
kubectl rollout status -n "${NAMESPACE}" deployment/agentic-api-postgres --timeout=120s

echo "=== Deploying agentic-api (Standalone Mode) ==="
# README steps 5 and 7, as guide.yaml has them. MODE and PROVIDER_NAME keep
# their defaults, standalone and none.
DEPLOY_SCRIPT="${OUTPUT_DIR}/deploy.sh"
emit env deploy.api_base deploy.standalone > "${DEPLOY_SCRIPT}"
bash "${DEPLOY_SCRIPT}"

echo "=== Deploy complete (emitted scripts in ${OUTPUT_DIR}) ==="
kubectl get pods -n "${NAMESPACE}" || true
