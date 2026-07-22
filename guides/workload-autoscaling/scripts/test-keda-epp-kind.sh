#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
ASSET_DIR="${REPO_ROOT}/guides/workload-autoscaling/keda-epp/kind"
WORKFLOW_FILE="${REPO_ROOT}/.github/workflows/ci-keda-epp-kind.yaml"

KIND_VERSION=v0.31.0
KIND_NODE_IMAGE='kindest/node:v1.35.0@sha256:4613778f3cfcd10e615029370f5786704559103cf27bef934597ba562b269661'
GAIE_VERSION=v1.5.0
KEDA_VERSION=2.20.0
PROMETHEUS_STACK_VERSION=62.7.0
PROMETHEUS_OPERATOR_VERSION=v0.76.1
PROMETHEUS_IMAGE='quay.io/prometheus/prometheus:v2.54.1@sha256:f6639335d34a77d9d9db382b92eeb7fc00934be8eae81dbc03b31cfe90411a94'
ROUTER_VERSION=v0.9.0
EPP_IMAGE='ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.9.0@sha256:873179822ab0895a37ea09f2112ca39a6ae50a26612561c8bfad7f9a8c5af6f5'
SIM_IMAGE='ghcr.io/llm-d/llm-d-inference-sim:v0.9.0@sha256:be957b008416a645f206532aad408b5ff29dc81c628b8486479e79d0c2b3801b'
ROUTER_CHART='oci://ghcr.io/llm-d/charts/llm-d-router-standalone'
PROMETHEUS_CHART="https://github.com/prometheus-community/helm-charts/releases/download/kube-prometheus-stack-${PROMETHEUS_STACK_VERSION}/kube-prometheus-stack-${PROMETHEUS_STACK_VERSION}.tgz"
GAIE_CRDS="https://github.com/kubernetes-sigs/gateway-api-inference-extension/config/crd?ref=${GAIE_VERSION}"

NAMESPACE=llm-d-optimized-baseline
MONITORING_NAMESPACE=llm-d-monitoring
KEDA_NAMESPACE=keda
EPP_DEPLOYMENT=optimized-baseline-epp
EPP_SERVICE=optimized-baseline-epp
EPP_SERVICEMONITOR=optimized-baseline-epp-monitor
MODEL_DEPLOYMENT=optimized-baseline-nvidia-gpu-vllm-decode
SCALEDOBJECT=optimized-baseline-keda-epp
TRIGGER_AUTHENTICATION=keda-prometheus-auth
EXPECTED_HPA=keda-hpa-optimized-baseline
MODEL_ID='Qwen/Qwen3-32B'
PROMETHEUS_SERVICE=llmd-kube-prometheus-stack-prometheus

QUEUE_QUERY='sum(llm_d_epp_flow_control_queue_size{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})'
RUNNING_QUERY='sum(llm_d_epp_request_running{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})'
QUEUE_SELECTOR='llm_d_epp_flow_control_queue_size{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"}'
RUNNING_SELECTOR='llm_d_epp_request_running{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"}'

POLL_TIMEOUT_SECONDS=180
POLL_INTERVAL_SECONDS=2
STABLE_SAMPLES=3
HPA_SAMPLE_INTERVAL_SECONDS=5
KUBECTL_REQUEST_TIMEOUT=10s
HELM_TIMEOUT=8m
KEDA_HELM_TIMEOUT=15m
REQUEST_TIMEOUT_SECONDS=240

