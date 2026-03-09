#!/usr/bin/env bash
set -Eeuo pipefail

# Check that all required commands are available
check_prerequisites() {
    local missing=()

    for cmd in kubectl curl jq guidellm; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "Error: Missing required commands: ${missing[*]}" >&2
        echo "This script requires kubectl, curl, jq, and guidellm to be installed." >&2
        exit 1
    fi
}

# Detect the namespace we're running in
detect_namespace() {
    local ns="${NAMESPACE:-}"

    if [[ -z "$ns" ]]; then
        # Try to get namespace from serviceaccount
        if [[ -f /var/run/secrets/kubernetes.io/serviceaccount/namespace ]]; then
            ns=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)
        else
            # Fall back to kubectl context
            ns=$(kubectl config view --minify -o jsonpath='{..namespace}' 2>/dev/null || echo "default")
        fi
    fi

    echo "$ns"
}

# Fetch gateway address with validation
get_gateway_address() {
    local ns="$1"
    local addr

    addr=$(kubectl get gateway -n "$ns" -o jsonpath='{.items[0].status.addresses[0].value}' 2>&1)
    local exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        echo "Error: Failed to get gateway in namespace '$ns'" >&2
        echo "kubectl output: $addr" >&2
        echo "Try: kubectl get gateway -n $ns" >&2
        exit 1
    fi

    if [[ -z "$addr" ]]; then
        echo "Error: No gateway address found in namespace '$ns'" >&2
        echo "The gateway may not be ready yet." >&2
        echo "Check status: kubectl get gateway -n $ns -o yaml" >&2
        exit 1
    fi

    echo "$addr"
}

# Fetch model name from vLLM API with HTTP status checking
get_model_name() {
    local endpoint="$1"
    local response
    local http_code

    # Use curl with -w to capture HTTP status code
    response=$(curl -s -w "\n%{http_code}" --max-time 10 --connect-timeout 5 "$endpoint/v1/models" 2>&1)
    local curl_exit=$?

    if [[ $curl_exit -ne 0 ]]; then
        echo "Error: Failed to connect to gateway at $endpoint" >&2
        echo "curl exit code: $curl_exit" >&2
        echo "The gateway may not be ready or network is unreachable." >&2
        exit 1
    fi

    http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | head -n-1)

    if [[ "$http_code" != "200" ]]; then
        echo "Error: Gateway returned HTTP $http_code" >&2
        echo "Endpoint: $endpoint/v1/models" >&2
        echo "Response: $body" >&2
        exit 1
    fi

    # Parse model name with jq
    local model
    model=$(echo "$body" | jq -r '.data[0].id // empty' 2>&1)
    local jq_exit=$?

    if [[ $jq_exit -ne 0 ]]; then
        echo "Error: Failed to parse JSON response from gateway" >&2
        echo "jq error: $model" >&2
        echo "Response body: $body" >&2
        exit 1
    fi

    if [[ -z "$model" ]]; then
        echo "Error: No models found at gateway" >&2
        echo "The vLLM server may not have loaded any models yet." >&2
        echo "Response: $body" >&2
        exit 1
    fi

    echo "$model"
}

# Main execution
main() {
    echo "Checking prerequisites..."
    check_prerequisites

    echo "Detecting namespace..."
    NAMESPACE=$(detect_namespace)
    echo "Using namespace: $NAMESPACE"

    echo "Fetching gateway address..."
    GATEWAY_ADDRESS=$(get_gateway_address "$NAMESPACE")
    echo "Gateway address: $GATEWAY_ADDRESS"

    echo "Discovering model name..."
    MODEL_NAME=$(get_model_name "http://${GATEWAY_ADDRESS}")
    echo "Model name: $MODEL_NAME"

    echo ""
    echo "Starting guidellm benchmark..."
    guidellm benchmark \
        --target "http://${GATEWAY_ADDRESS}" \
        --rate-type sweep \
        --max-seconds 30 \
        --model "${MODEL_NAME}" \
        --data "prompt_tokens=256,output_tokens=128"
}

main "$@"
