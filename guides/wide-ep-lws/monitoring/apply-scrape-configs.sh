#!/usr/bin/env bash
# Merge wide-ep-lws scrape configs into an existing prometheus-server configmap.
#
# Usage:
#   NAMESPACE=ecrncevi-dev ./apply-scrape-configs.sh
#   NAMESPACE=ecrncevi-dev KUBECONFIG=~/.kube/config.kermit ./apply-scrape-configs.sh
#
# Prerequisites:
#   - A prometheus-server configmap must already exist in NAMESPACE
#   - yq v4+ (https://github.com/mikefarah/yq)

set -euo pipefail

: "${NAMESPACE:?Set NAMESPACE to target namespace}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRAPE_FILE="${SCRIPT_DIR}/prometheus-scrape-configs.yaml"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

command -v yq >/dev/null 2>&1 || { echo "yq v4+ is required"; exit 1; }

echo "Extracting current prometheus.yml from configmap..."
kubectl get configmap prometheus-server -n "$NAMESPACE" \
  -o jsonpath='{.data.prometheus\.yml}' > "$TMPDIR/current.yml"

echo "Injecting wide-ep-lws scrape configs (namespace=${NAMESPACE})..."
envsubst < "$SCRAPE_FILE" > "$TMPDIR/extra.yml"

# Remove any existing jobs with the same names to avoid duplicates
for job in $(yq '.scrape_configs[].job_name' "$TMPDIR/extra.yml"); do
  yq -i "del(.scrape_configs[] | select(.job_name == \"${job}\"))" "$TMPDIR/current.yml"
done

# Append the new jobs
yq -i '.scrape_configs += load("'"$TMPDIR/extra.yml"'").scrape_configs' "$TMPDIR/current.yml"

echo "Applying updated configmap..."
kubectl create configmap prometheus-server -n "$NAMESPACE" \
  --from-file=prometheus.yml="$TMPDIR/current.yml" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Restarting prometheus-server..."
kubectl rollout restart deployment prometheus-server -n "$NAMESPACE"
kubectl rollout status deployment prometheus-server -n "$NAMESPACE" --timeout=60s

echo "Done. Scrape jobs added: vllm-decode, vllm-prefill, epp"
