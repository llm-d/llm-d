#!/usr/bin/env bash
set -Eeuo pipefail

# Validate the initial direct KEDA+EPP state on OpenShift. The generic inference
# smoke runs before this script, so the EPP metric label sets should exist, but
# this script does not generate autoscaling load or assert scale-up behavior.

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  -n, --namespace NAMESPACE   Kubernetes namespace (default: llm-d)
  -m, --model MODEL_ID        Model discovered by the generic smoke validator
  -h, --help                  Show this help and exit
EOF
  exit 0
}

NAMESPACE=llm-d
CLI_MODEL_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--namespace) NAMESPACE="$2"; shift 2 ;;
    -m|--model) CLI_MODEL_ID="$2"; shift 2 ;;
    -h|--help) show_help ;;
    *) echo "Unknown option: $1" >&2; show_help ;;
  esac
done

EXPECTED_MODEL_ID="Qwen/Qwen3-32B"
MODEL_DEPLOYMENT=optimized-baseline-nvidia-gpu-vllm-decode
EPP_DEPLOYMENT=optimized-baseline-epp
EPP_SERVICE=optimized-baseline-epp
EPP_SERVICEMONITOR=optimized-baseline-epp-monitor
MODEL_PODMONITOR=decode
SCALEDOBJECT=optimized-baseline-keda-epp
EXPECTED_HPA=keda-hpa-optimized-baseline
AUTH_SERVICEACCOUNT=keda-epp-prometheus
AUTH_SECRET=keda-prometheus-auth
TRIGGER_AUTHENTICATION=keda-prometheus-auth
THANOS_URL=https://thanos-querier.openshift-monitoring.svc.cluster.local:9091/api/v1/query
POLL_TIMEOUT_SECONDS="${POLL_TIMEOUT_SECONDS:-300}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-10}"
FALLBACK_POLL_INTERVAL_SECONDS="${FALLBACK_POLL_INTERVAL_SECONDS:-15}"
FALLBACK_REQUIRED_STREAK="${FALLBACK_REQUIRED_STREAK:-4}"
STABLE_REQUIRED_SAMPLES="${STABLE_REQUIRED_SAMPLES:-3}"
STABLE_SAMPLE_INTERVAL_SECONDS="${STABLE_SAMPLE_INTERVAL_SECONDS:-15}"
CURL_POD_NAME="curl-keda-epp-${RANDOM}-$$"
ARTIFACT_DIR="${ARTIFACT_DIR:-/tmp/pod-logs-${GUIDE_NAME:-workload-autoscaling}}"
HPA_NAME="${EXPECTED_HPA}"

namespace_hash() {
  if command -v sha256sum &>/dev/null; then
    printf '%s' "$1" | sha256sum | cut -c1-8
  else
    printf '%s' "$1" | shasum -a 256 | cut -c1-8
  fi
}

AUTH_CRB="keda-epp-prometheus-cluster-monitoring-view-$(namespace_hash "${NAMESPACE}")"
mkdir -p "${ARTIFACT_DIR}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

capture_namespaced_resource() {
  local resource="$1" file="$2"
  kubectl get "${resource}" -n "${NAMESPACE}" -o yaml > "${ARTIFACT_DIR}/${file}" 2>&1 || true
}

