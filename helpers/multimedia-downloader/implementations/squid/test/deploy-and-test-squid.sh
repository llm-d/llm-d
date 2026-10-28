#!/usr/bin/env bash
# deploy-and-test-squid.sh — deploys and tests the Squid HTTP or SSL Bump implementation.
# Usage details available via --help

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HTTP_DIR="${SCRIPT_DIR}/../http"
readonly SSL_BUMP_DIR="${SCRIPT_DIR}/../https-ssl-bump"
readonly DOCKER_DIR="${SCRIPT_DIR}/../docker"
readonly SERVICE_YAML="${SCRIPT_DIR}/../../../service.yaml"
readonly LOCAL_IMAGE="squid-ssl-bump:local"
readonly REMOTE_TAG="dev"
readonly CURL_IMAGE="curlimages/curl:8.11.1"
readonly HTTP_TEST_URL="http://images.cocodataset.org/val2017/000000039769.jpg"
readonly HTTP_PROXY="http://multimedia-downloader:80"
readonly SSL_TEST_URL="https://images.dog.ceo/breeds/poodle-standard/n02113799_2280.jpg"
readonly SSL_CA_ORG="Squid Test CA"

# Default configurations
MODE="http"
SKIP_CLEANUP=false
USE_OPENSHIFT=false
KUBE_CONTEXT=""
NAMESPACE="default"
REGISTRY="${SQUID_IMAGE_REGISTRY:-}" # Reads from ENV var if set
BUILD_PUSH=false
KUSTOMIZE_TMP=""

# --- Logging Helpers -----------------------------------------------------------
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
section() { echo -e "\n${YELLOW}==> $*${NC}"; }
success() { echo -e "${GREEN}✓ $*${NC}"; }
error()   { echo -e "${RED}✗ $*${NC}"; }

# --- Argument Parsing ----------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)             MODE="$2"; shift 2 ;;
        --skip-cleanup)     SKIP_CLEANUP=true; shift ;;
        --openshift)        USE_OPENSHIFT=true; shift ;;
        --registry)         REGISTRY="$2"; shift 2 ;;
        --build-push)       BUILD_PUSH=true; MODE="ssl-bump"; shift ;;
        --context)          KUBE_CONTEXT="$2"; USE_OPENSHIFT=true; shift 2 ;;
        --help|-h)
            sed -n '2,/^# Requirements:/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) error "Unknown argument: $1"; exit 1 ;;
    esac
done

# --- Validation & Dynamic Variables --------------------------------------------
case "${MODE}" in
    http)     
        readonly CLUSTER_NAME="squid-smoke"
        readonly TEST_POD="curl-test-pod"
        readonly IMPL_DIR="${HTTP_DIR}" 
        ;;
    ssl-bump) 
        readonly CLUSTER_NAME="squid-ssl-bump-smoke"
        readonly TEST_POD="curl-ssl-test-pod"
        readonly IMPL_DIR="${SSL_BUMP_DIR}" 
        ;;
    *) error "Invalid --mode '${MODE}'. Must be 'http' or 'ssl-bump'."; exit 1 ;;
esac

# Validate dependencies (using short-circuit evaluation for clean code)
[[ "${MODE}" == "ssl-bump" && ${USE_OPENSHIFT} == true && -z "${REGISTRY}" ]] && { error "--mode ssl-bump --openshift requires a registry. Use --registry or export SQUID_IMAGE_REGISTRY"; exit 1; }
[[ ${BUILD_PUSH} == true && -z "${REGISTRY}" ]] && { error "--build-push requires a registry. Use --registry or export SQUID_IMAGE_REGISTRY"; exit 1; }

# --- Core Functions ------------------------------------------------------------

cleanup() {
    section "Cleaning up"

    if [[ ${SKIP_CLEANUP} == true ]]; then
        echo "  Resources left in place for inspection (--skip-cleanup)."
    else
        kubectl delete pod "${TEST_POD}" --ignore-not-found --wait=false 2>/dev/null || true

        if [[ ${USE_OPENSHIFT} == true ]]; then
            kubectl delete -f "${SERVICE_YAML}" --ignore-not-found 2>/dev/null || true
            kubectl delete -k "${IMPL_DIR}/overlays/openshift" --ignore-not-found 2>/dev/null || true
            echo "  OpenShift multimedia-downloader resources deleted."
        else
            kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true
            echo "  Cluster '${CLUSTER_NAME}' deleted."
        fi
    fi

    if [[ -d "${SCRIPT_DIR}/squid-ssl-certs" ]]; then
        rm -rf "${SCRIPT_DIR}/squid-ssl-certs"
        echo "  Generated certificates cleaned up."
    fi
    [[ -n "${KUSTOMIZE_TMP}" && -d "${KUSTOMIZE_TMP}" ]] && rm -rf "${KUSTOMIZE_TMP}"
}
trap cleanup EXIT

build_and_push_image() {
    section "Building Squid SSL Bump image"
    docker build -t "${LOCAL_IMAGE}" -f "${DOCKER_DIR}/Dockerfile.squid-ssl-bump" "${DOCKER_DIR}"
    success "Image built: ${LOCAL_IMAGE}"

    if [[ ${BUILD_PUSH} == true && -n "${REGISTRY}" ]]; then
        local remote_image="${REGISTRY}/squid-ssl-bump:${REMOTE_TAG}"
        section "Pushing image to registry"
        docker tag "${LOCAL_IMAGE}" "${remote_image}"
        docker push "${remote_image}"
        success "Image pushed: ${remote_image}"
    fi
}