STATIC_ONLY=false
VERIFY_ARTIFACTS=false
VERIFY_ARTIFACT_PATHS=()
if [[ ${1:-} == "--static-only" && $# -eq 1 ]]; then
  STATIC_ONLY=true
elif [[ ${1:-} == "--verify-artifacts" && $# -ge 2 ]]; then
  VERIFY_ARTIFACTS=true
  shift
  VERIFY_ARTIFACT_PATHS=("$@")
elif (( $# != 0 )); then
  echo "Usage: ${0##*/} [--static-only | --verify-artifacts DIR [DIR ...]]" >&2
  exit 2
fi

if [[ ${VERIFY_ARTIFACTS} == false ]]; then
  ARTIFACT_DIR="$(mktemp -d /tmp/llmd-keda-epp-kind-artifacts.XXXXXX)"
  TEMP_DIR="$(mktemp -d /tmp/llmd-keda-epp-kind-runtime.XXXXXX)"
  KUBECONFIG="${TEMP_DIR}/kubeconfig"
  export KUBECONFIG
  CLUSTER_NAME="llmd-keda-epp-contract-${RANDOM}-$$"
  printf '%s\n' "${CLUSTER_NAME}" > "${ARTIFACT_DIR}/cluster-name.txt"
else
  ARTIFACT_DIR=""
  TEMP_DIR=""
  KUBECONFIG=""
  CLUSTER_NAME=""
fi
STAGE=initialization
STAGE_START=${SECONDS}
STAGE_RECORDED=false
RUN_START_EPOCH="$(date +%s)"
CLUSTER_CREATED=false
CLEANUP_RESULT=not-run
EPP_POD=""
EPP_POD_IP=""
PROMETHEUS_PORT=""
EPP_METRICS_PORT=""
EPP_PROXY_PORT=""
PROMETHEUS_PF_PID=""
EPP_METRICS_PF_PID=""
EPP_PROXY_PF_PID=""
RUNNING_REQUEST_PID=""
QUEUED_REQUEST_PID=""
SECOND_QUEUED_REQUEST_PID=""
BACKGROUND_CHILD_PID=""
REQUEST_EXIT_STATUS=""

TIMINGS_FILE="${ARTIFACT_DIR}/stage-timings.tsv"
POLL_FILE="${ARTIFACT_DIR}/polling-history.tsv"
SUMMARY_FILE="${ARTIFACT_DIR}/run-summary.txt"
if [[ ${VERIFY_ARTIFACTS} == false ]]; then
  printf 'stage\tresult\tduration_seconds\n' > "${TIMINGS_FILE}"
  printf 'elapsed_seconds\tstage\tcheck\tvalue\tstreak\n' > "${POLL_FILE}"
fi

log() {
  printf '[%ss] [%s] %s\n' "${SECONDS}" "${STAGE}" "$*"
}

record_poll() {
  local check_name="$1" value="$2" streak="$3"
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "${SECONDS}" "${STAGE}" "${check_name}" "${value//$'\t'/ }" "${streak}" \
    >> "${POLL_FILE}"
}

finish_stage() {
  local result="$1"
  (( BASH_SUBSHELL == 0 )) || return 0
  [[ ${STAGE_RECORDED} == false ]] \
    || fail "stage ${STAGE} already has an authoritative result"
  printf '%s\t%s\t%s\n' "${STAGE}" "${result}" "$((SECONDS - STAGE_START))" \
    >> "${TIMINGS_FILE}"
  STAGE_RECORDED=true
}

begin_stage() {
  if [[ ${STAGE} != initialization ]]; then
    finish_stage passed
  fi
  STAGE="$1"
  STAGE_START=${SECONDS}
  STAGE_RECORDED=false
  log "starting"
}

fail() {
  log "ERROR: $*" >&2
  return 1
}

validate_cluster_identity() {
  [[ $1 =~ ^llmd-keda-epp-contract-[0-9]+-[0-9]+$ ]]
}

poll_kubectl_get() {
  kubectl --request-timeout="${KUBECTL_REQUEST_TIMEOUT}" get "$@"
}

verify_governance_record() {
  local record_name="$1" record_path="$2" expected_hash="$3" evidence_file="$4"
  local actual_hash
  if [[ ! -e ${record_path} ]]; then
    printf '%s\t%s\t%s\n' "${record_name}" not-applicable "${record_path}" \
      >> "${evidence_file}"
    return 0
  fi
  if [[ ! -f ${record_path} ]]; then
    printf '%s\t%s\t%s\n' "${record_name}" failed-not-file "${record_path}" \
      >> "${evidence_file}"
    fail "governance record ${record_name} exists but is not a regular file"
    return 1
  fi
  actual_hash="$(shasum -a 256 "${record_path}" | awk '{print $1}')"
  if [[ ${actual_hash} != "${expected_hash}" ]]; then
    printf '%s\t%s\t%s\n' "${record_name}" failed-hash "${record_path}" \
      >> "${evidence_file}"
    fail "governance record ${record_name} does not match the approved local checksum"
    return 1
  fi
  printf '%s\t%s\t%s\n' "${record_name}" verified "${record_path}" \
    >> "${evidence_file}"
}

verify_artifact_trees() {
  local scan_root scan_result=0
  (( $# > 0 )) || return 2
  for scan_root in "$@"; do
    if ! python3 - "${scan_root}" <<'PY'
import base64
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
if not root.is_dir():
    raise SystemExit(2)
report_path = root / 'artifact-safety-report.txt'
violations = []
files_scanned = 0
byte_patterns = {
    'pem-certificate': re.compile(br'-----BEGIN [^-\r\n]*CERTIFICATE-----'),
    'pem-private-key': re.compile(br'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'sensitive-sentinel': re.compile(br'SENSITIVE_SENTINEL_DO_NOT_RETAIN'),
}
base64_headers = {
    base64.b64encode(b'-----BEGIN CERTIFICATE-----').rstrip(b'='),
    base64.b64encode(b'-----BEGIN PRIVATE KEY-----').rstrip(b'='),
    base64.b64encode(b'-----BEGIN RSA PRIVATE KEY-----').rstrip(b'='),
    base64.b64encode(b'-----BEGIN EC PRIVATE KEY-----').rstrip(b'='),
    base64.b64encode(b'-----BEGIN OPENSSH PRIVATE KEY-----').rstrip(b'='),
}

def inspect_secret_objects(value, relative_path):
    if isinstance(value, dict):
        if value.get('kind') == 'Secret':
            if 'data' in value:
                violations.append(('secret-data', relative_path))
            if 'stringData' in value:
                violations.append(('secret-string-data', relative_path))
            metadata = value.get('metadata')
            if isinstance(metadata, dict) and 'annotations' in metadata:
                violations.append(('secret-annotations', relative_path))
        for child in value.values():
            inspect_secret_objects(child, relative_path)
    elif isinstance(value, list):
        for child in value:
            inspect_secret_objects(child, relative_path)

for path in sorted(root.rglob('*')):
    if not path.is_file() or path == report_path:
        continue
    relative_path = str(path.relative_to(root))
    files_scanned += 1
    raw = path.read_bytes()
    for category, pattern in byte_patterns.items():
        if pattern.search(raw):
            violations.append((category, relative_path))
    for encoded_header in base64_headers:
        if encoded_header in raw:
            violations.append(('base64-pem-header', relative_path))
            break
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        continue
    parsed_values = []
    try:
        parsed_values.append(json.loads(text))
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            import yaml
            parsed_values.extend(item for item in yaml.safe_load_all(text) if item is not None)
        except (yaml.YAMLError, UnicodeDecodeError):
            pass
    for parsed in parsed_values:
        inspect_secret_objects(parsed, relative_path)

unique_violations = sorted(set(violations))
with report_path.open('w') as report:
    report.write(f'result={"failed" if unique_violations else "passed"}\n')
    report.write(f'files_scanned={files_scanned}\n')
    report.write(f'violation_count={len(unique_violations)}\n')
    for category, relative_path in unique_violations:
        report.write(f'violation={category}\tpath={relative_path}\n')
raise SystemExit(1 if unique_violations else 0)
PY
    then
      scan_result=1
    fi
  done
  return "${scan_result}"
}

capture_memory() {
  local label="$1"
  {
    printf 'snapshot=%s\n' "${label}"
    printf 'elapsed_seconds=%s\n' "${SECONDS}"
    docker info --format 'docker_mem_total_bytes={{.MemTotal}} os={{.OperatingSystem}} arch={{.Architecture}}' 2>&1 || true
    docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}' 2>&1 || true
  } > "${ARTIFACT_DIR}/memory-${label}.txt"
}

capture_resource() {
  local namespace="$1" resource="$2" filename="$3"
  kubectl get "${resource}" -n "${namespace}" -o yaml \
    > "${ARTIFACT_DIR}/${filename}" 2>&1 || true
}

sanitize_secret_metadata() {
  jq '{
    apiVersion: .apiVersion,
    kind: .kind,
    metadata: {
      name: .metadata.name,
      namespace: .metadata.namespace,
      uid: .metadata.uid
    },
    type: .type,
    keys: ([
      ((.data // {}) | keys[]),
      ((.stringData // {}) | keys[])
    ] | unique)
  }'
}

capture_secret_metadata() {
  local namespace="$1" secret_name="$2" filename="$3"
  kubectl get "secret/${secret_name}" -n "${namespace}" -o json \
    | sanitize_secret_metadata > "${ARTIFACT_DIR}/${filename}"
}

capture_keda_operator_logs() {
  local capture_reason="$1"
  local capture_artifact_dir="${2:-${ARTIFACT_DIR}}"
  local full_log="${capture_artifact_dir}/keda-operator-full.log"
  local capture_report="${capture_artifact_dir}/keda-operator-log-capture.txt"
  local scan_report="${capture_artifact_dir}/keda-operator-log-scan.txt"
  local capture_result=passed capture_failure_result capture_exit_status=0
  local x509_count unknown_authority_count scaler_error_count

  case "${capture_reason}" in
    success) capture_failure_result=failed-required ;;
    failure) capture_failure_result=failed-optional ;;
    *)
      log "ERROR: unsupported KEDA operator log capture reason: ${capture_reason}" >&2
      return 2
      ;;
  esac

  : > "${full_log}"
  if kubectl logs -n "${KEDA_NAMESPACE}" deployment/keda-operator \
      --all-containers > "${full_log}" 2>&1; then
    capture_exit_status=0
  else
    capture_exit_status=$?
    capture_result="${capture_failure_result}"
  fi

  x509_count="$(grep -Eic 'x509' "${full_log}" || true)"
  unknown_authority_count="$(grep -Eic 'unknown authority' "${full_log}" || true)"
  scaler_error_count="$(grep -Eic \
    'FailedGetExternalMetric|TriggerError|scaler([^[:alnum:]]|.*)error|error.*scaler' \
    "${full_log}" || true)"

  {
    printf 'capture_reason=%s\n' "${capture_reason}"
    printf 'capture_result=%s\n' "${capture_result}"
    printf 'capture_exit_status=%s\n' "${capture_exit_status}"
    printf 'source=deployment/keda-operator --all-containers\n'
    printf 'artifact=%s\n' "${full_log##*/}"
  } > "${capture_report}"
  {
    printf 'x509=%s\n' "${x509_count}"
    printf 'unknown_authority=%s\n' "${unknown_authority_count}"
    printf 'scaler_errors=%s\n' "${scaler_error_count}"
    printf 'matching_lines:\n'
    grep -Ein \
      'x509|unknown authority|FailedGetExternalMetric|TriggerError|scaler([^[:alnum:]]|.*)error|error.*scaler' \
      "${full_log}" || true
  } > "${scan_report}"

  if [[ ! -f ${full_log} || ! -s ${capture_report} || ! -s ${scan_report} ]]; then
    log "ERROR: KEDA operator log diagnostic artifacts were not produced" >&2
    return 1
  fi
  if [[ ${capture_result} == failed-required ]]; then
    log "ERROR: required success-path KEDA operator log capture failed with status ${capture_exit_status}" >&2
    return 1
  fi
  return 0
}

diagnostic_dump() {
  [[ ${CLUSTER_CREATED} == true ]] || return 0
  set +e
  log "capturing failure diagnostics in ${ARTIFACT_DIR}" >&2
  kubectl cluster-info dump --output-directory="${ARTIFACT_DIR}/cluster-info-dump" \
    >/dev/null 2>&1 || true
  kubectl get nodes -o wide > "${ARTIFACT_DIR}/nodes.txt" 2>&1 || true
  kubectl get pods,deployments,statefulsets,services,endpoints -A -o wide \
    > "${ARTIFACT_DIR}/workloads.txt" 2>&1 || true
  kubectl get events -A --sort-by='.lastTimestamp' \
    > "${ARTIFACT_DIR}/events.txt" 2>&1 || true
  kubectl get servicemonitors,podmonitors,prometheuses -A -o yaml \
    > "${ARTIFACT_DIR}/monitoring-resources.yaml" 2>&1 || true
  capture_resource "${NAMESPACE}" "scaledobject/${SCALEDOBJECT}" scaledobject.yaml
  capture_resource "${NAMESPACE}" "triggerauthentication/${TRIGGER_AUTHENTICATION}" triggerauthentication.yaml
  capture_resource "${NAMESPACE}" "hpa/${EXPECTED_HPA}" generated-hpa.yaml
  capture_resource "${NAMESPACE}" "deployment/${MODEL_DEPLOYMENT}" target-deployment.yaml
  kubectl get pods -n "${NAMESPACE}" \
    -l 'llm-d.ai/guide=optimized-baseline,llm-d.ai/role=decode' -o yaml \
    > "${ARTIFACT_DIR}/target-pods.yaml" 2>&1 || true
  {
    printf 'role\tpid\talive\n'
    local request_role request_pid
    for request_role in RUNNING_REQUEST_PID QUEUED_REQUEST_PID SECOND_QUEUED_REQUEST_PID; do
      request_pid="${!request_role}"
      if [[ -n ${request_pid} ]] && kill -0 "${request_pid}" >/dev/null 2>&1; then
        printf '%s\t%s\ttrue\n' "${request_role}" "${request_pid}"
        ps -p "${request_pid}" -o pid=,ppid=,etime=,command= 2>&1 || true
      else
        printf '%s\t%s\tfalse\n' "${request_role}" "${request_pid:-absent}"
      fi
    done
  } > "${ARTIFACT_DIR}/load-processes.txt" 2>&1
  capture_secret_metadata "${MONITORING_NAMESPACE}" prometheus-web-tls \
    prometheus-tls-secret-metadata.json || true
  capture_secret_metadata "${KEDA_NAMESPACE}" llmd-prometheus-ca \
    keda-global-ca-secret-metadata.json || true
  capture_secret_metadata "${NAMESPACE}" keda-prometheus-auth \
    keda-auth-secret-metadata.json || true
  kubectl logs -n "${NAMESPACE}" deployment/${EPP_DEPLOYMENT} --all-containers --tail=500 \
    > "${ARTIFACT_DIR}/epp-envoy.log" 2>&1 || true
  kubectl logs -n "${NAMESPACE}" deployment/${MODEL_DEPLOYMENT} --all-containers --tail=500 \
    > "${ARTIFACT_DIR}/simulator.log" 2>&1 || true
  if [[ -s ${ARTIFACT_DIR}/keda-operator-log-capture.txt ]] &&
     grep -q '^capture_reason=success$' \
       "${ARTIFACT_DIR}/keda-operator-log-capture.txt"; then
    : # Preserve the authoritative success-path capture result and artifacts.
  else
    capture_keda_operator_logs failure || true
  fi
  kubectl logs -n "${KEDA_NAMESPACE}" deployment/keda-operator-metrics-apiserver --all-containers --tail=500 \
    > "${ARTIFACT_DIR}/keda-metrics-server.log" 2>&1 || true
  kubectl logs -n "${MONITORING_NAMESPACE}" deployment/llmd-kube-prometheus-stack-operator \
    --all-containers --tail=500 > "${ARTIFACT_DIR}/prometheus-operator.log" 2>&1 || true
  kubectl logs -n "${MONITORING_NAMESPACE}" \
    -l app.kubernetes.io/name=prometheus --all-containers --tail=500 \
    > "${ARTIFACT_DIR}/prometheus.log" 2>&1 || true
  kind export logs "${ARTIFACT_DIR}/kind-logs" --name "${CLUSTER_NAME}" \
    >/dev/null 2>&1 || true
  capture_memory failure
  set -e
}

stop_pid() {
  local process_id="$1"
  [[ -n ${process_id} ]] || return 0
  kill "${process_id}" >/dev/null 2>&1 || true
  wait "${process_id}" >/dev/null 2>&1 || true
}

cleanup() {
  local incoming_status="$1"
  trap - EXIT INT TERM ERR
  set +e
  local cleanup_start=${SECONDS}
  local cleanup_status=0

  stop_pid "${SECOND_QUEUED_REQUEST_PID}"
  stop_pid "${QUEUED_REQUEST_PID}"
  stop_pid "${RUNNING_REQUEST_PID}"
  stop_pid "${EPP_PROXY_PF_PID}"
  stop_pid "${EPP_METRICS_PF_PID}"
  stop_pid "${PROMETHEUS_PF_PID}"

  if [[ ${CLUSTER_CREATED} == true ]]; then
    if ! validate_cluster_identity "${CLUSTER_NAME}"; then
      CLEANUP_RESULT=failed-unsafe-cluster-identity
      cleanup_status=1
    elif kind delete cluster --name "${CLUSTER_NAME}" \
        > "${ARTIFACT_DIR}/cleanup-kind-delete.log" 2>&1; then
      CLEANUP_RESULT=passed
    else
      CLEANUP_RESULT=failed
      cleanup_status=1
    fi
  else
    CLEANUP_RESULT=not-required
  fi

  if ! verify_artifact_trees "${ARTIFACT_DIR}"; then
    CLEANUP_RESULT=failed-artifact-safety
    cleanup_status=1
  fi

  if [[ -d ${TEMP_DIR} && ${TEMP_DIR} == /tmp/llmd-keda-epp-kind-runtime.* ]]; then
    rm -rf -- "${TEMP_DIR}"
  else
    CLEANUP_RESULT=failed-unsafe-temp-path
    cleanup_status=1
  fi

  printf 'cleanup\t%s\t%s\n' "${CLEANUP_RESULT}" "$((SECONDS - cleanup_start))" \
    >> "${TIMINGS_FILE}"
  {
    printf 'result=%s\n' "$([[ ${incoming_status} -eq 0 && ${cleanup_status} -eq 0 ]] && echo passed || echo failed)"
    printf 'failed_stage=%s\n' "${STAGE}"
    printf 'total_duration_seconds=%s\n' "$(( $(date +%s) - RUN_START_EPOCH ))"
    printf 'cleanup_result=%s\n' "${CLEANUP_RESULT}"
    printf 'artifact_dir=%s\n' "${ARTIFACT_DIR}"
    printf 'cluster_name=%s\n' "${CLUSTER_NAME}"
  } > "${SUMMARY_FILE}"
  printf 'Artifacts: %s\n' "${ARTIFACT_DIR}"

  if [[ ${incoming_status} -eq 0 && ${cleanup_status} -ne 0 ]]; then
    exit "${cleanup_status}"
  fi
  exit "${incoming_status}"
}

on_error() {
  local status="$1" line="$2"
  trap - ERR
  if (( BASH_SUBSHELL != 0 )); then
    exit "${status}"
  fi
  if [[ ${STAGE_RECORDED} == false ]]; then
    finish_stage failed
  fi
  log "stage failed at line ${line} with status ${status}" >&2
  diagnostic_dump
  exit "${status}"
}

on_signal() {
  local signal="$1"
  trap - INT TERM ERR
  log "received ${signal}" >&2
  finish_stage interrupted
  diagnostic_dump
  case "${signal}" in
    INT) exit 130 ;;
    TERM) exit 143 ;;
  esac
}

if [[ ${VERIFY_ARTIFACTS} == false ]]; then
  trap 'on_error "$?" "$LINENO"' ERR
  trap 'on_signal INT' INT
  trap 'on_signal TERM' TERM
  trap 'cleanup "$?"' EXIT
fi

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

start_background_child() {
  trap - ERR
  "$@" &
  BACKGROUND_CHILD_PID=$!
  trap 'on_error "$?" "$LINENO"' ERR
}

capture_child_exit_status() {
  local process_id="$1"
  REQUEST_EXIT_STATUS=0
  if wait "${process_id}"; then
    REQUEST_EXIT_STATUS=0
  else
    REQUEST_EXIT_STATUS=$?
  fi
}

classify_poll_probe() {
  local output_file="$1" probe_status
  shift
  if "$@" > "${output_file}"; then
    printf 'success\n'
    return 0
  else
    probe_status=$?
  fi
  if [[ ${probe_status} -eq 3 ]]; then
    printf 'miss\n'
  else
    printf 'error:%s\n' "${probe_status}"
  fi
}

read_probe_value() {
  local output_file="$1"
  PROBE_VALUE=""
  IFS= read -r PROBE_VALUE < "${output_file}" \
    || fail "successful polling probe produced no output"
  [[ -n ${PROBE_VALUE} ]] || fail "successful polling probe produced an empty value"
}

timing_fixture_probe() {
  case "$1" in
    miss) return 3 ;;
    success) printf '1\n' ;;
    error) return 29 ;;
    *) return 30 ;;
  esac
}

async_request_failure_fixture() {
  return 23
}

verify_poll_and_stage_accounting() {
  local real_timings_file="${TIMINGS_FILE}" real_stage="${STAGE}"
  local real_stage_start="${STAGE_START}" real_stage_recorded="${STAGE_RECORDED}"
  local fixture_file="${TEMP_DIR}/timing-fixture.tsv"
  local probe_output="${TEMP_DIR}/timing-fixture-probe.txt"
  local probe_class probe_pid

  TIMINGS_FILE="${fixture_file}"
  printf 'stage\tresult\tduration_seconds\n' > "${TIMINGS_FILE}"

  STAGE=fixture-success
  STAGE_START=${SECONDS}
  STAGE_RECORDED=false
  probe_class="$(classify_poll_probe "${probe_output}" timing_fixture_probe miss)"
  [[ ${probe_class} == miss ]] || fail "expected polling miss was not classified as a miss"
  probe_class="$(classify_poll_probe "${probe_output}" timing_fixture_probe miss)"
  [[ ${probe_class} == miss ]] || fail "second polling miss was not classified as a miss"
  probe_class="$(classify_poll_probe "${probe_output}" timing_fixture_probe success)"
  [[ ${probe_class} == success ]] || fail "successful polling probe was not classified as success"
  read_probe_value "${probe_output}"
  [[ ${PROBE_VALUE} == 1 ]] || fail "successful polling fixture returned ${PROBE_VALUE}"
  finish_stage passed
  [[ $(awk -F '\t' '$1 == "fixture-success" && $2 == "passed" { count++ } END { print count+0 }' "${TIMINGS_FILE}") -eq 1 ]] \
    || fail "successful polling fixture did not record exactly one passed row"
  [[ $(awk -F '\t' '$1 == "fixture-success" && $2 == "failed" { count++ } END { print count+0 }' "${TIMINGS_FILE}") -eq 0 ]] \
    || fail "successful polling fixture recorded a failed row"

  STAGE=fixture-failure
  STAGE_START=${SECONDS}
  STAGE_RECORDED=false
  probe_class="$(classify_poll_probe "${probe_output}" timing_fixture_probe error)"
  [[ ${probe_class} == error:29 ]] || fail "real polling error lost status 29: ${probe_class}"
  finish_stage failed
  [[ $(awk -F '\t' '$1 == "fixture-failure" && $2 == "failed" { count++ } END { print count+0 }' "${TIMINGS_FILE}") -eq 1 ]] \
    || fail "real polling error did not record exactly one failed row"
  [[ $(awk -F '\t' '$1 == "fixture-failure" && $2 == "passed" { count++ } END { print count+0 }' "${TIMINGS_FILE}") -eq 0 ]] \
    || fail "real polling error also recorded a passed row"

  start_background_child async_request_failure_fixture
  probe_pid="${BACKGROUND_CHILD_PID}"
  capture_child_exit_status "${probe_pid}"
  [[ ${REQUEST_EXIT_STATUS} -eq 23 ]] \
    || fail "asynchronous request failure lost status 23: ${REQUEST_EXIT_STATUS}"
  [[ $(awk -F '\t' 'NR > 1 { count++ } END { print count+0 }' "${TIMINGS_FILE}") -eq 2 ]] \
    || fail "timing fixture recorded a duplicate stage result"

  TIMINGS_FILE="${real_timings_file}"
  STAGE="${real_stage}"
  STAGE_START="${real_stage_start}"
  STAGE_RECORDED="${real_stage_recorded}"
  BACKGROUND_CHILD_PID=""
  REQUEST_EXIT_STATUS=""
  PROBE_VALUE=""
  cp "${fixture_file}" "${ARTIFACT_DIR}/timing-fixture.tsv"
}

verify_secret_metadata_sanitizer() {
  local fixture_input="${TEMP_DIR}/secret-metadata-sensitive-fixture.json"
  local fixture_output="${TEMP_DIR}/secret-metadata-sanitized-fixture.json"
  cat > "${fixture_input}" <<'JSON'
{
  "apiVersion": "v1",
  "kind": "Secret",
  "metadata": {
    "name": "fixture-secret",
    "namespace": "fixture-namespace",
    "uid": "fixture-uid",
    "annotations": {
      "kubectl.kubernetes.io/last-applied-configuration": "{\"data\":{\"ca.crt\":\"SENSITIVE_SENTINEL_DO_NOT_RETAIN\"}}",
      "sensitive-annotation": "SENSITIVE_SENTINEL_DO_NOT_RETAIN"
    }
  },
  "type": "kubernetes.io/tls",
  "data": {
    "ca.crt": "SENSITIVE_SENTINEL_DO_NOT_RETAIN",
    "tls.crt": "-----BEGIN CERTIFICATE-----SENSITIVE_SENTINEL_DO_NOT_RETAIN",
    "tls.key": "-----BEGIN PRIVATE KEY-----SENSITIVE_SENTINEL_DO_NOT_RETAIN"
  },
  "stringData": {
    "password": "SENSITIVE_SENTINEL_DO_NOT_RETAIN"
  }
}
JSON
  sanitize_secret_metadata < "${fixture_input}" > "${fixture_output}"
  python3 - "${fixture_output}" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as stream:
    raw = stream.read()
payload = json.loads(raw)
assert payload == {
    'apiVersion': 'v1',
    'kind': 'Secret',
    'metadata': {
        'name': 'fixture-secret',
        'namespace': 'fixture-namespace',
        'uid': 'fixture-uid',
    },
    'type': 'kubernetes.io/tls',
    'keys': ['ca.crt', 'password', 'tls.crt', 'tls.key'],
}
for forbidden in (
    'SENSITIVE_SENTINEL_DO_NOT_RETAIN',
    'BEGIN CERTIFICATE',
    'BEGIN PRIVATE KEY',
    'last-applied-configuration',
    'annotations',
    '"data"',
    '"stringData"',
):
    assert forbidden not in raw, forbidden
PY
  cp "${fixture_output}" "${ARTIFACT_DIR}/secret-metadata-sanitizer-fixture.json"
}

verify_keda_operator_log_capture() {
  local fixture_root="${TEMP_DIR}/keda-operator-log-capture-fixture"
  local retained_fixture="${ARTIFACT_DIR}/keda-operator-log-capture-fixture.txt"
  local passed_dir="${fixture_root}/passed"
  local failed_required_dir="${fixture_root}/failed-required"
  local failed_optional_dir="${fixture_root}/failed-optional"
  mkdir -p \
    "${passed_dir}" \
    "${failed_required_dir}" \
    "${failed_optional_dir}"

  (
    kubectl() {
      if [[ ${KEDA_LOG_FIXTURE_MODE} == passed ]]; then
        printf '%s\n' \
          'transport x509: certificate signed by unknown authority' \
          'FailedGetExternalMetric scaler connection error'
        return 0
      fi
      printf '%s\n' 'simulated kubectl logs failure' >&2
      return 37
    }

    KEDA_LOG_FIXTURE_MODE=passed
    capture_keda_operator_logs success "${passed_dir}"
    test -s "${passed_dir}/keda-operator-full.log"
    grep -qx 'capture_reason=success' \
      "${passed_dir}/keda-operator-log-capture.txt"
    grep -qx 'capture_result=passed' \
      "${passed_dir}/keda-operator-log-capture.txt"
    grep -qx 'capture_exit_status=0' \
      "${passed_dir}/keda-operator-log-capture.txt"
    grep -qx 'x509=1' "${passed_dir}/keda-operator-log-scan.txt"
    grep -qx 'unknown_authority=1' \
      "${passed_dir}/keda-operator-log-scan.txt"
    grep -qx 'scaler_errors=1' \
      "${passed_dir}/keda-operator-log-scan.txt"

    KEDA_LOG_FIXTURE_MODE=failed
    if capture_keda_operator_logs success "${failed_required_dir}" \
        2> "${failed_required_dir}/call.stderr"; then
      fail "success-path KEDA log capture failure was treated as optional"
    fi
    grep -qx 'capture_reason=success' \
      "${failed_required_dir}/keda-operator-log-capture.txt"
    grep -qx 'capture_result=failed-required' \
      "${failed_required_dir}/keda-operator-log-capture.txt"
    grep -qx 'capture_exit_status=37' \
      "${failed_required_dir}/keda-operator-log-capture.txt"
    test -s "${failed_required_dir}/keda-operator-full.log"
    test -s "${failed_required_dir}/keda-operator-log-scan.txt"

    capture_keda_operator_logs failure "${failed_optional_dir}"
    grep -qx 'capture_reason=failure' \
      "${failed_optional_dir}/keda-operator-log-capture.txt"
    grep -qx 'capture_result=failed-optional' \
      "${failed_optional_dir}/keda-operator-log-capture.txt"
    grep -qx 'capture_exit_status=37' \
      "${failed_optional_dir}/keda-operator-log-capture.txt"
    test -s "${failed_optional_dir}/keda-operator-full.log"
    test -s "${failed_optional_dir}/keda-operator-log-scan.txt"
  )

  {
    printf 'success_capture=passed\n'
    printf 'success_scan_x509=1\n'
    printf 'success_scan_unknown_authority=1\n'
    printf 'success_scan_scaler_errors=1\n'
    printf 'success_failure=failed-required\n'
    printf 'diagnostic_failure=failed-optional\n'
  } > "${retained_fixture}"
}

verify_helm_timeout_contract() {
  local keda_timeout_pattern non_keda_timeout_pattern
  local keda_timeout_uses non_keda_timeout_uses
  keda_timeout_pattern="--timeout \"\${KEDA_HELM_TIMEOUT}\""
  non_keda_timeout_pattern="--timeout \"\${HELM_TIMEOUT}\""

  [[ ${KEDA_HELM_TIMEOUT} == 15m ]] \
    || fail "KEDA Helm timeout must remain exactly 15m"
  [[ ${HELM_TIMEOUT} == 8m ]] \
    || fail "non-KEDA Helm timeout must remain exactly 8m"
  keda_timeout_uses="$(grep -F -c -- "${keda_timeout_pattern}" "$0")"
  non_keda_timeout_uses="$(grep -F -c -- "${non_keda_timeout_pattern}" "$0")"
  [[ ${keda_timeout_uses} -eq 1 ]] \
    || fail "expected exactly one KEDA 15m Helm timeout use, found ${keda_timeout_uses}"
  [[ ${non_keda_timeout_uses} -eq 2 ]] \
    || fail "expected exactly two non-KEDA 8m Helm timeout uses, found ${non_keda_timeout_uses}"
  printf 'keda_timeout=%s\nkeda_timeout_uses=%s\nnon_keda_timeout=%s\nnon_keda_timeout_uses=%s\n' \
    "${KEDA_HELM_TIMEOUT}" "${keda_timeout_uses}" \
    "${HELM_TIMEOUT}" "${non_keda_timeout_uses}" \
    > "${ARTIFACT_DIR}/helm-timeout-contract.txt"
}

verify_governance_behavior() {
  local fixture_dir="${TEMP_DIR}/governance-fixtures"
  local evidence_file="${fixture_dir}/governance-checks.txt"
  local wrong_record="${fixture_dir}/wrong-record.md"
  mkdir -p "${fixture_dir}"
  printf 'record\tresult\tpath\n' > "${evidence_file}"
  verify_governance_record missing "${fixture_dir}/absent.md" \
    0000000000000000000000000000000000000000000000000000000000000000 \
    "${evidence_file}"
  grep -F $'missing\tnot-applicable\t' "${evidence_file}" >/dev/null \
    || fail "missing governance fixture was not treated as not-applicable"
  printf 'wrong local governance fixture\n' > "${wrong_record}"
  if verify_governance_record wrong "${wrong_record}" \
      0000000000000000000000000000000000000000000000000000000000000000 \
      "${evidence_file}" >/dev/null 2>&1; then
    fail "wrong-content governance fixture passed checksum verification"
  fi
  grep -F $'wrong\tfailed-hash\t' "${evidence_file}" >/dev/null \
    || fail "wrong-content governance fixture did not retain failure evidence"
  cp "${evidence_file}" "${ARTIFACT_DIR}/governance-check-fixture.tsv"
}

verify_cluster_identity_artifact() {
  local retained_identity invalid_identity
  [[ -s ${ARTIFACT_DIR}/cluster-name.txt ]] \
    || fail "early cluster identity artifact is missing"
  IFS= read -r retained_identity < "${ARTIFACT_DIR}/cluster-name.txt" \
    || fail "early cluster identity artifact is unreadable"
  [[ ${retained_identity} == "${CLUSTER_NAME}" ]] \
    || fail "early cluster identity artifact does not match the generated cluster"
  validate_cluster_identity "${retained_identity}" \
    || fail "generated cluster identity failed its safety regex"
  validate_cluster_identity llmd-keda-epp-contract-123-456 \
    || fail "cluster identity validator rejected a valid fixture"
  for invalid_identity in \
      '' midas-metricops llmd-keda-epp-contract-1 \
      llmd-keda-epp-contract-a-2 llmd-keda-epp-contract-1-2-extra; do
    if validate_cluster_identity "${invalid_identity}"; then
      fail "cluster identity validator accepted an unsafe fixture"
    fi
  done
  printf 'result=passed\nidentity_source=cluster-name.txt\npattern=llmd-keda-epp-contract-digits-digits\n' \
    > "${ARTIFACT_DIR}/cluster-identity-fixture.txt"
}

verify_poll_kubectl_bounds() {
  local fixture_file="${TEMP_DIR}/poll-kubectl-fixture.txt"
  local source_report="${ARTIFACT_DIR}/poll-kubectl-source-scan.txt"
  [[ ${KUBECTL_REQUEST_TIMEOUT} == 10s ]] \
    || fail "polling kubectl request timeout must remain exactly 10s"
  (
    kubectl() {
      printf '%s\n' "$*" >> "${fixture_file}"
    }
    poll_kubectl_get pods -n fixture
    poll_kubectl_get --raw /apis/external.metrics.k8s.io/v1beta1
  )
  grep -Fx -- '--request-timeout=10s get pods -n fixture' "${fixture_file}" >/dev/null \
    || fail "polling Kubernetes resource get fixture was not request-bounded"
  grep -Fx -- '--request-timeout=10s get --raw /apis/external.metrics.k8s.io/v1beta1' \
    "${fixture_file}" >/dev/null \
    || fail "polling Kubernetes raw get fixture was not request-bounded"
  python3 - "$0" "${source_report}" <<'PY'
import pathlib, re, sys
source = pathlib.Path(sys.argv[1]).read_text()
report_path = pathlib.Path(sys.argv[2])
targets = (
    'validate_scaledobject_contract',
    'query_external_metric',
    'validate_hpa_contract',
    'wait_for_bounded_scale_up',
)
results = []
for name in targets:
    match = re.search(
        rf'(?ms)^{name}\(\) \{{\n(.*?)(?=^[a-zA-Z_][a-zA-Z0-9_]*\(\) \{{|\Z)',
        source,
    )
    assert match, name
    body = match.group(1)
    assert not re.search(r'\bkubectl\s+get\b', body), name
    count = body.count('poll_kubectl_get')
    assert count >= 1, name
    if name == 'validate_scaledobject_contract':
        assert 'Ready=True,Active=True' in body
        assert "conditions.get('Active') == 'True'" in body
    results.append((name, count))
with report_path.open('w') as report:
    report.write('result=passed\nrequest_timeout=10s\n')
    for name, count in results:
        report.write(f'function={name}\tbounded_get_uses={count}\n')
PY
}

verify_artifact_safety_fixtures() {
  local fixture_root="${TEMP_DIR}/artifact-safety-fixtures"
  local safe_root="${fixture_root}/safe"
  local unsafe_name unsafe_root unsafe_file expected_category
  mkdir -p "${safe_root}"
  printf '%s\n' \
    '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"safe","namespace":"fixture"},"type":"Opaque","keys":["ca.crt"]}' \
    > "${safe_root}/safe-secret-metadata.json"
  printf 'certificate_subject=CN=metadata-only\n' > "${safe_root}/safe-log.txt"
  verify_artifact_trees "${safe_root}" \
    || fail "artifact safety verifier rejected its safe fixture"
  grep -qx 'result=passed' "${safe_root}/artifact-safety-report.txt" \
    || fail "safe artifact fixture did not retain a passing report"

  for unsafe_name in \
      pem-certificate pem-private-key base64-pem sentinel \
      secret-data secret-string-data secret-annotations; do
    unsafe_root="${fixture_root}/${unsafe_name}"
    unsafe_file="${unsafe_root}/unsafe.txt"
    mkdir -p "${unsafe_root}"
    case "${unsafe_name}" in
      pem-certificate)
        printf '%s\n' '-----BEGIN CERTIFICATE-----' 'redacted-fixture' \
          '-----END CERTIFICATE-----' > "${unsafe_file}"
        expected_category=pem-certificate
        ;;
      pem-private-key)
        printf '%s\n' '-----BEGIN PRIVATE KEY-----' 'redacted-fixture' \
          '-----END PRIVATE KEY-----' > "${unsafe_file}"
        expected_category=pem-private-key
        ;;
      base64-pem)
        printf '%s\n' 'LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t' > "${unsafe_file}"
        expected_category=base64-pem-header
        ;;
      sentinel)
        printf '%s\n' 'SENSITIVE_SENTINEL_DO_NOT_RETAIN' > "${unsafe_file}"
        expected_category=sensitive-sentinel
        ;;
      secret-data)
        printf '%s\n' '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"unsafe"},"data":{"key":"cmVkYWN0ZWQ="}}' \
          > "${unsafe_file}"
        expected_category=secret-data
        ;;
      secret-string-data)
        printf '%s\n' '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"unsafe"},"stringData":{"key":"redacted-fixture"}}' \
          > "${unsafe_file}"
        expected_category=secret-string-data
        ;;
      secret-annotations)
        printf '%s\n' '{"apiVersion":"v1","kind":"Secret","metadata":{"name":"unsafe","annotations":{"fixture":"redacted"}}}' \
          > "${unsafe_file}"
        expected_category=secret-annotations
        ;;
    esac
    if verify_artifact_trees "${unsafe_root}"; then
      fail "artifact safety verifier accepted unsafe fixture ${unsafe_name}"
    fi
    grep -q "^violation=${expected_category}"$'\t' "${unsafe_root}/artifact-safety-report.txt" \
      || fail "unsafe artifact fixture ${unsafe_name} lacked categorized evidence"
  done
  cp "${safe_root}/artifact-safety-report.txt" \
    "${ARTIFACT_DIR}/artifact-safety-safe-fixture-report.txt"
  printf 'result=passed\nunsafe_fixtures_rejected=7\nmatched_payloads_retained=false\n' \
    > "${ARTIFACT_DIR}/artifact-safety-fixture-summary.txt"
}

