#!/bin/bash

# Load generation script with malformed requests to trigger error metrics
# Usage: ./generate-load-llmd.sh [duration_minutes]

set -e

ENDPOINT="http://localhost:8000/v1"
DURATION_MINUTES=${1:-5}
MODEL_NAME="Qwen/Qwen3-0.6B"

echo "Load Generator with Error Generation"
echo "==================================="
echo "Endpoint: $ENDPOINT"
echo "Model: $MODEL_NAME"
echo "Duration: $DURATION_MINUTES minutes"
echo "Press Ctrl+C to stop"
echo ""

# First, check if the model is available
echo "Checking model availability..."
echo "------------------------------"
curl -s "$ENDPOINT/models" | jq . || echo "Failed to get models"
echo ""

# Function to send a normal request
send_request() {
    local request_num=$1
    local prompt=$2

    echo "Request #$request_num (NORMAL)"
    echo "Prompt: $prompt"
    echo "Sending..."

    local start_time=$(date +%s%N)

    local response=$(curl -s -X POST "$ENDPOINT/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{
            \"model\": \"$MODEL_NAME\",
            \"messages\": [
                {\"role\": \"user\", \"content\": \"$prompt\"}
            ],
            \"max_tokens\": 50,
            \"temperature\": 0.7,
            \"stream\": false
        }")

    local end_time=$(date +%s%N)
    local duration_ms=$(( (end_time - start_time) / 1000000 ))

    if [ -n "$response" ]; then
        echo "Response (${duration_ms}ms):"
        echo "$response" | jq . 2>/dev/null || echo "$response"
    else
        echo "ERROR: Empty response after ${duration_ms}ms"
    fi

    echo "----------------------------------------"
    echo ""
}

# Function to send malformed requests to trigger errors
send_malformed_request() {
    local request_num=$1
    local error_type=$2

    echo "Request #$request_num (MALFORMED - $error_type)"
    echo "Sending..."

    local start_time=$(date +%s%N)
    local response=""

    case $error_type in
        "invalid_model")
            # Request with non-existent model
            response=$(curl -s -X POST "$ENDPOINT/chat/completions" \
                -H "Content-Type: application/json" \
                -d "{
                    \"model\": \"nonexistent-model-123\",
                    \"messages\": [
                        {\"role\": \"user\", \"content\": \"Hello\"}
                    ],
                    \"max_tokens\": 50
                }")
            ;;
        "malformed_json")
            # Invalid JSON syntax
            response=$(curl -s -X POST "$ENDPOINT/chat/completions" \
                -H "Content-Type: application/json" \
                -d "{
                    \"model\": \"$MODEL_NAME\",
                    \"messages\": [
                        {\"role\": \"user\", \"content\": \"Hello\"
                    ],
                    \"max_tokens\": 50
                }" 2>&1)
            ;;
        "missing_required_field")
            # Missing required 'messages' field
            response=$(curl -s -X POST "$ENDPOINT/chat/completions" \
                -H "Content-Type: application/json" \
                -d "{
                    \"model\": \"$MODEL_NAME\",
                    \"max_tokens\": 50
                }")
            ;;
        "invalid_temperature")
            # Invalid temperature value (out of range)
            response=$(curl -s -X POST "$ENDPOINT/chat/completions" \
                -H "Content-Type: application/json" \
                -d "{
                    \"model\": \"$MODEL_NAME\",
                    \"messages\": [
                        {\"role\": \"user\", \"content\": \"Hello\"}
                    ],
                    \"max_tokens\": 50,
                    \"temperature\": 5.0
                }")
            ;;
        "invalid_max_tokens")
            # Negative max_tokens
            response=$(curl -s -X POST "$ENDPOINT/chat/completions" \
                -H "Content-Type: application/json" \
                -d "{
                    \"model\": \"$MODEL_NAME\",
                    \"messages\": [
                        {\"role\": \"user\", \"content\": \"Hello\"}
                    ],
                    \"max_tokens\": -10
                }")
            ;;
        "wrong_endpoint")
            # Non-existent endpoint
            response=$(curl -s -X POST "$ENDPOINT/nonexistent/endpoint" \
                -H "Content-Type: application/json" \
                -d "{
                    \"model\": \"$MODEL_NAME\",
                    \"messages\": [
                        {\"role\": \"user\", \"content\": \"Hello\"}
                    ]
                }")
            ;;
        "no_content_type")
            # Missing Content-Type header
            response=$(curl -s -X POST "$ENDPOINT/chat/completions" \
                -d "{
                    \"model\": \"$MODEL_NAME\",
                    \"messages\": [
                        {\"role\": \"user\", \"content\": \"Hello\"}
                    ],
                    \"max_tokens\": 50
                }")
            ;;
    esac

    local end_time=$(date +%s%N)
    local duration_ms=$(( (end_time - start_time) / 1000000 ))

    echo "Error Response (${duration_ms}ms):"
    echo "$response" | jq . 2>/dev/null || echo "$response"
    echo "----------------------------------------"
    echo ""
}

