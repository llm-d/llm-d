#!/usr/bin/env bash
# Verifies install-otel-collector-loki.sh and the logging manifests (llm-d#2926).
#
# The installer runs for real against the stub binaries in tests/bin, so no
# cluster is required. The manifest checks cover the settings that fail
# silently when they are missing: an agent that skips the tainted GPU nodes, a
# pipeline that leaves trace_id off the log record, or a Loki that turns pod
# uids into stream labels.

# Expected values such as ${env:LOG_NAMESPACES} are literal Collector placeholders.
# shellcheck disable=SC2016
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY="$(cd "${TEST_DIR}/../../../.." && pwd)"
INSTALLER="${REPOSITORY}/guides/recipes/observability/install-otel-collector-loki.sh"
AGENT="${REPOSITORY}/guides/recipes/observability/logging/otel-collector-logs.yaml"
LOKI="${REPOSITORY}/guides/recipes/observability/logging/loki.yaml"
STUB_DIR="${TEST_DIR}/bin"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/llm-d-logs-test.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

FAILURES=0

command -v yq >/dev/null || { echo "missing required tool: yq" >&2; exit 1; }

check() { # description expected actual
  if [[ "$2" == "$3" ]]; then
    printf '  ok   %s\n' "$1"
  else
    printf '  FAIL %s: expected [%s], got [%s]\n' "$1" "$2" "$3"
    FAILURES=$((FAILURES + 1))
  fi
}

check_contains() { # description file text
  if grep -F -- "$3" "$2" >/dev/null; then
    printf '  ok   %s\n' "$1"
  else
    printf '  FAIL %s: [%s] not in %s\n' "$1" "$3" "$2"
    FAILURES=$((FAILURES + 1))
  fi
}

check_lacks() { # description file text
  if grep -F -- "$3" "$2" >/dev/null; then
    printf '  FAIL %s: [%s] unexpectedly in %s\n' "$1" "$3" "$2"
    FAILURES=$((FAILURES + 1))
  else
    printf '  ok   %s\n' "$1"
  fi
}

# run NAME [installer args...] runs the installer against the stubs. Its output
# goes to NAME.log, the commands that changed the cluster to NAME.mutations and
# its exit status to NAME.status. Callers set STUB_* in the environment.
run() {
  local name="$1"
  shift
  : > "${WORK_DIR}/${name}.stub"
  : > "${WORK_DIR}/${name}.mutations"
  local status=0
  env \
    PATH="${STUB_DIR}:/usr/bin:/bin" \
    STUB_COMMAND=unused \
    STUB_MODE=logs \
    STUB_LOG="${WORK_DIR}/${name}.stub" \
    STUB_MUTATIONS="${WORK_DIR}/${name}.mutations" \
    bash "$INSTALLER" "$@" >"${WORK_DIR}/${name}.log" 2>&1 || status=$?
  echo "$status" > "${WORK_DIR}/${name}.status"
}

status() { cat "${WORK_DIR}/$1.status"; }
mutations() { echo "${WORK_DIR}/$1.mutations"; }
mutation_count() { grep -c . "$(mutations "$1")" || true; }
# line NAME TEXT prints the mutation line number that contains TEXT.
line() { grep -n -F -- "$2" "$(mutations "$1")" | head -n1 | cut -d: -f1; }

echo "fresh install"
run fresh -n llm-d-a
check "succeeds" "0" "$(status fresh)"
check_contains "creates the logging namespace" "$(mutations fresh)" "args=[create][namespace][llm-d-logging]"
check_contains "marks the namespace as created by the script" "$(mutations fresh)" \
  "args=[annotate][namespace][llm-d-logging][llm-d.ai/created-by=install-otel-collector-loki]"
check_contains "labels it for the privileged pod security level" "$(mutations fresh)" \
  "args=[label][namespace][llm-d-logging][pod-security.kubernetes.io/enforce=privileged]"
check_contains "deploys loki" "$(mutations fresh)" "args=[apply][-n][llm-d-logging][-f][${LOKI}]"
check_contains "deploys the agent" "$(mutations fresh)" "args=[apply][-n][llm-d-logging][-f][${AGENT}]"
check_contains "points the agent at the namespace and the bundled loki" "$(mutations fresh)" \
  "[LOG_NAMESPACES=llm-d-a][LOKI_OTLP_ENDPOINT=http://loki:3100/otlp]"
# A DaemonSet that Pod Security rejects creates no pods and reports no error, so
# the label has to be in place before the agent is applied.
check "labels the namespace before applying the agent" "true" \
  "$([[ "$(line fresh "[label]")" -lt "$(line fresh "-f][${AGENT}]")" ]] && echo true || echo false)"

echo "second namespace added to a running agent"
STUB_EXISTING_NAMESPACES="llm-d-logging" STUB_ENFORCE_LEVEL=privileged STUB_COLLECTED_NAMESPACES=llm-d-a,llm-d-b \
  run add -n llm-d-c -n llm-d-b