diagnostic_dump() {
  set +e
  echo "=== Direct KEDA+EPP failure diagnostics ===" >&2
  echo "Artifacts: ${ARTIFACT_DIR}" >&2

  capture_namespaced_resource deployment/${EPP_DEPLOYMENT} router-deployment.yaml
  capture_namespaced_resource service/${EPP_SERVICE} router-service.yaml
  capture_namespaced_resource endpoints/${EPP_SERVICE} router-endpoints.yaml
  capture_namespaced_resource servicemonitor/${EPP_SERVICEMONITOR} router-servicemonitor.yaml
  capture_namespaced_resource deployment/${MODEL_DEPLOYMENT} model-deployment.yaml
  capture_namespaced_resource podmonitor/${MODEL_PODMONITOR} model-podmonitor.yaml
  capture_namespaced_resource scaledobject/${SCALEDOBJECT} scaledobject.yaml
  capture_namespaced_resource triggerauthentication/${TRIGGER_AUTHENTICATION} triggerauthentication.yaml
  capture_namespaced_resource serviceaccount/${AUTH_SERVICEACCOUNT} auth-serviceaccount.yaml

  kubectl get secret/${AUTH_SECRET} -n "${NAMESPACE}" -o json 2>/dev/null \
    | jq 'del(.data, .stringData)' > "${ARTIFACT_DIR}/auth-secret-metadata.json" 2>&1 || true
  kubectl get replicasets -n "${NAMESPACE}" -o yaml \
    > "${ARTIFACT_DIR}/replicasets.yaml" 2>&1 || true
  kubectl get pods -n "${NAMESPACE}" -o yaml \
    > "${ARTIFACT_DIR}/pods.yaml" 2>&1 || true
  kubectl get events -n "${NAMESPACE}" --sort-by='.lastTimestamp' -o yaml \
    > "${ARTIFACT_DIR}/events.yaml" 2>&1 || true

  if [[ -n "${HPA_NAME}" ]]; then
    capture_namespaced_resource "hpa/${HPA_NAME}" generated-hpa.yaml
  else
    kubectl get hpa -n "${NAMESPACE}" -o yaml \
      > "${ARTIFACT_DIR}/generated-hpa.yaml" 2>&1 || true
  fi

  for selector in 'app=keda-operator' 'app.kubernetes.io/name=keda-operator'; do
    if kubectl get pods -n openshift-keda -l "${selector}" -o name 2>/dev/null | grep -q .; then
      kubectl logs -n openshift-keda -l "${selector}" --all-containers --tail=300 \
        2>&1 | grep -E "${NAMESPACE}|${SCALEDOBJECT}" \
        > "${ARTIFACT_DIR}/keda-operator-filtered.log" || true
      break
    fi
  done

  echo "--- ScaledObject conditions ---" >&2
  kubectl get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" \
    -o jsonpath='{range .status.conditions[*]}{.type}={.status} ({.reason}: {.message}){"\n"}{end}' \
    >&2 2>/dev/null || true
  echo "--- Workload status ---" >&2
  kubectl get deployment,replicaset,pod,hpa -n "${NAMESPACE}" -o wide >&2 2>/dev/null || true
  echo "=== End direct KEDA+EPP diagnostics ===" >&2
}

cleanup_curl_pod() {
  kubectl delete pod/${CURL_POD_NAME} -n "${NAMESPACE}" --ignore-not-found \
    >/dev/null 2>&1 || true
}

finish() {
  local status=$?
  trap - EXIT
  if [[ ${status} -ne 0 ]]; then
    diagnostic_dump
  fi
  cleanup_curl_pod
  exit "${status}"
}
trap finish EXIT

assert_exists() {
  local resource="$1"
  kubectl get "${resource}" -n "${NAMESPACE}" >/dev/null 2>&1 \
    || fail "expected ${resource} in namespace ${NAMESPACE}"
}

assert_absent() {
  local resource="$1"
  local output

  if output="$(kubectl get "${resource}" -n "${NAMESPACE}" 2>&1)"; then
    fail "forbidden WVA resource exists: ${resource}"
  elif ! grep -Eqi '\(NotFound\)|not found' <<< "${output}"; then
    fail "could not prove forbidden resource ${resource} is absent: ${output}"
  fi
}

echo "==> Validating expected resource graph"
[[ -z "${CLI_MODEL_ID}" || "${CLI_MODEL_ID}" == "${EXPECTED_MODEL_ID}" ]] \
  || fail "generic smoke discovered ${CLI_MODEL_ID}, expected ${EXPECTED_MODEL_ID}"

monitoring_label="$(kubectl get namespace "${NAMESPACE}" \
  -o jsonpath='{.metadata.labels.openshift\.io/user-monitoring}' 2>/dev/null || true)"
[[ "${monitoring_label}" == "true" ]] \
  || fail "namespace ${NAMESPACE} is not labeled openshift.io/user-monitoring=true"