ensure_keda_chart() {
  if ! helm repo list 2>/dev/null | awk '$1 == "kedacore" { found=1 } END { exit !found }'; then
    helm repo add kedacore https://kedacore.github.io/charts >/dev/null
  fi
  if ! helm show chart kedacore/keda --version "${KEDA_VERSION}" >/dev/null 2>&1; then
    helm repo update kedacore >/dev/null
  fi
  helm show chart kedacore/keda --version "${KEDA_VERSION}" \
    > "${ARTIFACT_DIR}/keda-chart.yaml"
}

static_checks() {
  local command_name actual_kind_version unsupported_array_builtin_pattern
  for command_name in bash curl docker git helm jq kind kubectl openssl python3; do
    require_command "${command_name}"
  done
  python3 -c 'import yaml' || fail "python3 PyYAML is required"

  actual_kind_version="$(kind version | awk '{print $2}')"
  [[ ${actual_kind_version} == "${KIND_VERSION}" ]] \
    || fail "kind ${KIND_VERSION} required, found ${actual_kind_version}"

  docker info > "${ARTIFACT_DIR}/docker-info.txt"
  capture_memory preflight
  git -C "${REPO_ROOT}" diff --check
  bash -n "$0"
  unsupported_array_builtin_pattern='map''file|read''array'
  if LC_ALL=C grep -En \
      "(^|[;&|])[[:space:]]*(${unsupported_array_builtin_pattern})([[:space:]]|$)" \
      "$0" > "${ARTIFACT_DIR}/bash32-unsupported-array-builtins.txt"; then
    fail "unsupported Bash array-loading builtin detected"
  fi
  verify_poll_and_stage_accounting
  verify_external_metric_validator
  verify_phase_b_replica_guard
  verify_secret_metadata_sanitizer
  verify_keda_operator_log_capture
  verify_helm_timeout_contract
  verify_governance_behavior
  verify_cluster_identity_artifact
  verify_poll_kubectl_bounds
  verify_artifact_safety_fixtures
  if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$0"
  else
    printf 'shellcheck unavailable; skipped\n' > "${ARTIFACT_DIR}/shellcheck-skipped.txt"
  fi

  printf 'record\tresult\tpath\n' > "${ARTIFACT_DIR}/governance-checks.tsv"
  verify_governance_record DECISIONS \
    "${REPO_ROOT}/.codex/work/1431/DECISIONS.md" \
    a96e4386613a45b3a3c70292c2327563e74e3cb16afdaf52665db16a943b1388 \
    "${ARTIFACT_DIR}/governance-checks.tsv"
  verify_governance_record IMPLEMENTATION_BRIEF \
    "${REPO_ROOT}/.codex/work/1431/IMPLEMENTATION_BRIEF.md" \
    2f4eff989b27167a56cc6d4b4ac636fc59f33fe445155db09cd0c0ba3e92e384 \
    "${ARTIFACT_DIR}/governance-checks.tsv"
  verify_governance_record BLOCKER_RESOLUTION \
    "${REPO_ROOT}/.codex/work/1431/BLOCKER_RESOLUTION.md" \
    9f3c3b8dd6c0ed0ffef5b720755459d2ae61235d420ba79861dfa5abc955d1e6 \
    "${ARTIFACT_DIR}/governance-checks.tsv"

  python3 - "${ASSET_DIR}" <<'PY'
import pathlib, sys, yaml
for item in sorted(pathlib.Path(sys.argv[1]).glob("*.yaml")):
    with item.open() as stream:
        list(yaml.safe_load_all(stream))
    print(f"YAML_OK {item}")
PY

  python3 - "${WORKFLOW_FILE}" <<'PY'
import pathlib, sys, yaml

class WorkflowLoader(yaml.SafeLoader):
    pass

for first_character, resolvers in list(WorkflowLoader.yaml_implicit_resolvers.items()):
    WorkflowLoader.yaml_implicit_resolvers[first_character] = [
        resolver for resolver in resolvers
        if resolver[0] != 'tag:yaml.org,2002:bool'
    ]

path = pathlib.Path(sys.argv[1])
with path.open() as stream:
    workflow = yaml.load(stream, Loader=WorkflowLoader)
triggers = workflow['on']
assert 'schedule' not in triggers
assert set(triggers) == {'pull_request', 'workflow_dispatch'}
paths = set(triggers['pull_request']['paths'])
required_paths = {
    '.github/workflows/ci-keda-epp-kind.yaml',
    'guides/env.sh',
    'guides/recipes/observability/**',
    'guides/recipes/modelserver/**',
    'guides/recipes/router/**',
    'guides/optimized-baseline/modelserver/**',
    'guides/optimized-baseline/router/**',
    'guides/workload-autoscaling/keda-epp/**',
    'guides/workload-autoscaling/scripts/test-keda-epp-kind.sh',
}
assert paths == required_paths, paths
assert workflow['permissions'] == {'contents': 'read'}
assert set(workflow['jobs']) == {'kind-contract'}
job = workflow['jobs']['kind-contract']
assert job['runs-on'] == 'ubuntu-24.04'
assert job['timeout-minutes'] == 120
assert job['env'] == {'KIND_VERSION': 'v0.31.0', 'KUBECTL_VERSION': 'v1.35.0'}
steps = job['steps']
run_step = next(step for step in steps if step.get('name') == 'Run the guide-local contract')
assert run_step['run'].strip() == 'guides/workload-autoscaling/scripts/test-keda-epp-kind.sh'
assert run_step['timeout-minutes'] == 105
assert run_step['timeout-minutes'] > 89
assert run_step['timeout-minutes'] < job['timeout-minutes']
assert job['timeout-minutes'] - run_step['timeout-minutes'] >= 15
install = next(step for step in steps if step.get('name') == 'Install and verify Kind and kubectl')['run']
for required in ('kind-linux-amd64.sha256', 'kubectl.sha256',
                 'kind version', 'kubectl version --client', 'sha256sum --check'):
    assert required in install, required
cleanup = next(step for step in steps if step.get('name') == 'Safeguard unique-cluster cleanup')
assert 'always()' in cleanup['if']
cleanup_script = cleanup['run']
for required in ('cluster-name.txt', '^llmd-keda-epp-contract-[0-9]+-[0-9]+$',
                 'kind delete cluster --name "${cluster_name}"'):
    assert required in cleanup_script, required
assert 'run-summary.txt' not in cleanup_script
assert 'kind get clusters' not in cleanup_script
safety = next(step for step in steps if step.get('id') == 'artifact-safety')
assert 'always()' in safety['if']
assert safety['run'].strip() == (
    'guides/workload-autoscaling/scripts/test-keda-epp-kind.sh '
    '--verify-artifacts /tmp/llmd-keda-epp-kind-artifacts.*')
upload = next(step for step in steps if step.get('uses') == 'actions/upload-artifact@v7')
assert 'always()' in upload['if']
assert "steps.artifact-safety.outcome == 'success'" in upload['if']
assert upload['with']['path'] == '/tmp/llmd-keda-epp-kind-artifacts.*'
assert upload['with']['if-no-files-found'] == 'error'
print('WORKFLOW_CONTRACT_OK')
PY

  kubectl kustomize "${REPO_ROOT}/guides/workload-autoscaling/keda-epp" \
    > "${ARTIFACT_DIR}/canonical-kustomize.yaml"
  kubectl kustomize --load-restrictor LoadRestrictionsNone "${ASSET_DIR}" \
    > "${ARTIFACT_DIR}/kind-kustomize.yaml"

  ensure_keda_chart
  helm show chart "${PROMETHEUS_CHART}" > "${ARTIFACT_DIR}/monitoring-chart.yaml"
  helm show chart "${ROUTER_CHART}" --version "${ROUTER_VERSION}" \
    > "${ARTIFACT_DIR}/router-chart.yaml"

  helm template optimized-baseline "${ROUTER_CHART}" \
    --version "${ROUTER_VERSION}" --namespace "${NAMESPACE}" \
    -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
    -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
    -f "${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml" \
    -f "${REPO_ROOT}/guides/workload-autoscaling/keda-epp/router.values.yaml" \
    -f "${ASSET_DIR}/router.values.yaml" \
    > "${ARTIFACT_DIR}/router-render.yaml"
  helm template keda kedacore/keda --version "${KEDA_VERSION}" \
    --namespace "${KEDA_NAMESPACE}" -f "${ASSET_DIR}/keda.values.yaml" \
    > "${ARTIFACT_DIR}/keda-render.yaml"
  helm template llmd "${PROMETHEUS_CHART}" \
    --namespace "${MONITORING_NAMESPACE}" \
    -f "${ASSET_DIR}/monitoring.values.yaml" \
    > "${ARTIFACT_DIR}/monitoring-render.yaml"

  python3 - \
    "${ARTIFACT_DIR}/canonical-kustomize.yaml" \
    "${ARTIFACT_DIR}/kind-kustomize.yaml" \
    "${ARTIFACT_DIR}/router-render.yaml" \
    "${ARTIFACT_DIR}/keda-render.yaml" \
    "${ARTIFACT_DIR}/monitoring-render.yaml" \
    "${ARTIFACT_DIR}/keda-chart.yaml" \
    "${ARTIFACT_DIR}/monitoring-chart.yaml" \
    "${ARTIFACT_DIR}/router-chart.yaml" <<'PY'
import sys, yaml

QUEUE = 'sum(llm_d_epp_flow_control_queue_size{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})'
RUNNING = 'sum(llm_d_epp_request_running{namespace="llm-d-optimized-baseline",service="optimized-baseline-epp",model_name="Qwen/Qwen3-32B"})'
PROM_IMAGE = 'quay.io/prometheus/prometheus:v2.54.1@sha256:f6639335d34a77d9d9db382b92eeb7fc00934be8eae81dbc03b31cfe90411a94'
EPP_IMAGE = 'ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.9.0@sha256:873179822ab0895a37ea09f2112ca39a6ae50a26612561c8bfad7f9a8c5af6f5'
SIM_IMAGE = 'ghcr.io/llm-d/llm-d-inference-sim:v0.9.0@sha256:be957b008416a645f206532aad408b5ff29dc81c628b8486479e79d0c2b3801b'

def docs(path):
    with open(path) as stream:
        return [item for item in yaml.safe_load_all(stream) if item]

def one(items, kind, name):
    found = [item for item in items if item.get('kind') == kind and item.get('metadata', {}).get('name') == name]
    assert len(found) == 1, (kind, name, len(found))
    return found[0]

def assert_scaledobject(items):
    ta = one(items, 'TriggerAuthentication', 'keda-prometheus-auth')
    assert ta['spec'] == {'secretTargetRef': [{'parameter': 'ca', 'name': 'keda-prometheus-auth', 'key': 'ca.crt'}]}
    so = one(items, 'ScaledObject', 'optimized-baseline-keda-epp')
    spec = so['spec']
    assert spec['scaleTargetRef'] == {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': 'optimized-baseline-nvidia-gpu-vllm-decode'}
    assert (spec['pollingInterval'], spec['cooldownPeriod'], spec['minReplicaCount'], spec['maxReplicaCount']) == (15, 300, 1, 8)
    hpa = spec['advanced']['horizontalPodAutoscalerConfig']
    assert hpa['name'] == 'keda-hpa-optimized-baseline'
    assert hpa['behavior'] == {
        'scaleUp': {'stabilizationWindowSeconds': 0, 'policies': [{'type': 'Percent', 'value': 100, 'periodSeconds': 15}]},
        'scaleDown': {'stabilizationWindowSeconds': 300, 'policies': [{'type': 'Percent', 'value': 100, 'periodSeconds': 15}]},
    }
    triggers = spec['triggers']
    assert [item['name'] for item in triggers] == ['epp-queue-size', 'epp-running-requests']
    expected = [(QUEUE, '1'), (RUNNING, '16')]
    for trigger, (query, threshold) in zip(triggers, expected):
        assert trigger['type'] == 'prometheus'
        assert trigger['metricType'] == 'AverageValue'
        assert trigger['metadata'] == {
            'serverAddress': 'https://llmd-kube-prometheus-stack-prometheus.llm-d-monitoring.svc.cluster.local:9090',
            'query': query,
            'threshold': threshold,
        }
        assert trigger['authenticationRef'] == {'name': 'keda-prometheus-auth'}
    return so

canonical = docs(sys.argv[1])
kind = docs(sys.argv[2])
router = docs(sys.argv[3])
keda = docs(sys.argv[4])
monitoring = docs(sys.argv[5])

canonical_so = assert_scaledobject(canonical)
kind_so = assert_scaledobject(kind)
assert canonical_so['spec'] == kind_so['spec']

model = one(kind, 'Deployment', 'optimized-baseline-nvidia-gpu-vllm-decode')
assert model['spec']['replicas'] == 1
container = model['spec']['template']['spec']['containers'][0]
assert container['image'] == SIM_IMAGE
assert container['resources'] == {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '250m', 'memory': '128Mi'}}
assert not any('gpu' in key.lower() for section in container['resources'].values() for key in section)
assert '--time-to-first-token' in container['args'] and '180s' in container['args']

epp = one(router, 'Deployment', 'optimized-baseline-epp')
containers = epp['spec']['template']['spec']['containers']
epp_container = next(item for item in containers if item['name'] == 'epp')
proxy_container = next(item for item in containers if item['name'] == 'envoy-proxy')
assert epp_container['image'] == EPP_IMAGE
assert epp_container['resources'] == {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '384Mi'}}
assert proxy_container['resources'] == {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '192Mi'}}
config = one(router, 'ConfigMap', 'optimized-baseline-epp')['data']['optimized-baseline-keda-epp-plugins.yaml']
parsed_config = yaml.safe_load(config)
detector = next(item for item in parsed_config['plugins'] if item['type'] == 'concurrency-detector')
assert detector['parameters'] == {'maxConcurrency': 1, 'concurrencyMode': 'requests', 'headroom': 0.0}
service = one(router, 'Service', 'optimized-baseline-epp')
assert len([port for port in service['spec']['ports'] if port['port'] == 9090 and port['name'] == 'http-metrics']) == 1
service_monitor = one(router, 'ServiceMonitor', 'optimized-baseline-epp-monitor')
assert service_monitor['spec']['endpoints'] == [{'interval': '10s', 'path': '/metrics', 'port': 'http-metrics'}]
explicit_pod_relabel = any(rule.get('targetLabel') == 'pod' for endpoint in service_monitor['spec']['endpoints'] for rule in endpoint.get('relabelings', []))
assert not explicit_pod_relabel