# Normal prompts
prompts=(
    "Hello!"
    "What is 2+2?"
    "Tell me a joke"
    "What is the capital of France?"
    "How does a computer work?"
)

# Error types to cycle through
error_types=(
    "invalid_model"
    "malformed_json"
    "missing_required_field"
    "invalid_temperature"
    "invalid_max_tokens"
    "wrong_endpoint"
    "no_content_type"
)

# Trap SIGINT to handle graceful shutdown
trap 'echo -e "\n\nShutting down gracefully..."; show_final_metrics; exit 0' INT

show_final_metrics() {
    echo ""
    echo "Final metrics check..."
    echo "======================="
    echo "Looking for error metrics:"
    curl -s http://localhost:8080/metrics | grep -E "inference.*error" || echo "No error metrics found yet"
    echo ""
    echo "Request metrics:"
    curl -s http://localhost:8080/metrics | grep -E "inference.*request_total" || echo "No request metrics found"
    echo ""
    echo "All inference metrics:"
    curl -s http://localhost:8080/metrics | grep -E "inference_" | grep -v "#" | head -10
}

# Calculate end time
start_time=$(date +%s)
end_time=$((start_time + DURATION_MINUTES * 60))
request_count=0
error_count=0

echo "Starting load generation with error injection..."
echo "Start time: $(date)"
echo ""

# Send requests continuously until duration expires
while [ $(date +%s) -lt $end_time ]; do
    request_count=$((request_count + 1))

    # Every 5th request is malformed to generate errors
    if [ $((request_count % 5)) -eq 0 ]; then
        error_count=$((error_count + 1))
        error_index=$(( (error_count - 1) % ${#error_types[@]} ))
        error_type="${error_types[$error_index]}"
        send_malformed_request "$request_count" "$error_type"
    else
        # Normal request
        prompt_index=$(( (request_count - 1) % ${#prompts[@]} ))
        prompt="${prompts[$prompt_index]}"
        send_request "$request_count" "$prompt"
    fi

    # Small delay between requests
    sleep 2

    # Show progress every 10 requests
    if [ $((request_count % 10)) -eq 0 ]; then
        current_time=$(date +%s)
        elapsed_seconds=$((current_time - start_time))
        remaining_seconds=$((end_time - current_time))
        elapsed_minutes=$((elapsed_seconds / 60))
        remaining_minutes=$((remaining_seconds / 60))
        echo ">>> Requests: $request_count (Errors: $error_count) | Elapsed: ${elapsed_minutes}m | Remaining: ${remaining_minutes}m"
        echo ""
    fi
done

echo ""
echo "Load generation complete!"
echo "Total requests sent: $request_count"
echo "Error requests sent: $error_count"
echo "End time: $(date)"

show_final_metrics