for resource in \
  deployment/${EPP_DEPLOYMENT} \
  service/${EPP_SERVICE} \
  servicemonitor/${EPP_SERVICEMONITOR} \
  inferencepool/optimized-baseline \
  deployment/${MODEL_DEPLOYMENT} \
  podmonitor/${MODEL_PODMONITOR} \
  serviceaccount/${AUTH_SERVICEACCOUNT} \
  secret/${AUTH_SECRET} \
  triggerauthentication/${TRIGGER_AUTHENTICATION} \
  scaledobject/${SCALEDOBJECT}; do
  assert_exists "${resource}"
done

kubectl get "clusterrolebinding/${AUTH_CRB}" -o json \
  > "${ARTIFACT_DIR}/auth-clusterrolebinding.json" \
  || fail "expected ClusterRoleBinding/${AUTH_CRB}"
jq -e --arg namespace "${NAMESPACE}" '
  .roleRef.kind == "ClusterRole" and
  .roleRef.name == "cluster-monitoring-view" and
  any(.subjects[]?;
    .kind == "ServiceAccount" and
    .name == "keda-epp-prometheus" and
    .namespace == $namespace)
' "${ARTIFACT_DIR}/auth-clusterrolebinding.json" >/dev/null \
  || fail "ClusterRoleBinding/${AUTH_CRB} has the wrong role or subject"

for resource in \
  deployment/wva-controller-manager \
  servicemonitor/wva-controller-manager-metrics-monitor \
  scaledobject/optimized-baseline-nvidia-gpu-vllm-decode-scaler \
  triggerauthentication/wva-prometheus-auth \
  hpa/wva-keda-hpa-optimized-baseline-nvidia-gpu-vllm-decode; do
  assert_absent "${resource}"
done

api_resources="$(kubectl api-resources --namespaced=true --verbs=list -o name)" \
  || fail "could not enumerate namespaced API resources to check VariantAutoscaling absence"
variant_resource="$(grep -E '^variantautoscalings\.' <<< "${api_resources}" | head -1 || true)"
if [[ -n "${variant_resource}" ]]; then
  variant_resources="$(kubectl get "${variant_resource}" -n "${NAMESPACE}" -o name)" \
    || fail "could not list ${variant_resource} resources in namespace ${NAMESPACE}"
  [[ -z "${variant_resources}" ]] \
    || fail "VariantAutoscaling resources exist in namespace ${NAMESPACE}: ${variant_resources}"
fi

kubectl get service/${EPP_SERVICE} -n "${NAMESPACE}" -o json \
  > "${ARTIFACT_DIR}/router-service.json"
jq -e '
  any(.spec.ports[]; .name == "http-metrics" and .port == 9090) and
  any(.spec.ports[]; .name == "http" and .port == 80)
' "${ARTIFACT_DIR}/router-service.json" >/dev/null \
  || fail "${EPP_SERVICE} does not expose http-metrics:9090 and http:80"

kubectl get servicemonitor/${EPP_SERVICEMONITOR} -n "${NAMESPACE}" -o json \
  > "${ARTIFACT_DIR}/router-servicemonitor.json"
jq -e '
  any(.spec.endpoints[]; .port == "http-metrics" and .path == "/metrics")
' "${ARTIFACT_DIR}/router-servicemonitor.json" >/dev/null \
  || fail "${EPP_SERVICEMONITOR} does not scrape http-metrics:/metrics"
jq -s -e '
  .[0].spec.selector.matchLabels as $selector |
  .[1].metadata.labels as $labels |
  all($selector | to_entries[]; $labels[.key] == .value)
' "${ARTIFACT_DIR}/router-servicemonitor.json" \
  "${ARTIFACT_DIR}/router-service.json" >/dev/null \
  || fail "ServiceMonitor selector does not match the EPP Service labels"

kubectl get configmap/${EPP_DEPLOYMENT} -n "${NAMESPACE}" -o json \
  > "${ARTIFACT_DIR}/router-configmap.json"
jq -e '.data["optimized-baseline-keda-epp-plugins.yaml"] | contains("flowControl")' \
  "${ARTIFACT_DIR}/router-configmap.json" >/dev/null \
  || fail "EPP Flow Control configuration is not present"

kubectl get deployment/${MODEL_DEPLOYMENT} -n "${NAMESPACE}" -o json \
  > "${ARTIFACT_DIR}/model-deployment.json"
kubectl get podmonitor/${MODEL_PODMONITOR} -n "${NAMESPACE}" -o json \
  > "${ARTIFACT_DIR}/model-podmonitor.json"
