#!/usr/bin/env bash
# deploy-and-test-squid.sh: deploys and tests the Squid HTTP or SSL Bump implementation.
#
# Usage: ./deploy-and-test-squid.sh [options]
#
# Options:
#   --mode <http|ssl-bump>  Implementation to test (default: http)
#   --platform <k8s|openshift>
#                           Overlay to apply to the current kubectl context (default: k8s)
#   --openshift             Alias for --platform openshift
#   --context <ctx>         kubectl context to switch to before deploying
#   --registry <url>        Registry for the SSL Bump image (or export SQUID_IMAGE_REGISTRY)
#   --build-push            Build and push the SSL Bump image, then exit (implies --mode ssl-bump)
#   --skip-cleanup          Leave resources in place after the test
#   --help, -h              Show this help
#
# The script deploys into the current kubectl context and namespace, and on exit
# deletes only the resources it created. SSL Bump mode needs the image in a
# registry the cluster can pull from (see --build-push).
#
# Requirements: kubectl, docker (--build-push)

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DOCKER_DIR="${SCRIPT_DIR}/../docker"
readonly SERVICE_YAML="${SCRIPT_DIR}/../../../service.yaml"
readonly CURL_IMAGE="curlimages/curl:8.11.1"
readonly HTTP_TEST_URL="http://images.cocodataset.org/val2017/000000039769.jpg"
readonly PROXY_URL="http://multimedia-downloader:80"
readonly SSL_TEST_URL="https://images.dog.ceo/breeds/poodle-standard/n02113799_2280.jpg"
readonly SSL_CA_ORG="Squid Test CA"
readonly SSL_SECRETS=(squid-ssl-certs squid-ca-public-cert)

# Default configurations
MODE="http"
SKIP_CLEANUP=false
PLATFORM="k8s"
KUBE_CONTEXT=""
REGISTRY="${SQUID_IMAGE_REGISTRY:-}" # Reads from ENV var if set
BUILD_PUSH=false

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
        --platform)         PLATFORM="$2"; shift 2 ;;
        --openshift)        PLATFORM="openshift"; shift ;;
        --registry)         REGISTRY="$2"; shift 2 ;;
        --build-push)       BUILD_PUSH=true; MODE="ssl-bump"; shift ;;
        --context)          KUBE_CONTEXT="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,/^# Requirements:/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) error "Unknown argument: $1"; exit 1 ;;
    esac
done

# --- Validation & Dynamic Variables --------------------------------------------
# TEST_TARGET is a kubectl resource reference usable with exec, wait and delete.
case "${MODE}" in
    http)
        readonly TEST_TARGET="pod/curl-test-pod"
        readonly IMPL_DIR="${SCRIPT_DIR}/../http"
        ;;
    ssl-bump)
        readonly TEST_TARGET="deployment/curl-ssl-test"
        readonly IMPL_DIR="${SCRIPT_DIR}/../https-ssl-bump"
        ;;
    *) error "Invalid --mode '${MODE}'. Must be 'http' or 'ssl-bump'."; exit 1 ;;
esac
case "${PLATFORM}" in
    k8s|openshift) ;;
    *) error "Invalid --platform '${PLATFORM}'. Must be 'k8s' or 'openshift'."; exit 1 ;;
esac
readonly KUSTOMIZE_DIR="${IMPL_DIR}/overlays/${PLATFORM}"

[[ "${MODE}" == "ssl-bump" && -z "${REGISTRY}" ]] && { error "--mode ssl-bump requires a registry. Use --registry or export SQUID_IMAGE_REGISTRY"; exit 1; }
readonly SSL_BUMP_IMAGE="${REGISTRY}/squid-ssl-bump:dev"

# --- Core Functions ------------------------------------------------------------

cleanup() {
    section "Cleaning up"

    if [[ ${SKIP_CLEANUP} == true ]]; then
        echo "  Resources left in place for inspection (--skip-cleanup)."
        return
    fi
    kubectl delete "${TEST_TARGET}" --ignore-not-found --wait=false 2>/dev/null || true
    kubectl delete -f "${SERVICE_YAML}" --ignore-not-found 2>/dev/null || true
    kubectl delete -k "${KUSTOMIZE_DIR}" --ignore-not-found 2>/dev/null || true
    if [[ "${MODE}" == "ssl-bump" ]]; then
        kubectl delete secret "${SSL_SECRETS[@]}" --ignore-not-found 2>/dev/null || true
        rm -rf "${SCRIPT_DIR}/squid-ssl-certs"
    fi
    echo "  multimedia-downloader resources deleted."
}

build_and_push_image() {
    section "Building and pushing Squid SSL Bump image"
    docker build -t "${SSL_BUMP_IMAGE}" -f "${DOCKER_DIR}/Dockerfile.squid-ssl-bump" "${DOCKER_DIR}"
    docker push "${SSL_BUMP_IMAGE}"
    success "Image pushed: ${SSL_BUMP_IMAGE}"
}

