#!/usr/bin/env bash
# Helm post-renderer: injects the UDS tokenizer sidecar and sets
# production-grade resources on the EPP container. Helm streams rendered
# manifests on stdin; we hand them to kustomize alongside the patches
# and stream the result back.
set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$SCRIPT_DIR/kustomization.yaml" \
   "$SCRIPT_DIR/patch-uds-sidecar.yaml" \
   "$SCRIPT_DIR/patch-epp-resources.yaml" \
   "$TMP/"
cat > "$TMP/rendered.yaml"

kustomize build "$TMP"