jq -e '
  .spec.selector.matchLabels["llm-d.ai/role"] == "decode" and
  any(.spec.podMetricsEndpoints[]; .port == "modelserver" and .path == "/metrics")
' "${ARTIFACT_DIR}/model-podmonitor.json" >/dev/null \
  || fail "PodMonitor/decode selector or endpoint is incorrect"
jq -e '
  .spec.template.metadata.labels["llm-d.ai/role"] == "decode" and
  any(.spec.template.spec.containers[];
    .name == "modelserver" and
    any(.ports[]?; .name == "modelserver" and .containerPort == 8000))
' "${ARTIFACT_DIR}/model-deployment.json" >/dev/null \
  || fail "model pod labels or modelserver port do not match PodMonitor/decode"

echo "==> Validating initial model and EPP readiness"
jq -e '
  .spec.replicas == 1 and
  .status.readyReplicas == 1 and
  .status.availableReplicas == 1
' "${ARTIFACT_DIR}/model-deployment.json" >/dev/null \
  || fail "model Deployment is not at spec/ready/available replicas 1"

kubectl get deployment/${EPP_DEPLOYMENT} -n "${NAMESPACE}" -o json \
  > "${ARTIFACT_DIR}/router-deployment.json"
jq -e '.spec.replicas == 1 and .status.readyReplicas == 1 and .status.availableReplicas == 1' \
  "${ARTIFACT_DIR}/router-deployment.json" >/dev/null \
  || fail "EPP Deployment is not Ready at one replica"

decode_pod_count="$(kubectl get pods -n "${NAMESPACE}" -l llm-d.ai/role=decode -o json \
  | jq '[.items[] | select(.metadata.deletionTimestamp == null)] | length')"
[[ "${decode_pod_count}" == "1" ]] \
  || fail "expected exactly one non-terminating decode pod, found ${decode_pod_count}"