keda_deployments = [item for item in keda if item.get('kind') == 'Deployment']
assert sorted(item['metadata']['name'] for item in keda_deployments) == ['keda-operator', 'keda-operator-metrics-apiserver']
for deployment in keda_deployments:
    resources = deployment['spec']['template']['spec']['containers'][0]['resources']
    assert resources == {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}}
keda_operator = one(keda, 'Deployment', 'keda-operator')
keda_operator_spec = keda_operator['spec']['template']['spec']
keda_operator_container = keda_operator_spec['containers'][0]
assert keda_operator_container['image'] == 'ghcr.io/kedacore/keda:2.20.0'
assert keda_operator_container['args'].count('--ca-dir=/custom/ca') == 1
custom_ca_mounts = [item for item in keda_operator_container['volumeMounts'] if item['name'] == 'llmd-prometheus-ca']
assert custom_ca_mounts == [{'name': 'llmd-prometheus-ca', 'mountPath': '/custom/ca', 'readOnly': True}]
custom_ca_volumes = [item for item in keda_operator_spec['volumes'] if item['name'] == 'llmd-prometheus-ca']
assert custom_ca_volumes == [{'name': 'llmd-prometheus-ca', 'secret': {
    'secretName': 'llmd-prometheus-ca',
    'items': [{'key': 'ca.crt', 'path': 'ca.crt'}],
}}]
keda_metrics_server = one(keda, 'Deployment', 'keda-operator-metrics-apiserver')
assert keda_metrics_server['spec']['template']['spec']['containers'][0]['image'] == 'ghcr.io/kedacore/keda-metrics-apiserver:2.20.0'
assert not [item for item in keda_metrics_server['spec']['template']['spec'].get('volumes', []) if item['name'] == 'llmd-prometheus-ca']
assert not [item for item in keda_metrics_server['spec']['template']['spec']['containers'][0].get('volumeMounts', []) if item['name'] == 'llmd-prometheus-ca']

