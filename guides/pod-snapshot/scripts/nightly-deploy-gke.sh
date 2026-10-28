#!/usr/bin/env bash
# -*- indent-tabs-mode: nil; tab-width: 2; sh-indentation: 2; -*-

# Nightly deploy for the pod-snapshot guide on GKE.
#
# Invoked as the custom_deploy_script of reusable-nightly-e2e-gke.yaml by
# .github/workflows/nightly-e2e-pod-snapshot-gke-acc-gpu-[vllm|sglang]-x.yaml.
# Runs from the repo root; the reusable workflow exports NAMESPACE and has
# already created the namespace and the llm-d-hf-token secret. The calling
# workflow sets MODEL_SERVER (vllm or sglang).
#
# Deploy only: this script brings the guide up to the state the validator
# needs and waits for it, then exits. Every deploy and wait step is emitted
# from guides/pod-snapshot/guide.yaml via `scripts/guide.py emit`, the same
# source the README is rendered from, so a guide change reaches the nightly
# without a second edit; the script only adds Deployment-level waits around
# the guide's pod waits (see below) and the CRD retry. Before deploying, it
# empties the GCS snapshot bucket (the snapshots outlive the reusable's fresh
# namespace) so Pod 1 always performs a true cold start. It finishes with Pod 1
# (cold start + snapshot) and Pod 2 (restored from the snapshot) both Ready.
#
# The threshold checks run afterwards, in the reusable's validate step:
# e2e_validate_args: '--extra-validate pod-snapshot -- --engine <vllm|sglang>'
# hands off to .github/scripts/e2e/e2e-validate-pod-snapshot.sh, which runs
# guides/pod-snapshot/scripts/check_thresholds.py.
#
# Environment knobs (all optional except NAMESPACE and MODEL_SERVER):
#   GCS_BUCKET       - Hierarchical-namespace GCS bucket for snapshots
#                      (default: llm-d-scale-pod-snapshots-us-central1)
#   OUTPUT_DIR       - Directory for emitted scripts (default: /tmp/pod-snapshot.ci)
#   CRD_RETRY_DELAY  - Seconds between CRD apply retries (default: 10)

set -euo pipefail

: "${NAMESPACE:?NAMESPACE must be exported by the calling workflow}"
: "${MODEL_SERVER:?MODEL_SERVER must be exported by the calling workflow}"

case "${MODEL_SERVER}" in
  vllm | sglang) ;;
  *)
    echo "ERROR: unsupported MODEL_SERVER: ${MODEL_SERVER} (expected vllm or sglang)" >&2
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Script-relative rather than `git rev-parse`, and passed to emit as REPO_ROOT,
# so contract tests also work from a checkout that is not a git work tree.
REPO_ROOT="${REPO_ROOT:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"

GUIDE_NAME="pod-snapshot"
GUIDE_DIR="${REPO_ROOT}/guides/${GUIDE_NAME}"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/${GUIDE_NAME}.ci}"
GCS_BUCKET="${GCS_BUCKET:-llm-d-scale-pod-snapshots-us-central1}"
CRD_RETRY_DELAY="${CRD_RETRY_DELAY:-10}"
DEPLOYMENT="pod-snapshot-nvidia-gpu-${MODEL_SERVER}-decode"

BUCKET_RE='^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$'

mkdir -p "${OUTPUT_DIR}"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ "${GCS_BUCKET}" =~ ${BUCKET_RE} ]] || die "unexpected GCS_BUCKET '${GCS_BUCKET}'"

for tool in kubectl helm gcloud python3; do
  command -v "${tool}" >/dev/null 2>&1 || die "${tool} is required"
done

# guide.py needs pyyaml; GitHub runners ship python3 but not always the module.
# PIP_BREAK_SYSTEM_PACKAGES: a PEP 668 externally-managed python3 rejects
# --user installs outright; old pips ignore the variable.
python3 -c 'import yaml' 2>/dev/null \
  || PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --user --quiet 'pyyaml==6.*'

# emit: every guide.py emit call, with the ci context (drops steps marked
# skip_in: [ci], such as git clone and llm-d-hf-token creation) and the
# nightly's --var overrides. NAMESPACE, REPO_ROOT, MODEL_SERVER and GCS_BUCKET
# are plumbing.
emit() {
  python3 "${REPO_ROOT}/scripts/guide.py" emit "${GUIDE_DIR}" \
    --context ci \
    --var REPO_ROOT="${REPO_ROOT}" \
    --var NAMESPACE="${NAMESPACE}" \
    --var MODEL_SERVER="${MODEL_SERVER}" \
    --var GCS_BUCKET="${GCS_BUCKET}" \
    "$@"
}

