#!/usr/bin/env bash
# Wait for all pods matching a label selector to be Running and Ready.
# Usage: bash wait-for-pods-ready.sh -l <label-selector> -n <namespace> [-t <timeout-seconds>]

set -euo pipefail

LABEL=""
NAMESPACE=""
TIMEOUT=600
INTERVAL=5

while [[ $# -gt 0 ]]; do
  case "$1" in
    -l) LABEL="$2"; shift 2 ;;
    -n) NAMESPACE="$2"; shift 2 ;;
    -t) TIMEOUT="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "${LABEL}" || -z "${NAMESPACE}" ]]; then
  echo "Usage: $0 -l <label-selector> -n <namespace> [-t <timeout-seconds>]"
  exit 1
fi

elapsed=0
while true; do
  echo "--- Checking pods (elapsed: ${elapsed}s, timeout: ${TIMEOUT}s) ---"
  kubectl get pods -l "${LABEL}" -n "${NAMESPACE}"

  total=$(kubectl get pods -l "${LABEL}" -n "${NAMESPACE}" --no-headers 2>/dev/null | wc -l | tr -d '[:space:]')
  ready=$(kubectl get pods -l "${LABEL}" -n "${NAMESPACE}" -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' 2>/dev/null | grep -c "True" || true)
  ready=$(echo "${ready}" | tr -d '[:space:]')

  echo "----> Ready: ${ready}/${total}"

  if [[ "${total}" -gt 0 && "${ready}" -eq "${total}" ]]; then
    echo "All pods are ready!"
    exit 0
  fi

  if [[ "${elapsed}" -ge "${TIMEOUT}" ]]; then
    echo "ERROR: Timed out waiting for pods to be ready."
    exit 1
  fi

  sleep "${INTERVAL}"
  elapsed=$((elapsed + INTERVAL))
done