check "succeeds" "0" "$(status add)"
check_contains "keeps the collected namespaces and adds the new one, sorted and once each" "$(mutations add)" \
  "[LOG_NAMESPACES=llm-d-a,llm-d-b,llm-d-c]"
check_lacks "does not recreate the namespace" "$(mutations add)" "[create][namespace]"
check_lacks "does not relabel a privileged namespace" "$(mutations add)" "[label]"

echo "existing namespace without a pod security label"
STUB_EXISTING_NAMESPACES="llm-d-logging" run unlabeled -n llm-d-a
check "succeeds" "0" "$(status unlabeled)"
check_contains "labels it" "$(mutations unlabeled)" "[pod-security.kubernetes.io/enforce=privileged]"
check_lacks "does not claim a namespace it did not create" "$(mutations unlabeled)" "[annotate]"

echo "logging namespace enforces baseline"
STUB_EXISTING_NAMESPACES="llm-d-logging" STUB_ENFORCE_LEVEL=baseline run baseline -n llm-d-a
check "fails" "1" "$(status baseline)"
check "changes nothing" "0" "$(mutation_count baseline)"
check_contains "names the level that blocks the agent" "${WORK_DIR}/baseline.log" "'baseline' Pod Security level"

echo "custom OTLP endpoint"
run endpoint -n llm-d-a -l observability --otlp-endpoint http://loki.loki.svc.cluster.local:3100/otlp
check "succeeds" "0" "$(status endpoint)"
check_lacks "does not deploy loki" "$(mutations endpoint)" "loki.yaml"
check_contains "uses the logging namespace from -l" "$(mutations endpoint)" "[apply][-n][observability]"
check_contains "points the agent at the endpoint" "$(mutations endpoint)" \
  "[LOKI_OTLP_ENDPOINT=http://loki.loki.svc.cluster.local:3100/otlp]"

echo "agent rejected at admission"
STUB_AGENT_ROLLOUT_STATUS=1 run rejected -n llm-d-a
check "fails" "1" "$(status rejected)"
check_contains "prints the DaemonSet events" "${WORK_DIR}/rejected.log" "violates PodSecurity"

echo "unsupported platforms"
STUB_OPENSHIFT=1 run openshift -n llm-d-a
check "openshift fails" "1" "$(status openshift)"
check "openshift changes nothing" "0" "$(mutation_count openshift)"
check_contains "openshift points to OpenShift Logging" "${WORK_DIR}/openshift.log" "ClusterLogForwarder"
STUB_NODE_NAME=gk3-cluster-pool-1-abcd run autopilot -n llm-d-a
check "autopilot fails" "1" "$(status autopilot)"
check "autopilot changes nothing" "0" "$(mutation_count autopilot)"
check_contains "autopilot points to Cloud Logging" "${WORK_DIR}/autopilot.log" "Cloud Logging"

echo "bad arguments"
run no-namespace
check "no -n fails" "1" "$(status no-namespace)"
check "no -n changes nothing" "0" "$(mutation_count no-namespace)"
run bad-namespace -n 'llm-d-a/../*'
check "invalid namespace fails" "1" "$(status bad-namespace)"
check "invalid namespace changes nothing" "0" "$(mutation_count bad-namespace)"

echo "uninstall"
STUB_EXISTING_NAMESPACES="llm-d-logging" STUB_CREATED_BY=install-otel-collector-loki run uninstall-owned -u
check "succeeds" "0" "$(status uninstall-owned)"
check_contains "removes the agent" "$(mutations uninstall-owned)" "args=[delete][-n][llm-d-logging][-f][${AGENT}]"
check_contains "removes loki" "$(mutations uninstall-owned)" "args=[delete][-n][llm-d-logging][-f][${LOKI}]"
check_contains "deletes the namespace it created" "$(mutations uninstall-owned)" "args=[delete][namespace][llm-d-logging]"
STUB_EXISTING_NAMESPACES="llm-d-logging" run uninstall-shared -u
check_lacks "keeps a namespace it did not create" "$(mutations uninstall-shared)" "[delete][namespace]"

echo "agent manifest"
DS='select(.kind=="DaemonSet") | .spec.template.spec'
CONTAINER="$DS | .containers[0]"
check "tolerates every taint, so it runs on GPU and TPU nodes" "Exists" \
  "$(yq e "$DS | .tolerations[] | select(.key == null) | .operator" "$AGENT")"
check "runs as root, the one exception" "0" "$(yq e "$DS | .securityContext.runAsUser" "$AGENT")"
check "no privilege escalation" "false" "$(yq e "$CONTAINER | .securityContext.allowPrivilegeEscalation" "$AGENT")"
check "drops all capabilities" "ALL" "$(yq e "$CONTAINER | .securityContext.capabilities.drop[0]" "$AGENT")"
check "adds no capabilities" "null" "$(yq e "$CONTAINER | .securityContext.capabilities.add" "$AGENT")"
check "not privileged" "null" "$(yq e "$CONTAINER | .securityContext.privileged" "$AGENT")"
check "read-only root filesystem" "true" "$(yq e "$CONTAINER | .securityContext.readOnlyRootFilesystem" "$AGENT")"
check "/var/log/pods mounted read-only" "true" \
  "$(yq e "$CONTAINER | .volumeMounts[] | select(.mountPath == \"/var/log/pods\") | .readOnly" "$AGENT")"
