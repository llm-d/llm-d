#!/usr/bin/env bash
# Deploy the WVA + optimized-baseline stack on the AMD ROCm CI cluster in a single namespace.
# Same code path for CI nightly runs and local development.
#
# The cluster is vanilla Kubernetes with a kube-prometheus-stack already installed, so
# this takes the k8s platform path: the KEDA trigger queries Prometheus directly. Whether
# that endpoint is HTTPS or plain HTTP is discovered rather than assumed, since the guide's
# TLS settings make WVA refuse to start against a stack installed without --enable-tls.
#
# Unlike the CKS nightly this deliberately sets no PriorityClass. The ROCm cluster
# is shared with four other nightlies; a preempting nightly would evict their pods.
# It also caps the model server at 2 replicas (TP2, so 4 GPUs) to stay inside the
# GPU budget the nightly reserves.
#
# Environment variables:
#   NAMESPACE             target namespace for ALL resources (set by the nightly workflow)
#   WVA_TAG               WVA controller image tag override (default: unset = upstream default)
#   OUTPUT_DIR            where to write the generated overlay (default: mktemp -d)
#   MONITORING_NAMESPACE  namespace running Prometheus (default: discovered)
#   PROMETHEUS_ADDRESS    Prometheus endpoint KEDA queries (default: discovered)
#   ROUTER_CHART_VERSION  EPP router chart version (default: set by guides/env.sh)

set -euo pipefail

if command -v grealpath &>/dev/null; then
  _realpath=grealpath          # macOS: brew install coreutils
elif realpath --version &>/dev/null 2>&1; then
  _realpath=realpath           # Linux GNU coreutils
else
  echo "ERROR: GNU realpath not found. On macOS install it with: brew install coreutils" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${REPO_ROOT}/guides/env.sh"

NAMESPACE="${NAMESPACE:-llm-d-optimized-baseline}"
SCALEDOBJECT=optimized-baseline-rocm-vllm-decode-scaler
# Short hash used as a suffix on ClusterRoleBindings to make them unique per namespace.
NS_HASH="$(printf '%s' "${NAMESPACE}" | sha256sum | cut -c1-8)"
WVA_TAG="${WVA_TAG:-}"
OUTPUT_DIR="${OUTPUT_DIR:-$(mktemp -d -t nightly-deploy-amd.XXXXXX)}"
# Prometheus sits wherever the cluster's monitoring stack was installed: the ROCm CI
# cluster runs kube-prometheus-stack in monitoring, other clusters use llm-d-monitoring.
# Read it off the Prometheus CR instead of pinning one name.
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-$(kubectl get prometheus -A \
  -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || true)}"
MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-llm-d-monitoring}"

# The guide assumes Prometheus serves HTTPS, which holds only when the stack was installed
# with `install-prometheus-grafana.sh --enable-tls`. Installed without it, Prometheus serves
# plain HTTP and WVA refuses to start until every TLS setting is stripped. prometheus-web-tls
# is the Secret the TLS install creates, so its presence is the signal.
if [[ -n "${PROMETHEUS_ADDRESS:-}" ]]; then
  PROMETHEUS_SCHEME="${PROMETHEUS_ADDRESS%%://*}"
else
  if ! kubectl get service prometheus-operated -n "${MONITORING_NAMESPACE}" >/dev/null 2>&1; then
    echo "ERROR: no prometheus-operated Service in ${MONITORING_NAMESPACE}, so WVA and KEDA have" >&2
    echo "       nothing to query. Set MONITORING_NAMESPACE if the monitoring stack lives" >&2
    echo "       elsewhere, or PROMETHEUS_ADDRESS to address it directly." >&2
    exit 1
  fi
  if kubectl get secret prometheus-web-tls -n "${MONITORING_NAMESPACE}" >/dev/null 2>&1; then
    PROMETHEUS_SCHEME=https
  else
    PROMETHEUS_SCHEME=http
  fi
  PROMETHEUS_ADDRESS="${PROMETHEUS_SCHEME}://prometheus-operated.${MONITORING_NAMESPACE}.svc.cluster.local:9090"
fi

mkdir -p "${OUTPUT_DIR}"

echo "Generating overlay in ${OUTPUT_DIR}"
echo "  NAMESPACE:  ${NAMESPACE}"
echo "  PROMETHEUS: ${PROMETHEUS_ADDRESS}"
[[ -n "${WVA_TAG}" ]] && echo "  WVA_TAG:   ${WVA_TAG}"