setup_context() {
    if [[ -n "${KUBE_CONTEXT}" ]]; then
        kubectl config use-context "${KUBE_CONTEXT}"
    fi
    local namespace
    namespace="$(kubectl config view --minify --output 'jsonpath={..namespace}' 2>/dev/null)"
    echo "  Using context '$(kubectl config current-context)', namespace '${namespace:-default}', platform '${PLATFORM}'"
}

deploy_squid() {
    section "Deploying squid (mode: ${MODE}, overlay: ${PLATFORM})"
    kubectl apply -k "${KUSTOMIZE_DIR}"
    if [[ "${MODE}" == "ssl-bump" ]]; then
        kubectl set image deployment/multimedia-downloader squid="${SSL_BUMP_IMAGE}"
        # Squid loads the CA at startup; restart so a pre-existing deployment picks up
        # the certificates generated by this run.
        kubectl rollout restart deployment/multimedia-downloader
    fi
    kubectl apply -f "${SERVICE_YAML}"
    kubectl rollout status deployment/multimedia-downloader --timeout=120s
    success "multimedia-downloader is ready."
}

# squid_log_line <url>: prints the latest access log entry for url, polling briefly
# because the entry is written when the request completes. Only pods that are not
# terminating are read, so a previous rollout's log cannot satisfy the check.
squid_log_line() {
    local url="$1" line="" pods
    pods=$(kubectl get pod -l app=multimedia-downloader -o go-template='{{range .items}}{{if not .metadata.deletionTimestamp}}{{.metadata.name}} {{end}}{{end}}')
    for _ in $(seq 10); do
        # shellcheck disable=SC2086 # pods is a space-separated list
        line=$(for p in ${pods}; do kubectl logs "${p}" -c squid --tail=20; done | grep -F "${url}" | tail -1 || true)
        [[ -n "${line}" ]] && break
        sleep 0.5
    done
    echo "${line}"
}

# assert_squid_status <expected_pattern> <url> [curl_args...]
# Sends one curl request and verifies the squid log contains expected_pattern for that URL.
assert_squid_status() {
    local expected="$1" url="$2"; shift 2

    echo "  Requesting ${url} (expect ${expected})..."
    if ! kubectl exec "${TEST_TARGET}" -- curl -sf -o /dev/null "$@" "${url}"; then
        error "curl request failed: ${url}"
        return 1
    fi

    local log_line
    log_line=$(squid_log_line "${url}")
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
    # A pod cannot be re-run in place; remove one left by a --skip-cleanup run.
    kubectl delete "${TEST_TARGET}" --ignore-not-found --wait=true 2>/dev/null
    kubectl run "${TEST_TARGET#pod/}" --image="${CURL_IMAGE}" --restart=Never -- sleep 30
    kubectl wait --for=condition=Ready "${TEST_TARGET}" --timeout=60s

    run_cache_test "${HTTP_TEST_URL}" -x "${PROXY_URL}"
}

run_ssl_bump_test() {
    section "Generating SSL certificates"
    rm -rf "${SCRIPT_DIR}/squid-ssl-certs"
    "${SCRIPT_DIR}/generate-ssl-certs.sh" --out-dir "${SCRIPT_DIR}/squid-ssl-certs" --org "${SSL_CA_ORG}"
    success "SSL certificates generated"

    # Must generate certs BEFORE deploying squid so the secret exists
    deploy_squid

    section "Deploying test client with CA trust (client-trust Component)"
    # Delete first so the init container re-reads the freshly generated CA on reruns.
    kubectl delete "${TEST_TARGET}" --ignore-not-found --wait=true 2>/dev/null
    kubectl apply -k "${SCRIPT_DIR}"
    kubectl rollout status "${TEST_TARGET}" --timeout=120s

    section "Testing SSL Bump Cache"
    run_cache_test "${SSL_TEST_URL}"

    section "Verifying SSL Bump interception"
    kubectl exec "${TEST_TARGET}" -- curl -sfv "${SSL_TEST_URL}" -o /dev/null 2>&1 | grep -i "issuer" || echo "Could not verify issuer"
}

# --- Main Execution Flow -------------------------------------------------------

if [[ ${BUILD_PUSH} == true ]]; then
    build_and_push_image
    echo -e "\nNOTE: Run the test with: $0 --mode ssl-bump --platform ${PLATFORM} --registry ${REGISTRY}"
    exit 0
fi

trap cleanup EXIT
setup_context

if [[ "${MODE}" == "http" ]]; then
    deploy_squid
    run_http_test
else
    run_ssl_bump_test
fi

section "Summary"
success "Proxy deployed and tested (${MODE})"
echo "To inspect manually: kubectl logs -l app=multimedia-downloader -c squid"