assert not [item for item in monitoring if item.get('kind') in ('Alertmanager', 'DaemonSet')]
assert not [item for item in monitoring if 'grafana' in item.get('metadata', {}).get('name', '')]
prom = one(monitoring, 'Prometheus', 'llmd-kube-prometheus-stack-prometheus')
assert prom['spec']['image'] == PROM_IMAGE
assert prom['spec']['web']['tlsConfig'] == {'cert': {'secret': {'name': 'prometheus-web-tls', 'key': 'tls.crt'}}, 'keySecret': {'name': 'prometheus-web-tls', 'key': 'tls.key'}}
assert prom['spec']['serviceMonitorSelector'] == {}
assert prom['spec']['serviceMonitorNamespaceSelector'] == {}
assert prom['spec']['resources'] == {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '500m', 'memory': '768Mi'}}
operator = one(monitoring, 'Deployment', 'llmd-kube-prometheus-stack-operator')
assert operator['spec']['template']['spec']['containers'][0]['resources'] == {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}}
assert not operator['spec']['template']['spec'].get('volumes')
assert all(probe['scheme'] == 'HTTP' for probe in (
    operator['spec']['template']['spec']['containers'][0]['readinessProbe']['httpGet'],
    operator['spec']['template']['spec']['containers'][0]['livenessProbe']['httpGet']))

with open(sys.argv[6]) as stream: keda_chart = yaml.safe_load(stream)
with open(sys.argv[7]) as stream: monitoring_chart = yaml.safe_load(stream)
with open(sys.argv[8]) as stream: router_chart = yaml.safe_load(stream)
assert keda_chart['version'] == '2.20.0' and keda_chart['appVersion'] == '2.20.0'
assert monitoring_chart['version'] == '62.7.0' and monitoring_chart['appVersion'] == 'v0.76.1'
assert router_chart['version'] == 'v0.9.0' and router_chart['appVersion'] == 'v0.9.0'
print('STATIC_CONTRACT_OK')
PY
}

create_cluster() {
  log "creating isolated cluster ${CLUSTER_NAME}"
  kind create cluster --name "${CLUSTER_NAME}" --image "${KIND_NODE_IMAGE}" \
    --config "${ASSET_DIR}/kind.yaml" --kubeconfig "${KUBECONFIG}" \
    > "${ARTIFACT_DIR}/kind-create.log" 2>&1
  CLUSTER_CREATED=true
  kubectl cluster-info > "${ARTIFACT_DIR}/cluster-info.txt"
  kubectl get nodes -o wide > "${ARTIFACT_DIR}/nodes-after-create.txt"
  capture_memory cluster-created
}

install_stack() {
  kubectl create namespace "${MONITORING_NAMESPACE}"
  kubectl create namespace "${KEDA_NAMESPACE}" --dry-run=client -o yaml \
    | kubectl apply -f -
  kubectl create namespace "${NAMESPACE}"

  log "installing GAIE CRDs ${GAIE_VERSION}"
  kubectl apply -k "${GAIE_CRDS}" > "${ARTIFACT_DIR}/gaie-crds-apply.log"

  log "installing monitoring CRDs ${PROMETHEUS_STACK_VERSION}"
  helm show crds "${PROMETHEUS_CHART}" \
    | kubectl apply --server-side --validate=false -f - \
      > "${ARTIFACT_DIR}/monitoring-crds-apply.log"

  log "generating the local Prometheus CA and server certificate"
  "${REPO_ROOT}/guides/recipes/observability/generate-prometheus-tls-certs.sh" \
    --namespace "${MONITORING_NAMESPACE}" \
    --cert-dir "${TEMP_DIR}/prometheus-certs" \
    --validity 7 \
    --context "${KUBECONFIG}" \
    > "${ARTIFACT_DIR}/tls-generation.log"
  openssl verify -CAfile "${TEMP_DIR}/prometheus-certs/ca.crt" \
    "${TEMP_DIR}/prometheus-certs/tls.crt" \
    > "${ARTIFACT_DIR}/tls-verification.txt"
  openssl x509 -in "${TEMP_DIR}/prometheus-certs/tls.crt" -noout \
    -subject -issuer -dates -ext subjectAltName \
    > "${ARTIFACT_DIR}/tls-certificate-metadata.txt"

  log "installing low-resource TLS Prometheus stack ${PROMETHEUS_STACK_VERSION}"
  helm upgrade --install llmd "${PROMETHEUS_CHART}" \
    --namespace "${MONITORING_NAMESPACE}" --skip-crds \
    -f "${ASSET_DIR}/monitoring.values.yaml" \
    --wait --timeout "${HELM_TIMEOUT}" \
    > "${ARTIFACT_DIR}/monitoring-install.log"

  log "copying only the generated CA into the Kind-only KEDA global CA Secret"
  kubectl create secret generic llmd-prometheus-ca \
    --namespace "${KEDA_NAMESPACE}" \
    --from-file=ca.crt="${TEMP_DIR}/prometheus-certs/ca.crt" \
    --dry-run=client -o yaml \
    | kubectl apply -f - > "${ARTIFACT_DIR}/keda-global-ca-secret-apply.log"
  kubectl get secret/llmd-prometheus-ca -n "${KEDA_NAMESPACE}" -o json \
    | jq -e '.type == "Opaque" and (.data | keys) == ["ca.crt"]' >/dev/null \
    || fail "Kind-only KEDA global CA Secret must contain only ca.crt"

  log "installing KEDA ${KEDA_VERSION}"
  helm upgrade --install keda kedacore/keda --version "${KEDA_VERSION}" \
    --namespace "${KEDA_NAMESPACE}" \
    -f "${ASSET_DIR}/keda.values.yaml" \
    --wait --timeout "${KEDA_HELM_TIMEOUT}" \
    > "${ARTIFACT_DIR}/keda-install.log"

  log "installing router ${ROUTER_VERSION} with canonical values layering"
  helm upgrade --install optimized-baseline "${ROUTER_CHART}" \
    --version "${ROUTER_VERSION}" --namespace "${NAMESPACE}" \
    -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
    -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
    -f "${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml" \
    -f "${REPO_ROOT}/guides/workload-autoscaling/keda-epp/router.values.yaml" \
    -f "${ASSET_DIR}/router.values.yaml" \
    --wait --timeout "${HELM_TIMEOUT}" \
    > "${ARTIFACT_DIR}/router-install.log"

  log "copying only the generated CA into the canonical KEDA auth Secret"
  kubectl create secret generic keda-prometheus-auth \
    --namespace "${NAMESPACE}" \
    --from-file=ca.crt="${TEMP_DIR}/prometheus-certs/ca.crt" \
    --dry-run=client -o yaml \
    | kubectl apply -f - > "${ARTIFACT_DIR}/keda-auth-secret-apply.log"

  log "applying the canonical ScaledObject and Kind simulator overlay"
  kubectl apply -f "${ARTIFACT_DIR}/kind-kustomize.yaml" \
    > "${ARTIFACT_DIR}/kind-overlay-apply.log"

  capture_memory stack-installed
}

wait_for_workloads() {
  kubectl rollout status -n "${MONITORING_NAMESPACE}" \
    deployment/llmd-kube-prometheus-stack-operator --timeout=300s
  kubectl wait -n "${MONITORING_NAMESPACE}" \
    prometheus/llmd-kube-prometheus-stack-prometheus \
    --for=condition=Available --timeout=300s
  kubectl rollout status -n "${KEDA_NAMESPACE}" deployment/keda-operator \
    --timeout=300s
  kubectl rollout status -n "${KEDA_NAMESPACE}" \
    deployment/keda-operator-metrics-apiserver --timeout=300s
  kubectl rollout status -n "${NAMESPACE}" deployment/${MODEL_DEPLOYMENT} \
    --timeout=300s
  kubectl rollout status -n "${NAMESPACE}" deployment/${EPP_DEPLOYMENT} \
    --timeout=300s

  kubectl get deployment/${MODEL_DEPLOYMENT} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/simulator-deployment.json"
  jq -e --arg image "${SIM_IMAGE}" '
    .spec.replicas == 1 and
    .status.readyReplicas == 1 and
    .spec.template.spec.containers[0].image == $image and
    (.spec.template.spec.containers[0].resources.requests | has("nvidia.com/gpu") | not) and
    (.spec.template.spec.containers[0].resources.limits | has("nvidia.com/gpu") | not)
  ' "${ARTIFACT_DIR}/simulator-deployment.json" >/dev/null \
    || fail "simulator Deployment is not one ready CPU-only pinned replica"

  kubectl get deployment/${EPP_DEPLOYMENT} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/epp-deployment.json"
  jq -e --arg image "${EPP_IMAGE}" '
    .spec.replicas == 1 and
    .status.readyReplicas == 1 and
    any(.spec.template.spec.containers[]; .name == "epp" and .image == $image)
  ' "${ARTIFACT_DIR}/epp-deployment.json" >/dev/null \
    || fail "EPP Deployment is not one ready pinned replica"

  kubectl get servicemonitor/${EPP_SERVICEMONITOR} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/epp-servicemonitor.json"
  kubectl get deployment/llmd-kube-prometheus-stack-operator \
    -n "${MONITORING_NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/prometheus-operator-deployment.json"
  jq -e --arg image "quay.io/prometheus-operator/prometheus-operator:${PROMETHEUS_OPERATOR_VERSION}" '
    any(.spec.template.spec.containers[]; .image == $image)
  ' "${ARTIFACT_DIR}/prometheus-operator-deployment.json" >/dev/null \
    || fail "Prometheus Operator is not running the pinned image"
  kubectl get prometheus/${PROMETHEUS_SERVICE} -n "${MONITORING_NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/prometheus-custom-resource.json"
  jq -e --arg image "${PROMETHEUS_IMAGE}" '.spec.image == $image' \
    "${ARTIFACT_DIR}/prometheus-custom-resource.json" >/dev/null \
    || fail "Prometheus custom resource is not pinned to the approved image"

  local pod_count
  pod_count="$(kubectl get pods -n "${NAMESPACE}" \
    -l "llm-d-router-gateway=${EPP_DEPLOYMENT}" -o json \
    | jq '[.items[] | select(.metadata.deletionTimestamp == null and .status.phase == "Running")] | length')"
  [[ ${pod_count} == 1 ]] || fail "expected exactly one running EPP pod, found ${pod_count}"
  EPP_POD="$(kubectl get pods -n "${NAMESPACE}" \
    -l "llm-d-router-gateway=${EPP_DEPLOYMENT}" -o jsonpath='{.items[0].metadata.name}')"
  EPP_POD_IP="$(kubectl get "pod/${EPP_POD}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.podIP}')"
  [[ -n ${EPP_POD} && -n ${EPP_POD_IP} ]] || fail "could not identify EPP pod and IP"
  printf 'pod=%s\npod_ip=%s\n' "${EPP_POD}" "${EPP_POD_IP}" \
    > "${ARTIFACT_DIR}/selected-epp-endpoint.txt"
  capture_memory workloads-ready
}

wait_for_forward_port() {
  local logfile="$1" process_id="$2" remote_port="$3"
  local deadline=$((SECONDS + 30)) selected_port=""
  while (( SECONDS < deadline )); do
    selected_port="$(sed -nE "s/^Forwarding from 127\\.0\\.0\\.1:([0-9]+) -> ${remote_port}$/\\1/p" \
      "${logfile}" | head -1)"
    if [[ -n ${selected_port} ]]; then
      printf '%s\n' "${selected_port}"
      return 0
    fi
    kill -0 "${process_id}" >/dev/null 2>&1 \
      || fail "port-forward exited before exposing remote port ${remote_port}: $(tr '\n' ' ' < "${logfile}")"
    sleep 1
  done
  fail "port-forward did not expose remote port ${remote_port}"
}

start_port_forwards() {
  kubectl port-forward -n "${MONITORING_NAMESPACE}" \
    service/${PROMETHEUS_SERVICE} :9090 \
    > "${ARTIFACT_DIR}/prometheus-port-forward.log" 2>&1 &
  PROMETHEUS_PF_PID=$!
  PROMETHEUS_PORT="$(wait_for_forward_port \
    "${ARTIFACT_DIR}/prometheus-port-forward.log" "${PROMETHEUS_PF_PID}" 9090)"

  kubectl port-forward -n "${NAMESPACE}" service/${EPP_SERVICE} :9090 \
    > "${ARTIFACT_DIR}/epp-metrics-port-forward.log" 2>&1 &
  EPP_METRICS_PF_PID=$!
  EPP_METRICS_PORT="$(wait_for_forward_port \
    "${ARTIFACT_DIR}/epp-metrics-port-forward.log" "${EPP_METRICS_PF_PID}" 9090)"

  kubectl port-forward -n "${NAMESPACE}" service/${EPP_SERVICE} :80 \
    > "${ARTIFACT_DIR}/epp-proxy-port-forward.log" 2>&1 &
  EPP_PROXY_PF_PID=$!
  EPP_PROXY_PORT="$(wait_for_forward_port \
    "${ARTIFACT_DIR}/epp-proxy-port-forward.log" "${EPP_PROXY_PF_PID}" 8081)"

  printf 'prometheus=https://localhost:%s\nepp_metrics=http://localhost:%s\nepp_proxy=http://localhost:%s\n' \
    "${PROMETHEUS_PORT}" "${EPP_METRICS_PORT}" "${EPP_PROXY_PORT}" \
    > "${ARTIFACT_DIR}/port-forwards.txt"
}

prometheus_query() {
  local query="$1" output_file="$2"
  curl --fail --silent --show-error --max-time 20 \
    --cacert "${TEMP_DIR}/prometheus-certs/ca.crt" \
    --get "https://localhost:${PROMETHEUS_PORT}/api/v1/query" \
    --data-urlencode "query=${query}" > "${output_file}"
}

wait_for_prometheus_target() {
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0 target_file
  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    target_file="${ARTIFACT_DIR}/prometheus-targets-${attempt}.json"
    if curl --fail --silent --show-error --max-time 20 \
        --cacert "${TEMP_DIR}/prometheus-certs/ca.crt" \
        "https://localhost:${PROMETHEUS_PORT}/api/v1/targets?state=active" \
        > "${target_file}" && \
      jq -e --arg namespace "${NAMESPACE}" --arg service "${EPP_SERVICE}" \
        --arg pod "${EPP_POD}" --arg ip "${EPP_POD_IP}" '
          .status == "success" and
          any(.data.activeTargets[]?;
            .health == "up" and
            .labels.namespace == $namespace and
            .labels.service == $service and
            .discoveredLabels.__meta_kubernetes_pod_name == $pod and
            (.scrapeUrl | contains($ip + ":9090/metrics")))
        ' "${target_file}" >/dev/null; then
      streak=$((streak + 1))
      record_poll prometheus-epp-target "up:${EPP_POD}:${EPP_POD_IP}" "${streak}"
      if (( streak >= STABLE_SAMPLES )); then
        cp "${target_file}" "${ARTIFACT_DIR}/prometheus-targets-final.json"
        return 0
      fi
    else
      streak=0
      record_poll prometheus-epp-target not-up "${streak}"
    fi
    sleep "${POLL_INTERVAL_SECONDS}"
  done
  fail "Prometheus did not stably report the selected EPP pod target UP"
}