# Deletes everything under gs://GCS_BUCKET (never the bucket itself; -r implies
# all object versions) and asserts that the bucket then lists empty, so leftover
# objects or folders fail the run here rather than letting Pod 1 restore from
# a stale snapshot.
clear_bucket() {
  gcloud storage rm -r "gs://${GCS_BUCKET}/**" || true
  local remaining
  remaining="$(gcloud storage ls "gs://${GCS_BUCKET}")" || die "could not list gs://${GCS_BUCKET}"
  [[ -z "${remaining}" ]] || die "gs://${GCS_BUCKET} is still not empty: ${remaining//$'\n'/ }"
  echo "  [OK] gs://${GCS_BUCKET} is empty."
}

echo "=== Clearing gs://${GCS_BUCKET} so Pod 1 is a true cold start ==="
# The reusable deletes and recreates the namespace before this script runs, so
# there are no Deployments or PodSnapshot CRs to reset. Two things outlive a
# run: the GCS bucket, cleared here, and the cluster-scoped
# PodSnapshotStorageConfig (${NAMESPACE}-pod-snapshot-storage-config), which
# deploy.snapshot below re-applies unchanged; it has to survive, since the GKE
# controller needs it to purge the previous run's objects. (If Pod 1 were ever
# restored from a stale snapshot anyway, check_thresholds.py's "Pod 1 cold
# started" check fails the run.)
clear_bucket

echo "=== Installing the GAIE InferencePool CRDs ==="
# Run with -x so the log records the resolved URL (it comes from
# guides/env.sh), and retry transient GitHub/network failures (apply is
# idempotent).
CRDS_SCRIPT="${OUTPUT_DIR}/crds.sh"
emit env prerequisites.gaie > "${CRDS_SCRIPT}"
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

echo "=== Deploying the router (standalone mode) ==="
ROUTER_SCRIPT="${OUTPUT_DIR}/router.sh"
emit env deploy.router_values deploy.standalone > "${ROUTER_SCRIPT}"
bash "${ROUTER_SCRIPT}"

echo "=== Deploying the PodSnapshotStorageConfig and PodSnapshotPolicy ==="
SNAPSHOT_SCRIPT="${OUTPUT_DIR}/snapshot.sh"
emit env deploy.snapshot > "${SNAPSHOT_SCRIPT}"
bash "${SNAPSHOT_SCRIPT}"

echo "=== Deploying the ${MODEL_SERVER} model server (Pod 1 cold start) ==="
MODELSERVER_SCRIPT="${OUTPUT_DIR}/modelserver.sh"
emit env deploy.modelserver > "${MODELSERVER_SCRIPT}"
bash "${MODELSERVER_SCRIPT}"

echo "=== Waiting for Pod 1 to cold start and the PodSnapshot to be Ready ==="
# Runs verify.tests.snapshot: its second wait fails if no PodSnapshot exists
# or none reached Ready, which a Ready pod alone does not prove.
SNAPSHOT_WAIT_SCRIPT="${OUTPUT_DIR}/wait-snapshot.sh"
emit env verify.tests.snapshot > "${SNAPSHOT_WAIT_SCRIPT}"
# Wait on the Deployment before the emitted `kubectl wait pod -l`, which resolves
# its targets once and fails with "no matching resources found" if the
# ReplicaSet has not created Pod 1 yet. Not `rollout status`: it aborts with
# "exceeded its progress deadline" once the default progressDeadlineSeconds
# (600s) passes without a Ready replica, and a cold start may legitimately take
# longer (the validator allows up to 1800s end to end).
kubectl wait --for=condition=Available "deployment/${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=2400s
bash "${SNAPSHOT_WAIT_SCRIPT}"

echo "=== Scaling to 2 replicas and waiting for Pod 2 to restore from the snapshot ==="
# Scale and wait for the rollout before running the guide's own restore step.
# That step (emitted below, run under set -e) is `kubectl scale`, `kubectl wait
# pod -l`, `kubectl get events`, `kubectl logs -l`. `kubectl wait` resolves its
# targets once: right after the scale it can list only the already-Ready Pod 1
# and return at once, and the `kubectl logs -l` that follows then fails on a
# Pod 2 that exists but is still ContainerCreating, which aborts the deploy on a
# healthy cluster. With both replicas available first, the step's own scale is
# an idempotent no-op and its wait and logs run against two Running pods. (A
# scale-up of an already-complete Deployment is not subject to
# progressDeadlineSeconds, so rollout status only ends on its own timeout.)
kubectl scale "deployment/${DEPLOYMENT}" -n "${NAMESPACE}" --replicas=2
kubectl rollout status "deployment/${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=600s
RESTORE_WAIT_SCRIPT="${OUTPUT_DIR}/wait-restore.sh"
emit env verify.tests.restore > "${RESTORE_WAIT_SCRIPT}"
bash "${RESTORE_WAIT_SCRIPT}"

echo "=== Deploy complete (emitted scripts in ${OUTPUT_DIR}) ==="
kubectl get pods -n "${NAMESPACE}" || true
kubectl get podsnapshots -n "${NAMESPACE}" || true
