#!/usr/bin/env bash
# Helm post-renderer: adds the UDS tokenizer sidecar to the standalone
# chart's scheduler Deployment. Helm streams rendered manifests on stdin;
# we hand them to kustomize alongside the patch and stream the result back.
set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$SCRIPT_DIR/kustomization.yaml" "$SCRIPT_DIR/patch-uds-sidecar.yaml" "$TMP/"
cat > "$TMP/rendered.yaml"

kustomize build "$TMP"