direct_metric_value() {
  local metrics_file="$1" metric_name="$2"
  python3 - "${metrics_file}" "${metric_name}" "${MODEL_ID}" <<'PY'
import math, re, sys
path, metric_name, model = sys.argv[1:]
line_re = re.compile(r'^' + re.escape(metric_name) + r'\{([^}]*)\}\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)$')
label_re = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"')
required = {'model_name', 'target_model_name', 'fairness_id', 'priority'}
if metric_name == 'llm_d_epp_flow_control_queue_size':
    required.add('inference_pool')
values = []
with open(path) as stream:
    for raw in stream:
        match = line_re.match(raw.strip())
        if not match:
            continue
        labels = dict(label_re.findall(match.group(1)))
        if labels.get('model_name') != model or labels.get('target_model_name') != model:
            continue
        if not required.issubset(labels):
            raise SystemExit(f'{metric_name} missing intrinsic labels: {sorted(required - labels.keys())}')
        if 'inference_pool' in required and not labels['inference_pool']:
            raise SystemExit(f'{metric_name} has empty inference_pool')
        value = float(match.group(2))
        if not math.isfinite(value):
            raise SystemExit(f'{metric_name} is not finite')
        values.append(value)
if not values:
    raise SystemExit(3)
print(format(sum(values), '.15g'))
PY
}

numeric_is_one() {
  numeric_equals "$1" 1
}

numeric_equals() {
  python3 - "$1" "$2" <<'PY'
import math, sys
value = float(sys.argv[1])
expected = float(sys.argv[2])
raise SystemExit(0 if math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-9) else 1)
PY
}

numeric_at_most() {
  python3 - "$1" "$2" <<'PY'
import math, sys
value = float(sys.argv[1])
maximum = float(sys.argv[2])
raise SystemExit(0 if math.isfinite(value) and value <= maximum else 1)
PY
}

numeric_in_range() {
  python3 - "$1" "$2" "$3" <<'PY'
import math, sys
value = float(sys.argv[1])
minimum = float(sys.argv[2])
maximum = float(sys.argv[3])
raise SystemExit(0 if math.isfinite(value) and minimum <= value <= maximum else 1)
PY
}

fetch_direct_metrics() {
  local output_file="$1"
  curl --fail --silent --show-error --max-time 10 \
    "http://localhost:${EPP_METRICS_PORT}/metrics" > "${output_file}"
}

start_request() {
  local output_file="$1" error_file="$2"
  start_background_child \
    curl --fail --silent --show-error --max-time "${REQUEST_TIMEOUT_SECONDS}" \
    -H 'Content-Type: application/json' \
    --data-binary @"${TEMP_DIR}/request.json" \
    "http://localhost:${EPP_PROXY_PORT}/v1/chat/completions" \
    > "${output_file}" 2> "${error_file}"
}

wait_for_direct_running() {
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0
  local metrics value probe_class probe_output
  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    metrics="${ARTIFACT_DIR}/direct-running-${attempt}.prom"
    probe_output="${metrics}.value"
    value=unavailable
    if fetch_direct_metrics "${metrics}"; then
      probe_class="$(classify_poll_probe "${probe_output}" \
        direct_metric_value "${metrics}" llm_d_epp_request_running)"
      case "${probe_class}" in
        success)
          read_probe_value "${probe_output}"
          value="${PROBE_VALUE}"
          if numeric_is_one "${value}"; then
            streak=$((streak + 1))
          else
            streak=0
          fi
          ;;
        miss) streak=0 ;;
        error:*) fail "unexpected direct running metric probe status ${probe_class#error:}" ;;
        *) fail "unknown direct running metric probe classification: ${probe_class}" ;;
      esac
    else
      streak=0
    fi
    record_poll direct-running "${value}" "${streak}"
    if (( streak >= STABLE_SAMPLES )); then
      cp "${metrics}" "${ARTIFACT_DIR}/direct-running-final.prom"
      return 0
    fi
    if ! kill -0 "${RUNNING_REQUEST_PID}" >/dev/null 2>&1; then
      capture_child_exit_status "${RUNNING_REQUEST_PID}"
      RUNNING_REQUEST_PID=""
      fail "long-running request exited with status ${REQUEST_EXIT_STATUS} before running metric became stable: $(tr '\n' ' ' < "${ARTIFACT_DIR}/running-request.stderr")"
    fi
    sleep "${POLL_INTERVAL_SECONDS}"
  done
  fail "direct running-request metric did not stably equal 1"
}

wait_for_direct_pair() {
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0
  local metrics running queue running_class queue_class running_output queue_output
  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    metrics="${ARTIFACT_DIR}/direct-pair-${attempt}.prom"
    running_output="${metrics}.running-value"
    queue_output="${metrics}.queue-value"
    running=unavailable
    queue=unavailable
    if fetch_direct_metrics "${metrics}"; then
      running_class="$(classify_poll_probe "${running_output}" \
        direct_metric_value "${metrics}" llm_d_epp_request_running)"
      queue_class="$(classify_poll_probe "${queue_output}" \
        direct_metric_value "${metrics}" llm_d_epp_flow_control_queue_size)"
      case "${running_class}:${queue_class}" in
        success:success)
          read_probe_value "${running_output}"
          running="${PROBE_VALUE}"
          read_probe_value "${queue_output}"
          queue="${PROBE_VALUE}"
          if numeric_is_one "${running}" && numeric_is_one "${queue}"; then
            streak=$((streak + 1))
          else
            streak=0
          fi
          ;;
        error:*|*:error:*) fail "unexpected direct metric probe classification running=${running_class},queue=${queue_class}" ;;
        miss:*|*:miss) streak=0 ;;
        *) fail "unknown direct metric probe classification running=${running_class},queue=${queue_class}" ;;
      esac
    else
      streak=0
    fi
    record_poll direct-running-and-queue "running=${running},queue=${queue}" "${streak}"
    if (( streak >= STABLE_SAMPLES )); then
      cp "${metrics}" "${ARTIFACT_DIR}/direct-pair-final.prom"
      grep -E '^llm_d_epp_(request_running|flow_control_queue_size)\{' "${metrics}" \
        > "${ARTIFACT_DIR}/direct-pair-samples.txt"
      return 0
    fi
    if ! kill -0 "${RUNNING_REQUEST_PID}" >/dev/null 2>&1; then
      capture_child_exit_status "${RUNNING_REQUEST_PID}"
      RUNNING_REQUEST_PID=""
      fail "running request exited with status ${REQUEST_EXIT_STATUS} before queue contract became stable"
    fi
    if ! kill -0 "${QUEUED_REQUEST_PID}" >/dev/null 2>&1; then
      capture_child_exit_status "${QUEUED_REQUEST_PID}"
      QUEUED_REQUEST_PID=""
      fail "second request exited with status ${REQUEST_EXIT_STATUS} instead of remaining queued"
    fi
    sleep "${POLL_INTERVAL_SECONDS}"
  done
  fail "direct running and queue metrics did not stably equal 1 together"
}

require_all_stimulus_alive() {
  local context="$1" request_role request_pid
  for request_role in RUNNING_REQUEST_PID QUEUED_REQUEST_PID SECOND_QUEUED_REQUEST_PID; do
    request_pid="${!request_role}"
    [[ -n ${request_pid} ]] || continue
    if ! kill -0 "${request_pid}" >/dev/null 2>&1; then
      capture_child_exit_status "${request_pid}"
      printf -v "${request_role}" '%s' ""
      fail "${request_role} exited with status ${REQUEST_EXIT_STATUS} ${context}"
    fi
  done
}

wait_for_phase_b_direct_pair() {
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0
  local metrics running queue running_class queue_class running_output queue_output
  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    metrics="${ARTIFACT_DIR}/phase-b-direct-pair-${attempt}.prom"
    running_output="${metrics}.running-value"
    queue_output="${metrics}.queue-value"
    running=unavailable
    queue=unavailable
    if fetch_direct_metrics "${metrics}"; then
      running_class="$(classify_poll_probe "${running_output}" \
        direct_metric_value "${metrics}" llm_d_epp_request_running)"
      queue_class="$(classify_poll_probe "${queue_output}" \
        direct_metric_value "${metrics}" llm_d_epp_flow_control_queue_size)"
      case "${running_class}:${queue_class}" in
        success:success)
          read_probe_value "${running_output}"
          running="${PROBE_VALUE}"
          read_probe_value "${queue_output}"
          queue="${PROBE_VALUE}"
          if numeric_equals "${running}" 1 && numeric_equals "${queue}" 2; then
            streak=$((streak + 1))
          else
            streak=0
          fi
          ;;
        error:*|*:error:*) fail "unexpected Phase B direct metric probe classification running=${running_class},queue=${queue_class}" ;;
        miss:*|*:miss) streak=0 ;;
        *) fail "unknown Phase B direct metric probe classification running=${running_class},queue=${queue_class}" ;;
      esac
    else
      streak=0
    fi
    record_poll phase-b-direct-running-and-queue \
      "running=${running},queue=${queue}" "${streak}"
    if (( streak >= STABLE_SAMPLES )); then
      cp "${metrics}" "${ARTIFACT_DIR}/phase-b-direct-pair-final.prom"
      grep -E '^llm_d_epp_(request_running|flow_control_queue_size)\{' "${metrics}" \
        > "${ARTIFACT_DIR}/phase-b-direct-pair-samples.txt"
      return 0
    fi
    require_all_stimulus_alive "before Phase B direct metric evidence became stable"
    sleep "${POLL_INTERVAL_SECONDS}"
  done
  fail "Phase B direct running and queue metrics did not stably equal 1 and 2"
}

validate_prometheus_series() {
  local result_file="$1" metric_name="$2"
  python3 - "${result_file}" "${metric_name}" "${NAMESPACE}" \
    "${EPP_SERVICE}" "${MODEL_ID}" <<'PY'
import json, math, sys

path, metric_name, namespace, service, model = sys.argv[1:]
with open(path) as stream:
    payload = json.load(stream)
assert payload.get('status') == 'success', payload
data = payload.get('data', {})
assert data.get('resultType') == 'vector', data
results = data.get('result', [])
assert results, f'{metric_name}: Prometheus returned an empty vector'
required = {'namespace', 'service', 'model_name', 'target_model_name',
            'fairness_id', 'priority'}
if metric_name == 'llm_d_epp_flow_control_queue_size':
    required.add('inference_pool')
for result in results:
    labels = result.get('metric', {})
    assert labels.get('__name__') == metric_name, labels
    assert labels.get('namespace') == namespace, labels
    assert labels.get('service') == service, labels
    assert labels.get('model_name') == model, labels
    assert labels.get('target_model_name') == model, labels
    missing = required - labels.keys()
    assert not missing, f'{metric_name}: missing live labels {sorted(missing)}'
    if 'inference_pool' in required:
        assert labels['inference_pool'], f'{metric_name}: empty inference_pool'
    value = float(result['value'][1])
    assert math.isfinite(value), f'{metric_name}: non-finite sample'
PY
}

prometheus_aggregate_value() {
  local result_file="$1"
  python3 - "${result_file}" <<'PY'
import json, math, sys
with open(sys.argv[1]) as stream:
    payload = json.load(stream)
assert payload.get('status') == 'success', payload
data = payload.get('data', {})
assert data.get('resultType') == 'vector', data
results = data.get('result', [])
if not results:
    raise SystemExit(3)
assert len(results) == 1, f'expected exactly one non-empty aggregate result, got {len(results)}'
value = float(results[0]['value'][1])
assert math.isfinite(value), 'aggregate result is not finite'
print(format(value, '.15g'))
PY
}

wait_for_prometheus_pair() {
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0
  local running_file queue_file running queue raw_running raw_queue
  local running_output queue_output running_class queue_class
  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    running_file="${ARTIFACT_DIR}/prometheus-running-${attempt}.json"
    queue_file="${ARTIFACT_DIR}/prometheus-queue-${attempt}.json"
    running_output="${running_file}.value"
    queue_output="${queue_file}.value"
    running=unavailable
    queue=unavailable
    if prometheus_query "${RUNNING_QUERY}" "${running_file}" && \
       prometheus_query "${QUEUE_QUERY}" "${queue_file}"; then
      running_class="$(classify_poll_probe "${running_output}" \
        prometheus_aggregate_value "${running_file}")"
      queue_class="$(classify_poll_probe "${queue_output}" \
        prometheus_aggregate_value "${queue_file}")"
      case "${running_class}:${queue_class}" in
        success:success)
          read_probe_value "${running_output}"
          running="${PROBE_VALUE}"
          read_probe_value "${queue_output}"
          queue="${PROBE_VALUE}"
          if numeric_is_one "${running}" && numeric_is_one "${queue}"; then
            streak=$((streak + 1))
          else
            streak=0
          fi
          ;;
        error:*|*:error:*) fail "unexpected Prometheus aggregate probe classification running=${running_class},queue=${queue_class}" ;;
        miss:*|*:miss) streak=0 ;;
        *) fail "unknown Prometheus aggregate probe classification running=${running_class},queue=${queue_class}" ;;
      esac
    else
      streak=0
    fi
    record_poll prometheus-checked-in-queries \
      "running=${running},queue=${queue}" "${streak}"
    if (( streak >= STABLE_SAMPLES )); then
      raw_running="${ARTIFACT_DIR}/prometheus-running-series-final.json"
      raw_queue="${ARTIFACT_DIR}/prometheus-queue-series-final.json"
      prometheus_query "${RUNNING_SELECTOR}" "${raw_running}"
      prometheus_query "${QUEUE_SELECTOR}" "${raw_queue}"
      validate_prometheus_series "${raw_running}" llm_d_epp_request_running
      validate_prometheus_series "${raw_queue}" llm_d_epp_flow_control_queue_size
      cp "${running_file}" "${ARTIFACT_DIR}/prometheus-running-final.json"
      cp "${queue_file}" "${ARTIFACT_DIR}/prometheus-queue-final.json"
      return 0
    fi
    if ! kill -0 "${RUNNING_REQUEST_PID}" >/dev/null 2>&1; then
      capture_child_exit_status "${RUNNING_REQUEST_PID}"
      RUNNING_REQUEST_PID=""
      fail "running request exited with status ${REQUEST_EXIT_STATUS} before Prometheus contract became stable"
    fi
    if ! kill -0 "${QUEUED_REQUEST_PID}" >/dev/null 2>&1; then
      capture_child_exit_status "${QUEUED_REQUEST_PID}"
      QUEUED_REQUEST_PID=""
      fail "queued request exited with status ${REQUEST_EXIT_STATUS} before Prometheus contract became stable"
    fi
    sleep "${POLL_INTERVAL_SECONDS}"
  done
  fail "checked-in Prometheus queries did not each return one stable numeric result equal to 1"
}

