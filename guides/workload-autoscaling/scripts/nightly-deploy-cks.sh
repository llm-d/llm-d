#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${REPO_ROOT}/guides/env.sh"

# WVA kustomize builds target namespace llm-d-autoscaler (do not pass a conflicting kubectl -n).
WVA_DEPLOY_NS=llm-d-autoscaler
yq '.spec.template.spec.priorityClassName="nightly-gpu-critical"' -i guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
yq '.spec.template.spec.volumes += {"name": "triton-cache", "emptyDir": {}}' -i guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
yq '.spec.template.spec.containers[0].volumeMounts += {"mountPath": "/.triton", "name": "triton-cache"}' -i guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
yq '.spec.replicas=2' -i guides/optimized-baseline/modelserver/gpu/vllm/base/patch-vllm.yaml
kubectl apply -k guides/optimized-baseline/modelserver/gpu/vllm/base -n ${NAMESPACE}
helm install workload-variant-autoscaler-inferencepool-standalone \
  ${ROUTER_STANDALONE_CHART} \
  -f guides/recipes/router/base.values.yaml \
  -f guides/optimized-baseline/router/optimized-baseline.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}

# Override WVA controller image to the nightly build from this run
if [ -n "$WVA_TAG" ]; then
  export WVA_TAG
  yq -i '.images = [{"name": "controller", "newName": "ghcr.io/llm-d/llm-d-workload-variant-autoscaler", "newTag": strenv(WVA_TAG)}]' \
    guides/workload-autoscaling/wva-config/base/kustomization.yaml
fi

# Kustomization validation dry run
kubectl kustomize guides/workload-autoscaling/wva-config/platform/cks >/dev/null

# apply WVA assets
kubectl apply -k guides/workload-autoscaling/wva-config/platform/cks

# Validate WVA controller is ready, then apply autoscaling resources
kubectl wait deployment/workload-variant-autoscaler-controller-manager \
  -n "${WVA_DEPLOY_NS}" --for=condition=Available --timeout=300s
kubectl apply -k guides/workload-autoscaling/optimized-baseline-autoscaling/hpa -n "${NAMESPACE}"
kubectl get variantautoscaling,hpa -n "${NAMESPACE}"