check "no service account token" "false" "$(yq e "$DS | .automountServiceAccountToken" "$AGENT")"
check "one checkpoint directory per install" '$(POD_NAMESPACE)' \
  "$(yq e "$CONTAINER | .volumeMounts[] | select(.name == \"storage\") | .subPathExpr" "$AGENT")"
check "collects nothing until the installer names namespaces" "" \
  "$(yq e "$CONTAINER | .env[] | select(.name == \"LOG_NAMESPACES\") | .value" "$AGENT")"

echo "agent pipeline"
CONFIG="${WORK_DIR}/agent-config.yaml"
yq e 'select(.kind=="ConfigMap") | .data."config.yaml"' "$AGENT" > "$CONFIG"
OPS='.receivers.filelog.operators'
index_of() { yq e "[${OPS}[].type] | to_entries | map(select(.value == \"$1\")) | .[0].key" "$CONFIG"; }
check "collects only the listed namespaces" '/var/log/pods/{${env:LOG_NAMESPACES}}_*/*/*.log' \
  "$(yq e '.receivers.filelog.include[0]' "$CONFIG")"
check "never reads its own namespace" '/var/log/pods/${env:POD_NAMESPACE}_*/*/*.log' \
  "$(yq e '.receivers.filelog.exclude[0]' "$CONFIG")"
check "unwraps the CRI line first" "0" "$(index_of container)"
check "parses JSON after the CRI line" "true" \
  "$([[ "$(index_of json_parser)" -gt "$(index_of container)" ]] && echo true || echo false)"
check "sets the trace context after parsing JSON" "true" \
  "$([[ "$(index_of trace_parser)" -gt "$(index_of json_parser)" ]] && echo true || echo false)"
check "trace_id comes from the parsed JSON" "attributes.trace_id" \
  "$(yq e "${OPS}[] | select(.type == \"trace_parser\") | .trace_id.parse_from" "$CONFIG")"
check "span_id comes from the parsed JSON" "attributes.span_id" \
  "$(yq e "${OPS}[] | select(.type == \"trace_parser\") | .span_id.parse_from" "$CONFIG")"
check "service.name from the JSON becomes a resource attribute" 'resource["service.name"]' \
  "$(yq e "${OPS}[] | select(.type == \"move\" and .from == \"attributes[\\\"service.name\\\"]\") | .to" "$CONFIG")"
check "filelog keeps its offsets on disk" "file_storage" "$(yq e '.receivers.filelog.storage' "$CONFIG")"
check "the export queue survives restarts" "file_storage" \
  "$(yq e '.exporters."otlphttp/loki".sending_queue.storage' "$CONFIG")"
check "the backend is one setting" '${env:LOKI_OTLP_ENDPOINT}' \
  "$(yq e '.exporters."otlphttp/loki".endpoint' "$CONFIG")"
# A batch processor accepts every batch at once, so export failures would never
# reach filelog's retry and its offsets would move past lines that were lost.
check "no batch processor between filelog and the exporter" "memory_limiter" \
  "$(yq e '.service.pipelines.logs.processors | join(",")' "$CONFIG")"

echo "loki"
LOKI_CONFIG="${WORK_DIR}/loki-config.yaml"
yq e 'select(.kind=="ConfigMap") | .data."config.yaml"' "$LOKI" > "$LOKI_CONFIG"
LABELS='.limits_config.otlp_config.resource_attributes.attributes_config[] | select(.action == "index_label") | .attributes'
check "structured metadata is on" "true" "$(yq e '.limits_config.allow_structured_metadata' "$LOKI_CONFIG")"
check "stream labels are exactly namespace, pod, container and service" \
  "k8s.container.name,k8s.namespace.name,k8s.pod.name,service.name" \
  "$(yq e "[${LABELS}[]] | sort | join(\",\")" "$LOKI_CONFIG")"
check "loki's default label list is not added on top" "true" \
  "$(yq e '.limits_config.otlp_config.resource_attributes.ignore_defaults' "$LOKI_CONFIG")"
check "old logs are deleted" "true" "$(yq e '.compactor.retention_enabled' "$LOKI_CONFIG")"

echo "images"
for manifest in "$AGENT" "$LOKI"; do
  image="$(yq e 'select(.kind=="DaemonSet" or .kind=="Deployment") | .spec.template.spec.containers[0].image' "$manifest")"
  check "$(basename "$manifest") pins its image" "true" \
    "$([[ "$image" =~ :[0-9]+\.[0-9]+\.[0-9]+$ ]] && echo true || echo false)"
done

echo
if ((FAILURES > 0)); then
  echo "${FAILURES} assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