setup_cluster() {
    if [[ -n "${KUBE_CONTEXT}" ]]; then
        kubectl config use-context "${KUBE_CONTEXT}"
        echo "  Switched to kubectl context: ${KUBE_CONTEXT}"
    fi

    if [[ ${USE_OPENSHIFT} == true ]]; then
        # Use the current context's namespace. The command succeeds with empty
        # output (not a non-zero exit) when no namespace is set, so the `||`
        # fallback never fires; default explicitly on an empty result instead.
        NAMESPACE="$(kubectl config view --minify --output 'jsonpath={..namespace}' 2>/dev/null)"
        NAMESPACE="${NAMESPACE:-default}"
        echo "  Using current namespace: ${NAMESPACE}"
        kubectl config set-context --current --namespace="${NAMESPACE}"
    else
        section "Setting up kind cluster '${CLUSTER_NAME}'"
        if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
            kind create cluster --name "${CLUSTER_NAME}" --wait 90s
            echo "  Cluster created."
        fi

        if [[ "${MODE}" == "ssl-bump" ]]; then
            section "Loading image into kind cluster"
            kind load docker-image "${LOCAL_IMAGE}" --name "${CLUSTER_NAME}"
            success "Image loaded into cluster"
        fi
    fi
}

deploy_squid() {
    local kustomize_dir="${IMPL_DIR}/overlays/kind"

    if [[ ${USE_OPENSHIFT} == true ]]; then
        kustomize_dir="${IMPL_DIR}/overlays/openshift"
        
        if [[ "${MODE}" == "ssl-bump" && -n "${REGISTRY}" ]]; then
            KUSTOMIZE_TMP="$(mktemp -d "${IMPL_DIR}/overlays/.tmp-kustomize-XXXXXX")"
            cat > "${KUSTOMIZE_TMP}/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- ../openshift
images:
- name: ubuntu/squid
  newName: ${REGISTRY}/squid-ssl-bump
  newTag: ${REMOTE_TAG}
EOF
            kustomize_dir="${KUSTOMIZE_TMP}"
        fi
    fi

    section "Deploying squid (mode: ${MODE})"
    kubectl apply -k "${kustomize_dir}"
    
    kubectl apply -f "${SERVICE_YAML}"
    kubectl rollout status deployment/multimedia-downloader --timeout=120s
    success "multimedia-downloader is ready."
}

# assert_squid_status <expected_pattern> <url> [curl_args...]
# Sends one curl request and verifies the squid log contains expected_pattern for that URL.
assert_squid_status() {
    local expected="$1" url="$2"; shift 2

    echo "  Requesting ${url} (expect ${expected})..."
    if ! kubectl exec "${TEST_POD}" -- curl -sf -o /dev/null "$@" "${url}"; then
        error "curl request failed: ${url}"
        return 1
    fi
    sleep 3

    local log_line
    log_line=$(kubectl logs -l app=multimedia-downloader -c squid --tail=20 \
               | grep -F "${url}" | tail -1 || true)
    if ! echo "${log_line}" | grep -qE "${expected}"; then
        error "Expected '${expected}' in squid log but got: ${log_line:-<no matching entry>}"
        return 1
    fi
    success "${expected} confirmed for ${url}"
}

# run_cache_test <url> [curl_args...]
# Sends a miss request then a hit request, asserting cache status for each.
run_cache_test() {
    local url="$1"; shift
    assert_squid_status "TCP_MISS"  "${url}" "$@"
    assert_squid_status "TCP_.*HIT" "${url}" "$@"
}

run_http_test() {
    section "Testing HTTP Cache"
    kubectl run "${TEST_POD}" --image="${CURL_IMAGE}" --restart=Never -- sleep 30
    kubectl wait --for=condition=Ready pod/"${TEST_POD}" --timeout=60s

    run_cache_test "${HTTP_TEST_URL}" -x "${HTTP_PROXY}"
}

run_ssl_bump_test() {
    section "Generating SSL certificates"
    rm -rf "${SCRIPT_DIR}/squid-ssl-certs" 2>/dev/null || true
    "${SCRIPT_DIR}/generate-ssl-certs.sh" --namespace "${NAMESPACE}" --out-dir "${SCRIPT_DIR}/squid-ssl-certs" --org "${SSL_CA_ORG}"
    success "SSL certificates generated"

    # Must generate certs BEFORE deploying squid so the secret exists
    deploy_squid

    section "Deploying test client pod with CA trust"
    kubectl delete pod "${TEST_POD}" --ignore-not-found --wait=true 2>/dev/null
    kubectl apply -f "${SCRIPT_DIR}/curl-test-pod.yaml"
    kubectl wait --for=condition=Ready pod/"${TEST_POD}" --timeout=120s

    section "Testing SSL Bump Cache"
    run_cache_test "${SSL_TEST_URL}"

    section "Verifying SSL Bump interception"
    kubectl exec "${TEST_POD}" -- curl -sfv "${SSL_TEST_URL}" -o /dev/null 2>&1 | grep -i "issuer" || echo "Could not verify issuer"
}

# --- Main Execution Flow -------------------------------------------------------

if [[ ${BUILD_PUSH} == true ]]; then
    build_and_push_image
    echo -e "\nNOTE: Run the test with: $0 --mode ssl-bump --openshift --registry ${REGISTRY}"
    
    # Unregister the trap so we don't accidentally delete an active cluster
    trap - EXIT
    exit 0
fi

if [[ "${MODE}" == "ssl-bump" && ${USE_OPENSHIFT} == false ]]; then
    build_and_push_image
fi

setup_cluster

if [[ "${MODE}" == "http" ]]; then
    deploy_squid
    run_http_test
else
    run_ssl_bump_test
fi

section "Summary"
success "Proxy deployed and tested (${MODE})"
echo "To inspect manually: kubectl logs -l app=multimedia-downloader -c squid"
