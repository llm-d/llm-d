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
# without a second edit; the script only adds a Deployment-level wait before
# Pod 1's `kubectl wait pod -l` (see below), the CRD retry and a pre-smoke-loop
# `pods.json` snapshot. Before deploying, it empties the GCS snapshot bucket
# (the snapshots outlive the reusable's fresh namespace) so Pod 1 always
# performs a true cold start. It finishes with Pod 1 (cold start + snapshot)
# and Pod 2 (restored from the snapshot) both Ready.
#
# The waits are blind to container failures (`kubectl wait` and `rollout
# status` only look for Ready), so each one runs under wait_or_fail_fast, which
# aborts as soon as a model server pod is in a state it will not recover from
# (image pull or container start errors, a crash loop, a Failed pod) instead of
# sitting out the 40-minute timeout. When the script exits non-zero it prints
# the pods' describe output, the PodSnapshots and the namespace events, and
# names any gVisor node that rebooted during the run; the reusable skips its
# own describe step when the deploy script fails, so this is what puts the
# failure reason in the step log.
#
# The threshold checks run afterwards, in the reusable's validate step:
# e2e_validate_args: '--extra-validate pod-snapshot -- --engine <vllm|sglang>'
# hands off to .github/scripts/e2e/e2e-validate-pod-snapshot.sh, which runs
# guides/pod-snapshot/scripts/check_thresholds.py.
#
# Environment knobs (all optional except NAMESPACE and MODEL_SERVER):
#   GCS_BUCKET       - Hierarchical-namespace GCS bucket for snapshots
#                      (default: llm-d-scale-pod-snapshots-us-central1)
#   OUTPUT_DIR       - Directory for emitted scripts and pods.json
#                      (default: /tmp/pod-snapshot.ci)
#   CRD_RETRY_DELAY  - Seconds between CRD apply retries (default: 10)
#   POD_POLL_INTERVAL - Seconds between pod status checks during the waits
#                      (default: 15)

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
POD_POLL_INTERVAL="${POD_POLL_INTERVAL:-15}"
DEPLOYMENT="pod-snapshot-nvidia-gpu-${MODEL_SERVER}-decode"
# The model server pods (Pod 1 and Pod 2); also what the emitted waits select.
POD_SELECTOR="llm-d.ai/guide=${GUIDE_NAME}"
# The model server overlays' nodeSelector.
GVISOR_NODE_SELECTOR="sandbox.gke.io/runtime=gvisor"

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

# Prints "name=bootID" for every gVisor node. A node's bootID changes when it
# reboots.
node_boot_ids() {
  kubectl get nodes -l "${GVISOR_NODE_SELECTOR}" \
    -o jsonpath='{range .items[*]}{.metadata.name}={.status.nodeInfo.bootID}{"\n"}{end}'
}

# Names every gVisor node whose bootID differs from BOOT_IDS_BEFORE. A reboot
# kills the node's pods, so the waits below time out and nothing in the pod
# logs says why.
report_rebooted_nodes() {
  local after line
  [[ -n "${BOOT_IDS_BEFORE}" ]] || return 0
  after="$(node_boot_ids 2>/dev/null)" || return 0
  while IFS= read -r line; do
    grep -Fxq -- "${line}" <<<"${after}" \
      || echo "ERROR: node ${line%%=*} rebooted or was replaced during this run, which kills its pods; check its serial console for a kernel panic." >&2
  done <<<"${BOOT_IDS_BEFORE}"
}

# EXIT trap. The reusable skips its `kubectl describe pod` step when this
# script exits non-zero, so on failure put the reason in the step log here.
on_exit() {
  local status=$?
  [[ ${status} -ne 0 ]] || return 0
  report_rebooted_nodes
  echo "=== Namespace ${NAMESPACE} at failure (exit ${status}) ==="
  kubectl get pods -n "${NAMESPACE}" -o wide || true
  kubectl describe pods -l "${POD_SELECTOR}" -n "${NAMESPACE}" || true
  kubectl get podsnapshots -n "${NAMESPACE}" -o wide || true
  kubectl get events -n "${NAMESPACE}" --sort-by=.lastTimestamp | tail -n 40 || true
}