echo "==> Ensuring namespace ${NAMESPACE} exists"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# Nightly-only model server tweak: the guide ships 8 replicas, the nightly runs 2.
# The AMD base already mounts writable Triton and torch-compile caches, so unlike
# the CKS nightly nothing else needs patching in.
echo "==> Deploying the AMD model server"
yq '.spec.replicas=2' -i "${REPO_ROOT}/guides/optimized-baseline/modelserver/amd/vllm/base/patch-vllm.yaml"
# The base ships no PodMonitor, so nothing scrapes vLLM and WVA's saturation engine finds
# neither kv-cache utilization nor queue depth — it then emits a desired count equal to the
# current one and the nightly passes without autoscaling ever being exercised. Layer the
# monitoring component on so those metrics reach Prometheus.
MODELSERVER_DIR="${OUTPUT_DIR}/modelserver"
mkdir -p "${MODELSERVER_DIR}"
MODELSERVER_REL="$("${_realpath}" --relative-to="${MODELSERVER_DIR}" "${REPO_ROOT}")"
cat > "${MODELSERVER_DIR}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ${MODELSERVER_REL}/guides/optimized-baseline/modelserver/amd/vllm/base
components:
  - ${MODELSERVER_REL}/guides/recipes/modelserver/components/monitoring
EOF
kubectl apply -k "${MODELSERVER_DIR}" -n "${NAMESPACE}"

