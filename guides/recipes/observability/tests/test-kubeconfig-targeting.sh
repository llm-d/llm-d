#!/usr/bin/env bash

set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY="$(cd "${TEST_DIR}/../../../.." && pwd)"
INSTALLER="${REPOSITORY}/guides/recipes/observability/install-prometheus-grafana.sh"
TLS_HELPER="${REPOSITORY}/guides/recipes/observability/generate-prometheus-tls-certs.sh"
STUB_DIR="${TEST_DIR}/bin"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/llm-d-kubeconfig-test.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

SELECTED_KUBECONFIG="${WORK_DIR}/selected kubeconfig"
AMBIENT_KUBECONFIG="${WORK_DIR}/ambient kubeconfig"
touch "$SELECTED_KUBECONFIG" "$AMBIENT_KUBECONFIG"

assert_contains() {
  local file="$1"
  local expected="$2"
  grep -F -- "$expected" "$file" >/dev/null || {
    printf 'FAIL: expected [%s] in %s\n' "$expected" "$file" >&2
    exit 1
  }
}

assert_all_selected() {
  local file="$1"
  local line
  local count=0
  while IFS= read -r line; do
    [[ "$line" == command=* ]] || continue
    count=$((count + 1))
    [[ "$line" == *"kubeconfig=[${SELECTED_KUBECONFIG}]"* ]] || {
      printf 'FAIL: wrong selected kubeconfig: %s\n' "$line" >&2
      exit 1
    }
  done < "$file"
  [[ "$count" -gt 0 ]] || { printf 'FAIL: no client command recorded\n' >&2; exit 1; }
}

run_installer() {
  local mode="$1"
  local log_file="$2"
  local mutation_file="$3"
  shift 3
  env \
    PATH="${STUB_DIR}:/usr/bin:/bin" \
    STUB_COMMAND=unused \
    STUB_MODE="$mode" \
    STUB_LOG="$log_file" \
    STUB_MUTATIONS="$mutation_file" \
    SELECTED_KUBECONFIG="$SELECTED_KUBECONFIG" \
    KUBECONFIG="$AMBIENT_KUBECONFIG" \
    bash "$INSTALLER" "$@"
}

# A selected target that fails reachability must stop before any mutation.
selected_failure_log="${WORK_DIR}/selected-failure.log"
selected_failure_mutations="${WORK_DIR}/selected-failure.mutations"
: > "$selected_failure_log"
: > "$selected_failure_mutations"
set +e
run_installer selected-fails "$selected_failure_log" "$selected_failure_mutations" \
  -g "$SELECTED_KUBECONFIG" -c >/dev/null 2>&1
status=$?
set -e
[[ "$status" -ne 0 ]] || { printf 'FAIL: selected reachability unexpectedly succeeded\n' >&2; exit 1; }
assert_contains "$selected_failure_log" "command=kubectl kubeconfig=[${SELECTED_KUBECONFIG}] args=[cluster-info]"
[[ ! -s "$selected_failure_mutations" ]] || { printf 'FAIL: mutation after reachability failure\n' >&2; exit 1; }
printf '%s\n' 'PASS selected target controls validation and prevents mutation.'

# CRD-only uses the selected file for both clients, including a path with spaces.
selected_log="${WORK_DIR}/selected.log"
selected_mutations="${WORK_DIR}/selected.mutations"
: > "$selected_log"
: > "$selected_mutations"
run_installer success "$selected_log" "$selected_mutations" -g "$SELECTED_KUBECONFIG" -c >/dev/null 2>&1
assert_all_selected "$selected_log"
assert_contains "$selected_log" "command=helm kubeconfig=[${SELECTED_KUBECONFIG}] args=[show][crds][prometheus-community/kube-prometheus-stack]"
assert_contains "$selected_log" "command=kubectl kubeconfig=[${SELECTED_KUBECONFIG}] args=[apply][--server-side][--validate=false][-f][-]"
printf '%s\n' 'PASS selected kubeconfig is inherited by helm and kubectl.'

# Without -g, an ambient KUBECONFIG remains effective.
ambient_log="${WORK_DIR}/ambient.log"
ambient_mutations="${WORK_DIR}/ambient.mutations"
: > "$ambient_log"
: > "$ambient_mutations"
run_installer success "$ambient_log" "$ambient_mutations" -c >/dev/null 2>&1
assert_contains "$ambient_log" "command=kubectl kubeconfig=[${AMBIENT_KUBECONFIG}] args=[cluster-info]"
assert_contains "$ambient_log" "command=helm kubeconfig=[${AMBIENT_KUBECONFIG}] args=[repo][list]"
printf '%s\n' 'PASS ambient kubeconfig is preserved without -g.'

# The TLS helper changes directory before Kubernetes calls; its selected target
# must still be inherited, and certificate paths may contain spaces.
tls_log="${WORK_DIR}/tls.log"
tls_mutations="${WORK_DIR}/tls.mutations"
openssl_log="${WORK_DIR}/openssl.log"
cert_dir="${WORK_DIR}/tls certs"
: > "$tls_log"
: > "$tls_mutations"
: > "$openssl_log"
env \
  PATH="${STUB_DIR}:/usr/bin:/bin" \
  STUB_MODE=tls \
  STUB_LOG="$tls_log" \
  STUB_MUTATIONS="$tls_mutations" \
  STUB_OPENSSL_LOG="$openssl_log" \
  KUBECONFIG="$AMBIENT_KUBECONFIG" \
  bash "$TLS_HELPER" -g "$SELECTED_KUBECONFIG" -d "$cert_dir" >/dev/null 2>&1
assert_all_selected "$tls_log"
assert_contains "$tls_log" "command=kubectl kubeconfig=[${SELECTED_KUBECONFIG}] args=[create][secret][generic][prometheus-web-tls]"
assert_contains "$tls_log" "command=kubectl kubeconfig=[${SELECTED_KUBECONFIG}] args=[create][configmap][prometheus-web-tls-ca]"
printf '%s\n' 'PASS TLS helper retains the selected target after changing directory.'

printf '%s\n' 'All kubeconfig-targeting tests passed.'
