#!/usr/bin/env bash
# Tests check-routing-sidecar-metrics.sh against small rendered manifests.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK="${SCRIPT_DIR}/check-routing-sidecar-metrics.sh"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/routing-sidecar-metrics-test.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

FAILURES=0

# expect <pass|fail> <description>, manifest on stdin
expect() {
  local want="$1" description="$2" got output
  cat > "${WORK_DIR}/rendered.yaml"
  if output=$("${CHECK}" "${WORK_DIR}/rendered.yaml" 2>&1); then got=pass; else got=fail; fi
  if [[ "${got}" == "${want}" ]]; then
    echo "ok   ${description}"
  else
    echo "FAIL ${description}: want ${want}, got ${got}"
    echo "${output}"
    FAILURES=$((FAILURES + 1))
  fi
}

# deployment <args...> -- <port names...>: a decode Deployment with a native
# routing-proxy sidecar carrying the given args and port names.
deployment() {
  local args=() ports=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do args+=("$1"); shift; done
  [[ $# -gt 0 ]] && shift
  ports=("$@")
  cat <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: decode
spec:
  template:
    spec:
      initContainers:
        - name: routing-proxy
          image: ghcr.io/llm-d/llm-d-router-disagg-sidecar:v1.0.0
          restartPolicy: Always
          args:
$(for a in "${args[@]}"; do echo "            - \"${a}\""; done)
          ports:
$(for p in "${ports[@]}"; do echo "            - {name: ${p}, containerPort: 1}"; done)
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest
EOF
}

deployment --port=8000 --metrics-port=9095 -- sidecar sc-metrics | expect pass "flag and port"
deployment --port=8000 --metrics-port 9095 -- sidecar sc-metrics | expect pass "flag with a separate value"
deployment --port=8000 -- sidecar sc-metrics | expect fail "no --metrics-port"
deployment --port=8000 --metrics-port=9095 -- sidecar | expect fail "no sc-metrics port"
deployment --port=8000 --metrics-port=9095 -- sidecar metrics | expect fail "metrics port under another name"

expect pass "no sidecar" <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prefill
spec:
  template:
    spec:
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest
---
apiVersion: v1
kind: Service
metadata:
  name: decode
spec:
  ports:
    - name: routing-proxy
      port: 8000
EOF

expect fail "sidecar image under another container name" <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: decode
spec:
  template:
    spec:
      containers:
        - name: proxy
          image: ghcr.io/llm-d/llm-d-router-disagg-sidecar:v1.0.0
          args: ["--port=8000"]
EOF

{ deployment --port=8000 --metrics-port=9095 -- sidecar sc-metrics; echo "---"; deployment --port=8000 -- sidecar; } \
  | expect fail "one bad sidecar among several documents"

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "${FAILURES} case(s) failed"
  exit 1
fi
echo "all cases passed"