validate_scaledobject_contract() {
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0
  local condition_value condition_file
  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    condition_file="${ARTIFACT_DIR}/scaledobject-conditions-${attempt}.json"
    condition_value=unavailable
    if poll_kubectl_get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" -o json \
        > "${condition_file}" 2>/dev/null; then
      condition_value="$(python3 - "${condition_file}" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    scaledobject = json.load(stream)
conditions = {
    item.get('type'): item.get('status')
    for item in scaledobject.get('status', {}).get('conditions', [])
}
print(f'Ready={conditions.get("Ready", "absent")},Active={conditions.get("Active", "absent")}')
PY
)"
    fi
    if [[ ${condition_value} == 'Ready=True,Active=True' ]]; then
      streak=$((streak + 1))
    else
      streak=0
    fi
    record_poll scaledobject-ready-active "${condition_value}" "${streak}"
    (( streak >= STABLE_SAMPLES )) && break
    sleep "${POLL_INTERVAL_SECONDS}"
  done
  (( streak >= STABLE_SAMPLES )) \
    || fail "ScaledObject did not become stably Ready=True and Active=True"

  poll_kubectl_get triggerauthentication/${TRIGGER_AUTHENTICATION} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/triggerauthentication-final.json"
  poll_kubectl_get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/scaledobject-final.json"
  capture_secret_metadata "${MONITORING_NAMESPACE}" prometheus-web-tls \
    prometheus-tls-secret-metadata-final.json
  capture_secret_metadata "${KEDA_NAMESPACE}" llmd-prometheus-ca \
    keda-global-ca-secret-metadata-final.json
  capture_secret_metadata "${NAMESPACE}" keda-prometheus-auth \
    keda-auth-secret-metadata-final.json
  poll_kubectl_get secret/keda-prometheus-auth -n "${NAMESPACE}" \
    -o jsonpath='{.data.ca\.crt}' | grep -q . \
    || fail "KEDA authentication Secret is missing non-empty ca.crt"

  python3 - "${ARTIFACT_DIR}/triggerauthentication-final.json" \
    "${ARTIFACT_DIR}/scaledobject-final.json" "${QUEUE_QUERY}" "${RUNNING_QUERY}" <<'PY'
import json, sys
ta_path, so_path, queue_query, running_query = sys.argv[1:]
with open(ta_path) as stream:
    ta = json.load(stream)
with open(so_path) as stream:
    so = json.load(stream)
assert ta['metadata']['name'] == 'keda-prometheus-auth'
assert ta['spec'] == {'secretTargetRef': [
    {'parameter': 'ca', 'name': 'keda-prometheus-auth', 'key': 'ca.crt'}]}
spec = so['spec']
assert spec['scaleTargetRef'] == {
    'apiVersion': 'apps/v1', 'kind': 'Deployment',
    'name': 'optimized-baseline-nvidia-gpu-vllm-decode'}
assert spec['pollingInterval'] == 15
assert spec['cooldownPeriod'] == 300
assert spec['minReplicaCount'] == 1
assert spec['maxReplicaCount'] == 8
assert spec['advanced']['horizontalPodAutoscalerConfig'] == {
    'name': 'keda-hpa-optimized-baseline',
    'behavior': {
        'scaleUp': {'stabilizationWindowSeconds': 0,
                    'policies': [{'type': 'Percent', 'value': 100, 'periodSeconds': 15}]},
        'scaleDown': {'stabilizationWindowSeconds': 300,
                      'policies': [{'type': 'Percent', 'value': 100, 'periodSeconds': 15}]}}}
expected = {
    'epp-queue-size': ('1', queue_query),
    'epp-running-requests': ('16', running_query),
}
assert len(spec['triggers']) == 2
for trigger in spec['triggers']:
    assert trigger['type'] == 'prometheus'
    assert trigger['metricType'] == 'AverageValue'
    assert trigger['authenticationRef'] == {'name': 'keda-prometheus-auth'}
    threshold, query = expected.pop(trigger['name'])
    assert trigger['metadata'] == {
        'serverAddress': 'https://llmd-kube-prometheus-stack-prometheus.llm-d-monitoring.svc.cluster.local:9090',
        'query': query, 'threshold': threshold}
assert not expected
conditions = {item['type']: item['status'] for item in so.get('status', {}).get('conditions', [])}
assert conditions.get('Ready') == 'True', conditions
assert conditions.get('Active') == 'True', conditions
assert conditions.get('Fallback', 'False') == 'False', conditions
PY
}

quantity_is_numeric() {
  python3 - "$1" <<'PY'
import math, re, sys
value = sys.argv[1]
match = re.fullmatch(r'([-+]?(?:\d+(?:\.\d*)?|\.\d+))(m?)', value)
assert match, f'unsupported or non-numeric Kubernetes quantity: {value}'
number = float(match.group(1)) / (1000.0 if match.group(2) else 1.0)
assert math.isfinite(number)
PY
}

validate_external_metric_request_path() {
  local request_path="$1" metric_name="$2"
  local expected_path="/apis/external.metrics.k8s.io/v1beta1/namespaces/${NAMESPACE}/${metric_name}?labelSelector=scaledobject.keda.sh%2Fname%3D${SCALEDOBJECT}"
  case "${metric_name}" in
    ''|*[!a-zA-Z0-9_.-]*) fail "unsafe external metric name in request path: ${metric_name}" ;;
  esac
  [[ ${request_path} == "${expected_path}" ]] \
    || fail "external metric request path lost its namespace or encoded ScaledObject selector"
}

query_external_metric() {
  local metric_name="$1" output_file="$2"
  local request_path="/apis/external.metrics.k8s.io/v1beta1/namespaces/${NAMESPACE}/${metric_name}?labelSelector=scaledobject.keda.sh%2Fname%3D${SCALEDOBJECT}"
  validate_external_metric_request_path "${request_path}" "${metric_name}"
  poll_kubectl_get --raw "${request_path}" > "${output_file}"
}

validate_external_metric_response() {
  local response_file="$1" metric_name="$2"
  python3 - "${response_file}" "${metric_name}" "${SCALEDOBJECT}" <<'PY'
import datetime, json, math, re, sys
path, expected_name, scaledobject = sys.argv[1:]
with open(path) as stream:
    payload = json.load(stream)
assert payload.get('kind') == 'ExternalMetricValueList', payload
items = payload.get('items', [])
assert len(items) == 1, f'{expected_name}: expected one external metric item, got {len(items)}'
item = items[0]
assert item['metricName'] == expected_name, item
labels = item.get('metricLabels')
assert labels is None or isinstance(labels, dict), item
if labels:
    assert labels.get('scaledobject.keda.sh/name') == scaledobject, labels
# Stock KEDA may return null labels and does not promise describedObject here.
# Namespace and selector correctness are asserted on the request path instead.
value = item['value']
match = re.fullmatch(r'([-+]?(?:\d+(?:\.\d*)?|\.\d+))(m?)', value)
assert match, f'{expected_name}: non-numeric value {value}'
number = float(match.group(1)) / (1000.0 if match.group(2) else 1.0)
assert math.isfinite(number), f'{expected_name}: non-finite value'
timestamp = item.get('timestamp')
assert isinstance(timestamp, str) and timestamp.strip(), f'{expected_name}: missing timestamp'
parsed_timestamp = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
assert parsed_timestamp.tzinfo is not None, f'{expected_name}: timestamp lacks timezone'
PY
}

external_metric_numeric_value() {
  local response_file="$1"
  python3 - "${response_file}" <<'PY'
import json, math, re, sys
with open(sys.argv[1]) as stream:
    payload = json.load(stream)
items = payload.get('items', [])
if len(items) != 1:
    raise SystemExit(3)
value = items[0].get('value', '')
match = re.fullmatch(r'([-+]?(?:\d+(?:\.\d*)?|\.\d+))(m?)', value)
assert match, value
number = float(match.group(1)) / (1000.0 if match.group(2) else 1.0)
assert math.isfinite(number)
print(format(number, '.15g'))
PY
}

hpa_metric_name_for_target() {
  local hpa_file="$1" target="$2"
  python3 - "${hpa_file}" "${target}" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    hpa = json.load(stream)
target = sys.argv[2]
names = [metric['external']['metric']['name']
         for metric in hpa['spec']['metrics']
         if metric['external']['target']['type'] == 'AverageValue'
         and metric['external']['target']['averageValue'] == target]
assert len(names) == 1, (target, names)
print(names[0])
PY
}

verify_external_metric_validator() {
  local fixture_dir="${TEMP_DIR}/external-validator-fixtures"
  local fixture_name fixture_file metric_name=s0-prometheus
  local request_path="/apis/external.metrics.k8s.io/v1beta1/namespaces/${NAMESPACE}/${metric_name}?labelSelector=scaledobject.keda.sh%2Fname%3D${SCALEDOBJECT}"
  local wrong_request_path="${request_path%"${SCALEDOBJECT}"}wrong"
  mkdir -p "${fixture_dir}"
  python3 - "${fixture_dir}" "${metric_name}" "${SCALEDOBJECT}" <<'PY'
import copy, json, pathlib, sys
fixture_dir, metric_name, scaledobject = sys.argv[1:]
fixture_dir = pathlib.Path(fixture_dir)
base = {
    'kind': 'ExternalMetricValueList',
    'apiVersion': 'external.metrics.k8s.io/v1beta1',
    'items': [{
        'metricName': metric_name,
        'metricLabels': None,
        'timestamp': '2026-07-20T14:36:48Z',
        'value': '1',
    }],
}
fixtures = {
    'null-labels': base,
    'empty-labels': copy.deepcopy(base),
    'populated-labels': copy.deepcopy(base),
    'empty-items': copy.deepcopy(base),
    'multiple-items': copy.deepcopy(base),
    'wrong-name': copy.deepcopy(base),
    'nonnumeric': copy.deepcopy(base),
    'missing-timestamp': copy.deepcopy(base),
}
fixtures['empty-labels']['items'][0]['metricLabels'] = {}
fixtures['populated-labels']['items'][0]['metricLabels'] = {
    'scaledobject.keda.sh/name': scaledobject,
    'implementation-detail': 'allowed',
}
fixtures['empty-items']['items'] = []
fixtures['multiple-items']['items'].append(copy.deepcopy(base['items'][0]))
fixtures['wrong-name']['items'][0]['metricName'] = 'wrong-prometheus'
fixtures['nonnumeric']['items'][0]['value'] = 'not-a-number'
del fixtures['missing-timestamp']['items'][0]['timestamp']
for name, payload in fixtures.items():
    with (fixture_dir / f'{name}.json').open('w') as stream:
        json.dump(payload, stream)
        stream.write('\n')
PY

  validate_external_metric_request_path "${request_path}" "${metric_name}"
  if validate_external_metric_request_path "${wrong_request_path}" \
      "${metric_name}" >/dev/null 2>&1; then
    fail "external metric request-path validator accepted the wrong selector"
  fi

  for fixture_name in null-labels empty-labels populated-labels; do
    fixture_file="${fixture_dir}/${fixture_name}.json"
    validate_external_metric_response "${fixture_file}" "${metric_name}"
  done
  for fixture_name in empty-items multiple-items wrong-name nonnumeric missing-timestamp; do
    fixture_file="${fixture_dir}/${fixture_name}.json"
    if validate_external_metric_response "${fixture_file}" "${metric_name}" \
        >/dev/null 2>&1; then
      fail "external metric validator accepted failing fixture ${fixture_name}"
    fi
  done
  cp -R "${fixture_dir}" "${ARTIFACT_DIR}/external-validator-fixtures"
}

wait_for_phase_b_metric_path() {
  local hpa_file="${ARTIFACT_DIR}/generated-hpa-final.json"
  local queue_metric_name running_metric_name
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0
  local running_file queue_file external_running_file external_queue_file
  local running_output queue_output external_running_output external_queue_output
  local running queue external_running external_queue
  local running_class queue_class external_running_class external_queue_class

  queue_metric_name="$(hpa_metric_name_for_target "${hpa_file}" 1)"
  running_metric_name="$(hpa_metric_name_for_target "${hpa_file}" 16)"
  printf 'queue\t%s\nrunning\t%s\n' \
    "${queue_metric_name}" "${running_metric_name}" \
    > "${ARTIFACT_DIR}/phase-b-external-metric-names.tsv"

  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    running_file="${ARTIFACT_DIR}/phase-b-prometheus-running-${attempt}.json"
    queue_file="${ARTIFACT_DIR}/phase-b-prometheus-queue-${attempt}.json"
    external_running_file="${ARTIFACT_DIR}/phase-b-external-running-${attempt}.json"
    external_queue_file="${ARTIFACT_DIR}/phase-b-external-queue-${attempt}.json"
    running_output="${running_file}.value"
    queue_output="${queue_file}.value"
    external_running_output="${external_running_file}.value"
    external_queue_output="${external_queue_file}.value"
    running=unavailable
    queue=unavailable
    external_running=unavailable
    external_queue=unavailable

    if prometheus_query "${RUNNING_QUERY}" "${running_file}" && \
       prometheus_query "${QUEUE_QUERY}" "${queue_file}" && \
       query_external_metric "${running_metric_name}" "${external_running_file}" && \
       query_external_metric "${queue_metric_name}" "${external_queue_file}" && \
       validate_external_metric_response "${external_running_file}" "${running_metric_name}" && \
       validate_external_metric_response "${external_queue_file}" "${queue_metric_name}"; then
      running_class="$(classify_poll_probe "${running_output}" \
        prometheus_aggregate_value "${running_file}")"
      queue_class="$(classify_poll_probe "${queue_output}" \
        prometheus_aggregate_value "${queue_file}")"
      external_running_class="$(classify_poll_probe "${external_running_output}" \
        external_metric_numeric_value "${external_running_file}")"
      external_queue_class="$(classify_poll_probe "${external_queue_output}" \
        external_metric_numeric_value "${external_queue_file}")"
      case "${running_class}:${queue_class}:${external_running_class}:${external_queue_class}" in
        success:success:success:success)
          read_probe_value "${running_output}"
          running="${PROBE_VALUE}"
          read_probe_value "${queue_output}"
          queue="${PROBE_VALUE}"
          read_probe_value "${external_running_output}"
          external_running="${PROBE_VALUE}"
          read_probe_value "${external_queue_output}"
          external_queue="${PROBE_VALUE}"
          if numeric_equals "${running}" 1 && numeric_equals "${queue}" 2 && \
             numeric_in_range "${external_running}" 0 3 && \
             numeric_equals "${external_queue}" 2; then
            streak=$((streak + 1))
          else
            streak=0
          fi
          ;;
        *error:*) fail "unexpected Phase B Prometheus/KEDA probe classification running=${running_class},queue=${queue_class},external_running=${external_running_class},external_queue=${external_queue_class}" ;;
        *miss*) streak=0 ;;
        *) fail "unknown Phase B Prometheus/KEDA probe classification running=${running_class},queue=${queue_class},external_running=${external_running_class},external_queue=${external_queue_class}" ;;
      esac
    else
      streak=0
    fi

    record_poll phase-b-prometheus-and-keda \
      "prom_running=${running},prom_queue=${queue},external_running=${external_running},external_queue=${external_queue}" \
      "${streak}"
    if (( streak >= STABLE_SAMPLES )); then
      cp "${running_file}" "${ARTIFACT_DIR}/phase-b-prometheus-running-final.json"
      cp "${queue_file}" "${ARTIFACT_DIR}/phase-b-prometheus-queue-final.json"
      cp "${external_running_file}" "${ARTIFACT_DIR}/phase-b-external-running-final.json"
      cp "${external_queue_file}" "${ARTIFACT_DIR}/phase-b-external-queue-final.json"
      return 0
    fi
    require_all_stimulus_alive "before Phase B Prometheus and KEDA evidence became stable"
    sleep "${POLL_INTERVAL_SECONDS}"
  done
  fail "Phase B did not retain stable Prometheus running=1/queue=2 and raw KEDA queue=2 evidence"
}