# Prints one line per model server pod that is in a state it will not recover
# from; prints nothing when all is well or the status cannot be read.
failed_pods() {
  kubectl get pods -l "${POD_SELECTOR}" -n "${NAMESPACE}" -o json 2>/dev/null | python3 -c '
import json, sys

# Waiting reasons the kubelet never clears on its own.
FATAL = {
    "CrashLoopBackOff", "ErrImagePull", "ImagePullBackOff", "InvalidImageName",
    "CreateContainerError", "CreateContainerConfigError", "StartError", "RunContainerError",
}
try:
    pods = json.load(sys.stdin).get("items", [])
except ValueError:
    sys.exit(0)
for pod in pods:
    name = pod["metadata"]["name"]
    status = pod.get("status", {})
    if status.get("phase") == "Failed":
        print(("%s: pod Failed: %s %s" % (name, status.get("reason", ""), status.get("message", ""))).rstrip())
        continue
    for c in status.get("initContainerStatuses", []) + status.get("containerStatuses", []):
        waiting = (c.get("state") or {}).get("waiting") or {}
        last = (c.get("lastState") or {}).get("terminated") or {}
        # CrashLoopBackOff only says "back-off"; the cause is in the last exit.
        last_exit = ""
        if last:
            last_exit = "; last exit %s (%s) %s" % (last.get("exitCode"), last.get("reason", ""), last.get("message", ""))
        if waiting.get("reason") in FATAL:
            print(("%s/%s: %s: %s%s" % (name, c["name"], waiting["reason"], waiting.get("message", ""), last_exit)).rstrip())
        elif c.get("restartCount", 0) > 0:
            # A model server that comes up once stays up: a restart is a crash.
            print(("%s/%s: restarted %dx%s" % (name, c["name"], c["restartCount"], last_exit)).rstrip())
'
}

# Runs a wait ("$@") in the background and, while it runs, polls the model
# server pods; the moment one of them cannot recover, stops the wait and fails
# with that pod's state instead of letting the wait sit out its timeout. Exits
# with the wait's own status otherwise.
wait_or_fail_fast() {
  local pid failed
  "$@" &
  pid=$!
  while kill -0 "${pid}" 2>/dev/null; do
    failed="$(failed_pods)" || failed=""
    if [[ -n "${failed}" ]]; then
      pkill -TERM -P "${pid}" 2>/dev/null || true
      kill -TERM "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
      die "a model server pod will not recover, stopping the wait:"$'\n'"${failed}"
    fi
    sleep "${POD_POLL_INTERVAL}"
  done
  wait "${pid}"
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

BOOT_IDS_BEFORE="$(node_boot_ids)" || BOOT_IDS_BEFORE=""
trap on_exit EXIT

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
wait_or_fail_fast kubectl wait --for=condition=Available "deployment/${DEPLOYMENT}" -n "${NAMESPACE}" --timeout=2400s
wait_or_fail_fast bash "${SNAPSHOT_WAIT_SCRIPT}"

echo "=== Scaling to 2 replicas and waiting for Pod 2 to restore from the snapshot ==="
RESTORE_WAIT_SCRIPT="${OUTPUT_DIR}/wait-restore.sh"
emit env verify.tests.restore > "${RESTORE_WAIT_SCRIPT}"
wait_or_fail_fast bash "${RESTORE_WAIT_SCRIPT}"

echo "=== Deploy complete (emitted scripts in ${OUTPUT_DIR}) ==="
# Snapshot the pod conditions before the reusable's 60s readiness delay and
# smoke loop: Ready.lastTransitionTime is the most recent transition, so a probe
# blip under smoke-test load would otherwise move t_ready to the re-Ready time.
kubectl get pods -l "llm-d.ai/guide=${GUIDE_NAME}" -n "${NAMESPACE}" -o json > "${OUTPUT_DIR}/pods.json" || true
kubectl get pods -n "${NAMESPACE}" || true
kubectl get podsnapshots -n "${NAMESPACE}" || true