# monitoring.values.yaml adds the EPP ServiceMonitor. The WVA guide's prerequisites call for
# it (see wva/README.md, which points at the optimized-baseline guide's monitoring step) and
# without it the inference_pool_* series never reach Prometheus.
echo "==> Installing EPP router via Helm"
helm install workload-variant-autoscaler-inferencepool-standalone \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${REPO_ROOT}/guides/optimized-baseline/router/optimized-baseline.values.yaml" \
  -f "${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

# kustomize rejects absolute resource paths, so reference the repo relative to OUTPUT_DIR.
REL="$("${_realpath}" --relative-to="${OUTPUT_DIR}" "${REPO_ROOT}")"

# platform/k8s pins namespace llm-d-optimized-baseline; wrap it so everything lands in NAMESPACE.
# The amd component renames the ScaledObject to match the AMD model server's namePrefix, so the
# patches below target it by its post-component name.
cat > "${OUTPUT_DIR}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ${NAMESPACE}
# Tags the cluster-scoped RBAC too, which deleting the namespace would otherwise
# strand: the nightly's teardown deletes ClusterRoleBindings carrying this label.
labels:
  - pairs:
      llm-d.ai/nightly-namespace: ${NAMESPACE}
    includeSelectors: false
resources:
  - ${REL}/guides/workload-autoscaling/wva/controller/platform/k8s/
  - ${REL}/guides/workload-autoscaling/wva/optimized-baseline/keda/k8s/
components:
  - ${REL}/guides/workload-autoscaling/wva/optimized-baseline/keda/components/amd/
patches:
  # The namespace and Prometheus endpoint live inside KEDA trigger strings, so the kustomize
  # namespace transformer cannot reach them — rewrite them explicitly. maxReplicaCount is capped
  # at the GPU budget the nightly reserves (2 replicas at TP2), not the guide's default of 10.
  # NB: variant_name is the ScaledObject's name (-scaler suffix), not the Deployment's.
  - patch: |-
      - op: replace
        path: /spec/triggers/0/metadata/query
        value: |
          wva_desired_replicas{
            variant_name="${SCALEDOBJECT}",
            namespace="${NAMESPACE}"
          }
      - op: replace
        path: /spec/triggers/0/metadata/serverAddress
        value: ${PROMETHEUS_ADDRESS}
      - op: replace
        path: /spec/maxReplicaCount
        value: 2
      # The deployment starts at 2 replicas while WVA, seeing no traffic yet, asks for 1. KEDA
      # would scale down within the 60s guide default — mid-startup, while the workflow is still
      # waiting on the pods it listed before the scale-down, which then fails on a NotFound.
      # Hold scale-down off until the stack is up and the benchmark is driving load.
      # TODO: interim. The real fix is to start the deployment at 1 replica (the floor WVA asks
      # for when idle) and let the benchmark drive scale-up, rather than pinning 2 and delaying
      # the scale-down that follows.
      - op: replace
        path: /spec/advanced/horizontalPodAutoscalerConfig/behavior/scaleDown/stabilizationWindowSeconds
        value: 900
    target:
      kind: ScaledObject
      name: ${SCALEDOBJECT}
EOF

if [[ "${PROMETHEUS_SCHEME}" == "http" ]]; then
  # KEDA rejects a trigger that carries a TLS-only setting alongside an http:// address.
  cat >> "${OUTPUT_DIR}/kustomization.yaml" <<EOF
  - patch: |-
      - op: remove
        path: /spec/triggers/0/metadata/unsafeSsl
    target:
      kind: ScaledObject
      name: ${SCALEDOBJECT}
  - path: controller-http-patch.yaml
    target:
      kind: Deployment
      name: wva-controller-manager
EOF

  # Strip the HTTPS wiring platform/k8s adds. Each of these is a separate startup failure:
  # without PROMETHEUS_ALLOW_HTTP the controller rejects the URL outright, and with a token
  # path still set it refuses to send a bearer token in the clear. PROMETHEUS_TOKEN_PATH has
  # to be empty rather than absent — absent means "use the ServiceAccount token", which fails
  # the same way. The prom-ca volume goes too, since nothing signs a plain-HTTP endpoint.
  cat > "${OUTPUT_DIR}/controller-http-patch.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: wva-controller-manager
spec:
  template:
    spec:
      containers:
        - name: manager
          env:
            - name: PROMETHEUS_BASE_URL
              value: ${PROMETHEUS_ADDRESS}
            - name: PROMETHEUS_ALLOW_HTTP
              value: "true"
            - name: PROMETHEUS_TOKEN_PATH
              value: ""
            - name: PROMETHEUS_CA_CERT
              \$patch: delete
          volumeMounts:
            - mountPath: /etc/prometheus/tls
              \$patch: delete
      volumes:
        - name: prom-ca
          \$patch: delete
EOF
fi

# ClusterRoleBindings are cluster-scoped; append a namespace hash so this nightly cannot
# collide with another WVA deployment on the shared cluster. These are the four the
# namespace-scoped/kubernetes overlay renders — `kubectl kustomize` it after a version bump,
# since a patch target that matches nothing is silently dropped.
for crb in \
  wva-manager-rolebinding \
  wva-metrics-auth-rolebinding \
  wva-metrics-reader-rolebinding \
  wva-epp-metrics-reader-role-binding; do
  cat >> "${OUTPUT_DIR}/kustomization.yaml" <<EOF
  - patch: |-
      - op: replace
        path: /metadata/name
        value: ${crb}-${NS_HASH}
    target:
      kind: ClusterRoleBinding
      name: ${crb}
EOF
done

if [[ -n "${WVA_TAG}" ]]; then
  # The upstream base kustomization already rewrites image name "controller" to
  # ghcr.io/llm-d/llm-d-workload-variant-autoscaler. Match the rewritten name here.
  cat >> "${OUTPUT_DIR}/kustomization.yaml" <<EOF
images:
  - name: ghcr.io/llm-d/llm-d-workload-variant-autoscaler
    newTag: ${WVA_TAG}
EOF
fi

echo "==> Rendering the overlay"
RENDERED="${OUTPUT_DIR}/rendered.yaml"
kubectl kustomize "${OUTPUT_DIR}" > "${RENDERED}"

# KEDA is the external metrics provider (Prometheus Adapter was retired upstream in
# llm-d-workload-variant-autoscaler#1399). WVA only registers its ScaledObject reconciler if
# the KEDA CRD exists when the controller starts, so check before deploying the controller.
echo "==> Checking for KEDA"
if ! kubectl get crd scaledobjects.keda.sh >/dev/null 2>&1; then
  echo "ERROR: CRD scaledobjects.keda.sh not found — install KEDA on the cluster before WVA." >&2
  exit 1
fi

if [[ "${PROMETHEUS_SCHEME}" == "https" ]]; then
  # The controller mounts prometheus-tls-cert to verify Prometheus over HTTPS. The volume is
  # not optional, so without this Secret the controller pod never leaves ContainerCreating.
  # Same extraction the guide documents (wva/README.md, "Extract the Prometheus CA certificate").
  echo "==> Creating the prometheus-tls-cert Secret in ${NAMESPACE}"
  if ! kubectl get secret prometheus-web-tls -n "${MONITORING_NAMESPACE}" >/dev/null 2>&1; then
    echo "ERROR: PROMETHEUS_ADDRESS is https but Secret prometheus-web-tls is missing from" >&2
    echo "       ${MONITORING_NAMESPACE}, so there is no CA to verify Prometheus against." >&2
    echo "       Install the monitoring stack with TLS enabled:" >&2
    echo "       guides/recipes/observability/install-prometheus-grafana.sh --enable-tls" >&2
    exit 1
  fi
  PROMETHEUS_CA_CERT="$(kubectl get secret prometheus-web-tls -n "${MONITORING_NAMESPACE}" \
    -o jsonpath='{.data.tls\.crt}' | base64 -d)"
  kubectl create secret generic prometheus-tls-cert \
    --from-literal=ca.crt="${PROMETHEUS_CA_CERT}" \
    --dry-run=client -o yaml | kubectl apply -f - -n "${NAMESPACE}"
else
  # The controller reads its config file as well as its environment, and rejects any
  # TLS-related setting once the endpoint is plain HTTP. The setting lives inside a
  # ConfigMap value, which no kustomize patch can reach, so edit the rendered output.
  echo "==> Rewriting wva-manager-config for plain-HTTP Prometheus"
  # shellcheck disable=SC2016  # ${1} and strenv() are yq syntax, not shell expansions
  PROMETHEUS_ADDRESS="${PROMETHEUS_ADDRESS}" yq -i '
    (select(.kind == "ConfigMap" and .metadata.name == "wva-manager-config").data["config.yaml"]) |=
      (sub("(?m)^[ \t]*PROMETHEUS_TLS_INSECURE_SKIP_VERIFY:.*\n", "") |
       sub("(?m)^([ \t]*PROMETHEUS_BASE_URL:).*$", "${1} \"" + strenv(PROMETHEUS_ADDRESS) + "\""))
  ' "${RENDERED}"
fi

echo "==> Applying WVA + autoscaling assets"
kubectl apply -f "${RENDERED}"

echo "==> Waiting for WVA controller to become Available"
kubectl wait deployment/wva-controller-manager \
  -n "${NAMESPACE}" --for=condition=Available --timeout=300s

echo "==> Waiting for the ScaledObject to be Ready"
# Ready only means KEDA accepted the trigger and created its HPA — not that the metric works.
kubectl wait scaledobject/"${SCALEDOBJECT}" \
  -n "${NAMESPACE}" --for=condition=Ready --timeout=300s

# If KEDA cannot query Prometheus it suppresses the error and serves `fallback: replicas`, so the
# stack comes up healthy and the ScaledObject still reports Ready=True/Active=True. Fallback=False
# is the only signal that the replica count actually comes from WVA.
# Require Fallback=False to HOLD: it reads False before KEDA's first poll, and WVA needs a
# scrape cycle to publish the metric, so early readings are meaningless in both directions.
echo "==> Verifying KEDA is scaling on the real metric (not fallback)"
streak=0
for _ in $(seq 1 30); do
  fallback="$(kubectl get scaledobject/"${SCALEDOBJECT}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.conditions[?(@.type=="Fallback")].status}' 2>/dev/null || true)"
  if [[ "${fallback}" == "False" ]]; then
    streak=$((streak + 1))
    [[ "${streak}" -ge 3 ]] && break
  else
    streak=0
  fi
  sleep 10
done

if [[ "${streak}" -lt 3 ]]; then
  echo "ERROR: ScaledObject is in fallback (Fallback=${fallback:-unknown}) — KEDA is NOT reading" >&2
  echo "       wva_desired_replicas. Replica count is coming from spec.fallback, not from WVA." >&2
  kubectl get scaledobject/"${SCALEDOBJECT}" -n "${NAMESPACE}" \
    -o jsonpath='{range .status.conditions[*]}  {.type}={.status} ({.reason}: {.message}){"\n"}{end}' >&2
  echo "--- KEDA operator errors for this ScaledObject ---" >&2
  kubectl logs -A -l app=keda-operator --tail=200 2>/dev/null \
    | grep -i "${NAMESPACE}" | tail -10 >&2 || true
  exit 1
fi

echo "==> Listing autoscaling resources"
kubectl get scaledobject,hpa -n "${NAMESPACE}"