validate_phase_b_replica_sample() {
  local hpa_file="$1" deployment_file="$2"
  python3 - "${hpa_file}" "${deployment_file}" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    hpa = json.load(stream)
with open(sys.argv[2]) as stream:
    deployment = json.load(stream)
values = {
    'hpa_current': hpa.get('status', {}).get('currentReplicas'),
    'hpa_desired': hpa.get('status', {}).get('desiredReplicas'),
    'deployment_spec': deployment.get('spec', {}).get('replicas'),
    'deployment_status': deployment.get('status', {}).get('replicas', 0),
    'deployment_ready': deployment.get('status', {}).get('readyReplicas', 0),
}
assert all(isinstance(value, int) for value in values.values()), values
if any(value > 2 for value in values.values()):
    raise SystemExit(42)
assert values['hpa_current'] >= 1, values
assert values['hpa_desired'] >= 1, values
assert values['deployment_spec'] >= 1, values
assert values['deployment_status'] >= 0, values
assert values['deployment_ready'] >= 0, values
print('\t'.join(str(values[name]) for name in (
    'hpa_current', 'hpa_desired', 'deployment_spec',
    'deployment_status', 'deployment_ready')))
if not all(value == 2 for value in values.values()):
    raise SystemExit(3)
PY
}

verify_phase_b_replica_guard() {
  local fixture_dir="${TEMP_DIR}/phase-b-replica-fixtures"
  local accepted="${fixture_dir}/accepted.json"
  local transition="${fixture_dir}/transition.json"
  local over_two="${fixture_dir}/over-two.json"
  local deployment="${fixture_dir}/deployment.json"
  local probe_exit
  mkdir -p "${fixture_dir}"
  printf '%s\n' '{"status":{"currentReplicas":2,"desiredReplicas":2}}' > "${accepted}"
  printf '%s\n' '{"status":{"currentReplicas":1,"desiredReplicas":2}}' > "${transition}"
  printf '%s\n' '{"status":{"currentReplicas":2,"desiredReplicas":3}}' > "${over_two}"
  printf '%s\n' '{"spec":{"replicas":2},"status":{"replicas":2,"readyReplicas":2}}' > "${deployment}"
  validate_phase_b_replica_sample "${accepted}" "${deployment}" > "${fixture_dir}/accepted.tsv"
  if validate_phase_b_replica_sample "${transition}" "${deployment}" \
      > "${fixture_dir}/transition.tsv"; then
    fail "Phase B replica guard treated a transition sample as stable"
  else
    probe_exit=$?
  fi
  [[ ${probe_exit} -eq 3 ]] || fail "Phase B replica guard lost transition status 3"
  if validate_phase_b_replica_sample "${over_two}" "${deployment}" \
      > "${fixture_dir}/over-two.tsv" 2> "${fixture_dir}/over-two.stderr"; then
    fail "Phase B replica guard accepted desiredReplicas greater than two"
  else
    probe_exit=$?
  fi
  [[ ${probe_exit} -eq 42 ]] || fail "Phase B replica guard did not return disproof status 42"
  cp -R "${fixture_dir}" "${ARTIFACT_DIR}/phase-b-replica-fixtures"
}

validate_hpa_contract() {
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0
  local hpa_file="${ARTIFACT_DIR}/generated-hpa-final.json"
  local metric_names=() metric_name_line
  while (( SECONDS < deadline )); do
    if poll_kubectl_get hpa/${EXPECTED_HPA} -n "${NAMESPACE}" -o json \
        > "${ARTIFACT_DIR}/generated-hpa-probe.json" 2>/dev/null; then
      break
    fi
    record_poll generated-hpa absent 0
    sleep "${POLL_INTERVAL_SECONDS}"
  done
  poll_kubectl_get hpa/${EXPECTED_HPA} -n "${NAMESPACE}" -o json > "${hpa_file}" \
    || fail "KEDA did not generate ${EXPECTED_HPA}"

  python3 - "${hpa_file}" "${ARTIFACT_DIR}/scaledobject-final.json" <<'PY'
import json, sys
with open(sys.argv[1]) as stream:
    hpa = json.load(stream)
with open(sys.argv[2]) as stream:
    so = json.load(stream)
assert hpa['metadata']['ownerReferences'] == [{
    'apiVersion': 'keda.sh/v1alpha1', 'kind': 'ScaledObject',
    'name': 'optimized-baseline-keda-epp', 'uid': so['metadata']['uid'],
    'controller': True, 'blockOwnerDeletion': True}]
spec = hpa['spec']
assert spec['scaleTargetRef'] == {
    'apiVersion': 'apps/v1', 'kind': 'Deployment',
    'name': 'optimized-baseline-nvidia-gpu-vllm-decode'}
assert spec['minReplicas'] == 1 and spec['maxReplicas'] == 8
assert spec['behavior'] == {
    'scaleUp': {'selectPolicy': 'Max', 'stabilizationWindowSeconds': 0,
                'policies': [{'type': 'Percent', 'value': 100, 'periodSeconds': 15}]},
    'scaleDown': {'selectPolicy': 'Max', 'stabilizationWindowSeconds': 300,
                  'policies': [{'type': 'Percent', 'value': 100, 'periodSeconds': 15}]}}
metrics = spec['metrics']
assert len(metrics) == 2
names, targets = [], []
for metric in metrics:
    assert metric['type'] == 'External'
    external = metric['external']
    names.append(external['metric']['name'])
    assert external['metric']['selector']['matchLabels'] == {
        'scaledobject.keda.sh/name': 'optimized-baseline-keda-epp'}
    assert external['target']['type'] == 'AverageValue'
    targets.append(external['target']['averageValue'])
assert len(set(names)) == 2 and all(names)
assert sorted(targets, key=int) == ['1', '16']
with open(sys.argv[1] + '.metric-names', 'w') as stream:
    stream.write('\n'.join(names) + '\n')
PY

  while IFS= read -r metric_name_line; do
    [[ -n ${metric_name_line} ]] || fail "generated HPA exposed an empty metric name"
    metric_names[${#metric_names[@]}]="${metric_name_line}"
    (( ${#metric_names[@]} <= 2 )) \
      || fail "generated HPA exposed more than two metric names"
  done < "${hpa_file}.metric-names"
  [[ ${#metric_names[@]} -eq 2 ]] || fail "generated HPA did not expose two metric names"

  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    local current_file="${ARTIFACT_DIR}/generated-hpa-current-${attempt}.json"
    local current_output="${current_file}.values"
    local all_numeric=true metric_name metric_file current_values current_class
    poll_kubectl_get hpa/${EXPECTED_HPA} -n "${NAMESPACE}" -o json \
      > "${current_file}"
    for metric_name in "${metric_names[@]}"; do
      metric_file="${ARTIFACT_DIR}/external-${metric_name//[^a-zA-Z0-9_.-]/_}-${attempt}.json"
      if ! query_external_metric "${metric_name}" "${metric_file}" || \
         ! validate_external_metric_response "${metric_file}" "${metric_name}"; then
        all_numeric=false
      fi
    done
    current_class="$(classify_poll_probe "${current_output}" \
      python3 - "${current_file}" "${metric_names[@]}" <<'PY'
import json, math, re, sys
with open(sys.argv[1]) as stream:
    hpa = json.load(stream)
expected = set(sys.argv[2:])
metrics = hpa.get('status', {}).get('currentMetrics', [])
if len(metrics) < 2:
    raise SystemExit(3)
assert len(metrics) == 2
assert hpa['status']['currentReplicas'] == 1
assert hpa['status']['desiredReplicas'] == 1
seen, values = set(), []
for metric in metrics:
    external = metric['external']
    name = external['metric']['name']
    value = external['current']['averageValue']
    match = re.fullmatch(r'([-+]?(?:\d+(?:\.\d*)?|\.\d+))(m?)', value)
    assert match
    number = float(match.group(1)) / (1000.0 if match.group(2) else 1.0)
    assert math.isfinite(number)
    seen.add(name)
    values.append(f'{name}={value}')
assert seen == expected, (seen, expected)
print(','.join(sorted(values)))
PY
)"
    case "${current_class}" in
      success)
        read_probe_value "${current_output}"
        current_values="${PROBE_VALUE}"
        ;;
      miss) current_values="" ;;
      error:*) fail "unexpected HPA currentMetrics probe status ${current_class#error:}" ;;
      *) fail "unknown HPA currentMetrics probe classification: ${current_class}" ;;
    esac
    if [[ ${all_numeric} == true && -n ${current_values} ]]; then
      streak=$((streak + 1))
    else
      streak=0
    fi
    record_poll hpa-current-and-external-metrics \
      "${current_values:-unavailable}" "${streak}"
    if (( streak >= STABLE_SAMPLES )); then
      cp "${current_file}" "${hpa_file}"
      return 0
    fi
    sleep "${HPA_SAMPLE_INTERVAL_SECONDS}"
  done
  fail "both KEDA external metrics and HPA currentMetrics did not become stably numeric"
}

wait_for_bounded_scale_up() {
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS)) attempt=0 streak=0
  local hpa_file deployment_file sample_file sample_value sample_exit
  local desired_two_seen=false
  local history_file="${ARTIFACT_DIR}/phase-b-replica-history.tsv"
  printf 'elapsed_seconds\thpa_current\thpa_desired\tdeployment_spec\tdeployment_status\tdeployment_ready\tstable_streak\n' \
    > "${history_file}"

  while (( SECONDS < deadline )); do
    attempt=$((attempt + 1))
    hpa_file="${ARTIFACT_DIR}/phase-b-hpa-${attempt}.json"
    deployment_file="${ARTIFACT_DIR}/phase-b-deployment-${attempt}.json"
    sample_file="${ARTIFACT_DIR}/phase-b-replica-sample-${attempt}.tsv"
    if ! poll_kubectl_get hpa/${EXPECTED_HPA} -n "${NAMESPACE}" -o json \
         > "${hpa_file}" || \
       ! poll_kubectl_get deployment/${MODEL_DEPLOYMENT} -n "${NAMESPACE}" -o json \
         > "${deployment_file}"; then
      streak=0
      record_poll phase-b-bounded-scale unavailable "${streak}"
      sleep "${HPA_SAMPLE_INTERVAL_SECONDS}"
      continue
    fi

    if validate_phase_b_replica_sample "${hpa_file}" "${deployment_file}" \
        > "${sample_file}"; then
      sample_exit=0
    else
      sample_exit=$?
    fi
    sample_value="$(cat "${sample_file}")"
    if [[ ${sample_exit} -eq 42 ]]; then
      printf '%s\t%s\t%s\n' "${SECONDS}" "${sample_value:-over-two}" "${streak}" \
        >> "${history_file}"
      fail "Phase B observed a replica value greater than two: ${sample_value:-see ${hpa_file} and ${deployment_file}}"
    fi
    [[ ${sample_exit} -eq 0 || ${sample_exit} -eq 3 ]] \
      || fail "Phase B replica sample was malformed (status ${sample_exit})"
    [[ -n ${sample_value} ]] || fail "Phase B replica sample produced no values"

    if [[ $(printf '%s' "${sample_value}" | cut -f2) == 2 ]]; then
      desired_two_seen=true
    fi
    if [[ ${sample_exit} -eq 0 ]]; then
      streak=$((streak + 1))
    else
      streak=0
    fi
    printf '%s\t%s\t%s\n' "${SECONDS}" "${sample_value}" "${streak}" \
      >> "${history_file}"
    record_poll phase-b-bounded-scale "${sample_value//$'\t'/,}" "${streak}"
    if (( streak >= STABLE_SAMPLES )); then
      [[ ${desired_two_seen} == true ]] \
        || fail "Phase B reached two replicas without observing desiredReplicas=2"
      cp "${hpa_file}" "${ARTIFACT_DIR}/phase-b-hpa-final.json"
      cp "${deployment_file}" "${ARTIFACT_DIR}/phase-b-deployment-final.json"
      poll_kubectl_get pods -n "${NAMESPACE}" \
        -l 'llm-d.ai/guide=optimized-baseline,llm-d.ai/role=decode' -o json \
        > "${ARTIFACT_DIR}/phase-b-target-pods-final.json"
      return 0
    fi
    require_all_stimulus_alive "before the bounded 1-to-2 transition became stable"
    sleep "${HPA_SAMPLE_INTERVAL_SECONDS}"
  done
  fail "target Deployment and generated HPA did not stably reach exactly two replicas"
}

stop_stimulus() {
  stop_pid "${SECOND_QUEUED_REQUEST_PID}"
  SECOND_QUEUED_REQUEST_PID=""
  stop_pid "${QUEUED_REQUEST_PID}"
  QUEUED_REQUEST_PID=""
  stop_pid "${RUNNING_REQUEST_PID}"
  RUNNING_REQUEST_PID=""
}

run_deterministic_stimulus() {
  cat > "${TEMP_DIR}/request.json" <<JSON
{"model":"${MODEL_ID}","messages":[{"role":"user","content":"bounded deterministic contract probe"}],"max_tokens":8,"temperature":0}
JSON
  start_request \
    "${ARTIFACT_DIR}/running-request.json" "${ARTIFACT_DIR}/running-request.stderr"
  RUNNING_REQUEST_PID="${BACKGROUND_CHILD_PID}"
  wait_for_direct_running
  start_request \
    "${ARTIFACT_DIR}/queued-request.json" "${ARTIFACT_DIR}/queued-request.stderr"
  QUEUED_REQUEST_PID="${BACKGROUND_CHILD_PID}"
  wait_for_direct_pair
  capture_memory stimulus-active
}

run_phase_b_stimulus() {
  start_request \
    "${ARTIFACT_DIR}/phase-b-second-queued-request.json" \
    "${ARTIFACT_DIR}/phase-b-second-queued-request.stderr"
  SECOND_QUEUED_REQUEST_PID="${BACKGROUND_CHILD_PID}"
  wait_for_phase_b_direct_pair
  wait_for_phase_b_metric_path
  capture_memory phase-b-stimulus-active
}

main() {
  if [[ ${VERIFY_ARTIFACTS} == true ]]; then
    verify_artifact_trees "${VERIFY_ARTIFACT_PATHS[@]}"
    return
  fi

  begin_stage static_validation
  static_checks
  if [[ ${STATIC_ONLY} == true ]]; then
    finish_stage passed
    STAGE=complete
    log "static validation passed"
    return 0
  fi

  begin_stage cluster_create
  create_cluster

  begin_stage stack_setup
  install_stack

  begin_stage workload_readiness
  wait_for_workloads

  begin_stage prometheus_tls_target
  start_port_forwards
  wait_for_prometheus_target

  begin_stage deterministic_stimulus
  run_deterministic_stimulus

  begin_stage prometheus_metric_contract
  wait_for_prometheus_pair

  begin_stage keda_contract
  validate_scaledobject_contract

  begin_stage hpa_contract
  validate_hpa_contract

  begin_stage phase_b_stimulus
  run_phase_b_stimulus

  begin_stage phase_b_scale_up
  wait_for_bounded_scale_up
  stop_stimulus
  capture_keda_operator_logs success
  capture_memory success

  finish_stage passed
  STAGE=complete
  log "Phase A metric contracts and the bounded Phase B 1-to-2 transition passed"
}

main "$@"
