#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
OVERLAY="${REPO_ROOT}/guides/wide-ep-lws/modelserver/gpu/vllm-glm-5.2/deployments/benchmark/baseline/p1w1d1w1"
MANIFEST="$(mktemp)"
trap 'rm -f "${MANIFEST}"' EXIT

kustomize build "${OVERLAY}" >"${MANIFEST}"

# The no-mtp component must modify ENABLE_MTP in place.  A duplicate env name
# is rejected by the LeaderWorkerSet CRD during server-side validation.
yq -o=json '.' "${MANIFEST}" | jq -s -e '
  [ .[]
    | select(.kind == "LeaderWorkerSet")
    | .spec.leaderWorkerTemplate.workerTemplate.spec.containers[0].env
    | map(select(.name == "ENABLE_MTP"))
    | select(length != 1 or .[0].value != "0")
  ] | length == 0
' >/dev/null