echo "==> Requiring a stable initial Deployment, pod, and HPA replica invariant"
initial_streak=0
deadline=$((SECONDS + POLL_TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  initial_sample_ok=false
  if kubectl get deployment/${MODEL_DEPLOYMENT} -n "${NAMESPACE}" -o json \
       > "${ARTIFACT_DIR}/model-deployment.json" 2>/dev/null && \
     kubectl get "hpa/${HPA_NAME}" -n "${NAMESPACE}" -o json \
       > "${ARTIFACT_DIR}/generated-hpa.json" 2>/dev/null && \
     kubectl get pods -n "${NAMESPACE}" -l llm-d.ai/role=decode -o json \
       > "${ARTIFACT_DIR}/decode-pods.json" 2>/dev/null && \
     jq -e '
       .spec.replicas == 1 and
       .status.readyReplicas == 1 and
       .status.availableReplicas == 1
     ' "${ARTIFACT_DIR}/model-deployment.json" >/dev/null && \
     jq -e '.status.currentReplicas == 1 and .status.desiredReplicas == 1' \
       "${ARTIFACT_DIR}/generated-hpa.json" >/dev/null && \
     jq -e '[.items[] | select(.metadata.deletionTimestamp == null)] | length == 1' \
       "${ARTIFACT_DIR}/decode-pods.json" >/dev/null; then
    initial_sample_ok=true
  fi

  if [[ "${initial_sample_ok}" == "true" ]]; then
    initial_streak=$((initial_streak + 1))
    echo "Initial replica sample ${initial_streak}/${STABLE_REQUIRED_SAMPLES}: Deployment=1 HPA=1 pods=1"
    if (( initial_streak >= STABLE_REQUIRED_SAMPLES )); then
      break
    fi
  else
    initial_streak=0
    echo "Initial replica invariant not yet stable: HPA=${HPA_NAME:-<none>}"
  fi
  sleep "${STABLE_SAMPLE_INTERVAL_SECONDS}"
done
(( initial_streak >= STABLE_REQUIRED_SAMPLES )) \
  || fail "Deployment, HPA, and pod replica invariant did not hold for ${STABLE_REQUIRED_SAMPLES} samples"

echo "==> Waiting for dedicated authentication Secret data"
auth_ready=false
for _ in $(seq 1 30); do
  if kubectl get secret/${AUTH_SECRET} -n "${NAMESPACE}" -o json 2>/dev/null \
      | jq -e '(.data.token | length > 0) and (.data["service-ca.crt"] | length > 0)' \
        >/dev/null 2>&1; then
    auth_ready=true
    break
  fi
  sleep 5
done
[[ "${auth_ready}" == "true" ]] \
  || fail "${AUTH_SECRET} did not receive token and service-ca.crt data"

echo "==> Creating in-cluster metrics client"
kubectl delete pod/${CURL_POD_NAME} -n "${NAMESPACE}" --ignore-not-found \
  >/dev/null 2>&1 || true
kubectl apply -f - <<EOF >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${CURL_POD_NAME}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/name: keda-epp-initial-state-validator
spec:
  serviceAccountName: ${AUTH_SERVICEACCOUNT}
  restartPolicy: Never
  containers:
    - name: curl
      image: curlimages/curl
      command: ["sleep", "3600"]
      volumeMounts:
        - name: thanos-auth
          mountPath: /var/run/keda-auth
          readOnly: true
  volumes:
    - name: thanos-auth
      secret:
        secretName: ${AUTH_SECRET}
        items:
          - key: token
            path: token
          - key: service-ca.crt
            path: service-ca.crt
EOF
kubectl wait pod/${CURL_POD_NAME} -n "${NAMESPACE}" \
  --for=condition=Ready --timeout=120s >/dev/null \
  || fail "in-cluster metrics client did not become Ready"

echo "==> Verifying direct EPP metric families"
DIRECT_METRICS="${ARTIFACT_DIR}/epp-metrics.txt"
kubectl exec -n "${NAMESPACE}" "${CURL_POD_NAME}" -- \
  curl --fail --silent --show-error --max-time 15 \
  "http://${EPP_SERVICE}:9090/metrics" > "${DIRECT_METRICS}" \
  || fail "could not query http://${EPP_SERVICE}:9090/metrics"

for metric in llm_d_epp_flow_control_queue_size llm_d_epp_request_running; do
  grep -Eq "^${metric}(\\{|[[:space:]])" "${DIRECT_METRICS}" \
    || fail "direct EPP endpoint does not expose a sample for ${metric}"
done
grep -E '^llm_d_epp_(flow_control_queue_size|request_running)(\{|[[:space:]])' \
  "${DIRECT_METRICS}" > "${ARTIFACT_DIR}/epp-metric-samples.txt"
echo "Direct EPP metric samples (values of zero are valid):"
sed -n '1,40p' "${ARTIFACT_DIR}/epp-metric-samples.txt"

thanos_query() {
  local query="$1" output="$2"
  # The variables below expand inside the validation pod, not in this shell.
  # shellcheck disable=SC2016
  kubectl exec -n "${NAMESPACE}" "${CURL_POD_NAME}" -- \
    sh -c '
      token="$(cat /var/run/keda-auth/token)"
      exec curl --fail --silent --show-error --max-time 30 \
        --cacert /var/run/keda-auth/service-ca.crt \
        -H "Authorization: Bearer ${token}" \
        --get "$1" --data-urlencode "query=$2"
    ' sh "${THANOS_URL}" "${query}" > "${output}" 2> "${output}.stderr"
}

validate_thanos_metric() {
  local metric="$1" raw_query="$2" aggregate_query="$3"
  local raw_file="${ARTIFACT_DIR}/thanos-${metric}-raw.json"
  local aggregate_file="${ARTIFACT_DIR}/thanos-${metric}-aggregate.json"
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS))
  local raw_count aggregate_count

  while (( SECONDS < deadline )); do
    if thanos_query "${raw_query}" "${raw_file}" && \
       thanos_query "${aggregate_query}" "${aggregate_file}" && \
       jq -e '.status == "success" and .data.resultType == "vector"' \
         "${raw_file}" >/dev/null 2>&1 && \
       jq -e '.status == "success" and .data.resultType == "vector"' \
         "${aggregate_file}" >/dev/null 2>&1; then
      raw_count="$(jq '.data.result | length' "${raw_file}")"
      aggregate_count="$(jq '.data.result | length' "${aggregate_file}")"

      if (( aggregate_count > 1 )); then
        fail "aggregate ${metric} query returned ${aggregate_count} results; expected exactly one"
      fi

      if (( raw_count > 0 && aggregate_count == 1 )); then
        jq -e --arg namespace "${NAMESPACE}" --arg service "${EPP_SERVICE}" \
          --arg model "${EXPECTED_MODEL_ID}" '
            all(.data.result[];
              .metric.namespace == $namespace and
              .metric.service == $service and
              .metric.model_name == $model and
              (.value | length == 2))
          ' "${raw_file}" >/dev/null \
          || fail "raw ${metric} series contain unexpected namespace, service, model, or value data"
        jq -e '
          .data.result[0].value as $value |
          ($value | length == 2) and
          ($value[0] | type == "number") and
          ($value[1] | type == "string") and
          ($value[1] | test("^-?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][-+]?[0-9]+)?$"))
        ' "${aggregate_file}" >/dev/null \
          || fail "aggregate ${metric} result has no numeric timestamp/value"

        echo "Thanos ${metric} raw series (${raw_count} source series):"
        jq -c '.data.result[] | {metric, value}' "${raw_file}"
        echo "Thanos ${metric} aggregate result:"
        jq -c '.data.result[0] | {metric, value}' "${aggregate_file}"
        return 0
      fi
    fi
    sleep "${POLL_INTERVAL_SECONDS}"
  done

  fail "Thanos did not return real raw and single aggregate ${metric} series within ${POLL_TIMEOUT_SECONDS}s"
}

