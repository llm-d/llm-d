#!/usr/bin/env bash
# Deploy and verify the SGLang variant, including its dedicated render pool.
set -euo pipefail

: "${NAMESPACE:?NAMESPACE must be exported by the nightly workflow}"
REPO_ROOT=$(git rev-parse --show-toplevel)
GUIDE_DIR="${REPO_ROOT}/guides/precise-prefix-cache-routing"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/precise-prefix-cache-routing-sglang-ci}"
mkdir -p "${OUTPUT_DIR}"
python3 -c 'import yaml' 2>/dev/null \
  || PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --user --quiet 'pyyaml==6.*'

# CI-only: allow the runner's port-forward to read the EPP's /metrics.
# This does not create Prometheus Operator resources or change the user guide.
cat > "${OUTPUT_DIR}/metrics.values.yaml" <<'EOF'
router:
  monitoring:
    prometheus:
      auth:
        enabled: false
EOF

python3 "${REPO_ROOT}/scripts/guide.py" emit "${GUIDE_DIR}" --context ci \
  --var NAMESPACE="${NAMESPACE}" --var MODEL_SERVER=sglang \
  --var ACCELERATOR_TYPE=gpu --var INFRA_PROVIDER=gke \
  --var MONITORING_VALUES="-f ${OUTPUT_DIR}/metrics.values.yaml" \
  env prerequisites.gaie deploy.router_values deploy.standalone \
  deploy.modelserver deploy.render > "${OUTPUT_DIR}/deploy.sh"
bash -euo pipefail "${OUTPUT_DIR}/deploy.sh"

for deployment in precise-prefix-cache-routing-epp \
  precise-prefix-cache-routing-gpu-sglang-decode precise-prefix-cache-routing-render; do
  kubectl rollout status -n "${NAMESPACE}" "deployment/${deployment}" --timeout=30m
done
python3 "${GUIDE_DIR}/verify-sglang.py" --namespace "${NAMESPACE}"