echo "==> Verifying EPP metric series through authenticated Thanos"
QUEUE_SELECTOR="llm_d_epp_flow_control_queue_size{namespace=\"${NAMESPACE}\",service=\"${EPP_SERVICE}\",model_name=\"${EXPECTED_MODEL_ID}\"}"
QUEUE_QUERY="sum(${QUEUE_SELECTOR})"
RUNNING_SELECTOR="llm_d_epp_request_running{namespace=\"${NAMESPACE}\",service=\"${EPP_SERVICE}\",model_name=\"${EXPECTED_MODEL_ID}\"}"
RUNNING_QUERY="sum(${RUNNING_SELECTOR})"
validate_thanos_metric queue-size "${QUEUE_SELECTOR}" "${QUEUE_QUERY}"
validate_thanos_metric running-requests "${RUNNING_SELECTOR}" "${RUNNING_QUERY}"

echo "==> Verifying ScaledObject readiness and sustained non-fallback health"
kubectl wait scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" \
  --for=condition=Ready --timeout=300s >/dev/null \
  || fail "ScaledObject/${SCALEDOBJECT} did not become Ready"

fallback_streak=0
fallback_status=unknown
health_exposed=false
for _ in $(seq 1 20); do
  kubectl get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/scaledobject.json"
  fallback_status="$(jq -r '.status.conditions[]? | select(.type == "Fallback") | .status' \
    "${ARTIFACT_DIR}/scaledobject.json")"
  health_count="$(jq '(.status.health // {}) | length' "${ARTIFACT_DIR}/scaledobject.json")"
  health_ok=true
  if (( health_count > 0 )); then
    health_exposed=true
    if ! jq -e '
      all((.status.health // {}) | to_entries[];
        .value.status == "Happy" and ((.value.numberOfFailures // 0) == 0))
    ' "${ARTIFACT_DIR}/scaledobject.json" >/dev/null; then
      health_ok=false
    fi
    jq -c '.status.health' "${ARTIFACT_DIR}/scaledobject.json"
  fi

  if [[ "${fallback_status}" == "False" && "${health_ok}" == "true" ]]; then
    fallback_streak=$((fallback_streak + 1))
    echo "Fallback=False healthy sample ${fallback_streak}/${FALLBACK_REQUIRED_STREAK}"
    if (( fallback_streak >= FALLBACK_REQUIRED_STREAK )); then
      break
    fi
  else
    fallback_streak=0
    echo "Fallback/health not yet stable: Fallback=${fallback_status}, health_ok=${health_ok}"
  fi
  sleep "${FALLBACK_POLL_INTERVAL_SECONDS}"
done
(( fallback_streak >= FALLBACK_REQUIRED_STREAK )) \
  || fail "Fallback=False did not hold with healthy triggers for ${FALLBACK_REQUIRED_STREAK} samples"
if [[ "${health_exposed}" != "true" ]]; then
  echo "NOTE: this KEDA version did not expose per-trigger status.health; Fallback and HPA metrics remain mandatory"
fi

echo "==> Discovering and validating the generated HPA"
hpa_ready=false
for _ in $(seq 1 30); do
  if kubectl get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" -o json \
       > "${ARTIFACT_DIR}/scaledobject.json" 2>/dev/null && \
     kubectl get "hpa/${HPA_NAME}" -n "${NAMESPACE}" -o json \
       > "${ARTIFACT_DIR}/generated-hpa.json" 2>/dev/null; then
    hpa_ready=true
    break
  fi
  sleep "${POLL_INTERVAL_SECONDS}"
done
[[ "${hpa_ready}" == "true" ]] \
  || fail "expected HPA/${HPA_NAME} was not available within ${POLL_TIMEOUT_SECONDS}s"

reported_hpa_name="$(jq -r '.status.hpaName // empty' "${ARTIFACT_DIR}/scaledobject.json")"
[[ -z "${reported_hpa_name}" || "${reported_hpa_name}" == "${EXPECTED_HPA}" ]] \
  || fail "ScaledObject reported HPA ${reported_hpa_name}, expected ${EXPECTED_HPA}"

scaledobject_uid="$(jq -r '.metadata.uid // empty' "${ARTIFACT_DIR}/scaledobject.json")"
[[ -n "${scaledobject_uid}" ]] || fail "ScaledObject/${SCALEDOBJECT} has no metadata UID"
jq -e --arg uid "${scaledobject_uid}" --arg name "${SCALEDOBJECT}" '
  any(.metadata.ownerReferences[]?;
    .apiVersion == "keda.sh/v1alpha1" and
    .kind == "ScaledObject" and
    .name == $name and
    .uid == $uid and
    .controller == true)
' "${ARTIFACT_DIR}/generated-hpa.json" >/dev/null \
  || fail "HPA/${HPA_NAME} is not controlled by the current ScaledObject UID"
jq -e --arg deployment "${MODEL_DEPLOYMENT}" '
  .spec.scaleTargetRef.apiVersion == "apps/v1" and
  .spec.scaleTargetRef.kind == "Deployment" and
  .spec.scaleTargetRef.name == $deployment and
  .spec.minReplicas == 1 and
  .spec.maxReplicas == 2 and
  ([.spec.metrics[] | select(.type == "External")] | length == 2) and
  (all(.spec.metrics[]; .type == "External" and .external.target.type == "AverageValue")) and
  ([.spec.metrics[].external.target.averageValue] | sort == ["1", "16"])
' "${ARTIFACT_DIR}/generated-hpa.json" >/dev/null \
  || fail "HPA/${HPA_NAME} target, bounds, metric types, or AverageValue targets are incorrect"

metric_status_ready=false
deadline=$((SECONDS + POLL_TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  kubectl get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/scaledobject.json"
  kubectl get "hpa/${HPA_NAME}" -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/generated-hpa.json"

  scaledobject_metric_count="$(jq '(.status.externalMetricNames // []) | length' \
    "${ARTIFACT_DIR}/scaledobject.json")"
  scaledobject_metric_names="$(jq -r '.status.externalMetricNames[]?' \
    "${ARTIFACT_DIR}/scaledobject.json" | sort -u | paste -sd, -)"
  hpa_spec_metric_names="$(jq -r '.spec.metrics[]? | select(.type == "External") | .external.metric.name' \
    "${ARTIFACT_DIR}/generated-hpa.json" | sort -u | paste -sd, -)"
  hpa_current_metric_names="$(jq -r '.status.currentMetrics[]? | select(.type == "External") | .external.metric.name' \
    "${ARTIFACT_DIR}/generated-hpa.json" | sort -u | paste -sd, -)"

  optional_metric_names_ok=true
  if (( scaledobject_metric_count > 0 )) && \
     [[ "${scaledobject_metric_names}" != "${hpa_spec_metric_names}" ]]; then
    optional_metric_names_ok=false
  fi

  if [[ "${optional_metric_names_ok}" == "true" && \
        -n "${hpa_spec_metric_names}" && \
        "${hpa_spec_metric_names}" == "${hpa_current_metric_names}" ]] && \
     jq -e '
       [.spec.metrics[]? | select(.type == "External") | .external.metric.name] as $specNames |
       [.status.currentMetrics[]? | select(.type == "External")] as $currentMetrics |
       [$currentMetrics[] | .external.metric.name] as $currentNames |
       ($specNames | length == 2) and
       ($currentMetrics | length == 2) and
       ($specNames | unique | length == 2) and
       ($currentNames | unique | length == 2) and
       all($specNames[]; (type == "string") and (length > 0)) and
       all($currentNames[]; (type == "string") and (length > 0)) and
       all($currentMetrics[];
         (.external.current.averageValue | type == "string") and
         (.external.current.averageValue | test("^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+|[numkKMGTP]*i?)?$")))
     ' "${ARTIFACT_DIR}/generated-hpa.json" >/dev/null; then
    metric_status_ready=true
    break
  fi
  sleep "${POLL_INTERVAL_SECONDS}"
done
[[ "${metric_status_ready}" == "true" ]] \
  || fail "HPA did not populate both expected external current metrics within ${POLL_TIMEOUT_SECONDS}s"

echo "HPA external metrics: ${hpa_spec_metric_names}"
jq -c '.status.currentMetrics' "${ARTIFACT_DIR}/generated-hpa.json"

echo "==> Verifying stable one-replica initial state"
for sample in $(seq 1 "${STABLE_REQUIRED_SAMPLES}"); do
  kubectl get deployment/${MODEL_DEPLOYMENT} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/model-deployment.json"
  kubectl get "hpa/${HPA_NAME}" -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/generated-hpa.json"
  kubectl get scaledobject/${SCALEDOBJECT} -n "${NAMESPACE}" -o json \
    > "${ARTIFACT_DIR}/scaledobject.json"
  kubectl get pods -n "${NAMESPACE}" -l llm-d.ai/role=decode -o json \
    > "${ARTIFACT_DIR}/decode-pods.json"
  kubectl get replicasets -n "${NAMESPACE}" -l llm-d.ai/role=decode -o json \
    > "${ARTIFACT_DIR}/decode-replicasets.json"

  jq -e '
    .spec.replicas == 1 and
    .status.replicas == 1 and
    .status.updatedReplicas == 1 and
    .status.readyReplicas == 1 and
    .status.availableReplicas == 1
  ' "${ARTIFACT_DIR}/model-deployment.json" >/dev/null \
    || fail "model Deployment left the one-replica invariant during stable sample ${sample}"
  jq -e '
    .status.currentReplicas == 1 and
    .status.desiredReplicas == 1 and
    ([.status.currentMetrics[]? | select(.type == "External")] | length == 2)
  ' "${ARTIFACT_DIR}/generated-hpa.json" >/dev/null \
    || fail "HPA left desired/current replicas one or lost a metric during stable sample ${sample}"
  jq -e '[.items[] | select(.metadata.deletionTimestamp == null)] | length == 1' \
    "${ARTIFACT_DIR}/decode-pods.json" >/dev/null \
    || fail "decode pod count changed during stable sample ${sample}"
  jq -e '([.items[].spec.replicas] | add // 0) == 1 and ([.items[].status.replicas] | add // 0) == 1' \
    "${ARTIFACT_DIR}/decode-replicasets.json" >/dev/null \
    || fail "ReplicaSets contain more than one active model replica during stable sample ${sample}"
  jq -e '
    any(.status.conditions[]?; .type == "Fallback" and .status == "False")
  ' "${ARTIFACT_DIR}/scaledobject.json" >/dev/null \
    || fail "ScaledObject entered fallback during stable sample ${sample}"

  echo "Stable sample ${sample}/${STABLE_REQUIRED_SAMPLES}: Deployment=1 HPA=1 pods=1"
  if (( sample < STABLE_REQUIRED_SAMPLES )); then
    sleep "${STABLE_SAMPLE_INTERVAL_SECONDS}"
  fi
done

echo "Direct KEDA+EPP initial state is healthy: authenticated metrics, two HPA metrics, one model replica, no fallback."
