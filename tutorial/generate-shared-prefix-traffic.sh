#!/usr/bin/env bash
# generate-shared-prefix-traffic.sh — Tutorial traffic generator for llm-d
#
# Sends requests with shared prefixes to demonstrate the routing difference
# between vanilla k8s round-robin and llm-d prefix-cache-aware scheduling.
#
# Usage:
#   ./generate-shared-prefix-traffic.sh [OPTIONS]
#
# Options:
#   -e ENDPOINT    Base URL (default: http://localhost:8000)
#   -m MODEL       Model name (default: random)
#   -d DURATION    Duration in seconds (default: 120)
#   -r RATE        Requests per second (default: 5)
#   -p PREFIXES    Number of distinct shared prefixes (default: 3)
#   -h             Show help

set -euo pipefail

# Defaults
ENDPOINT="${ENDPOINT:-http://localhost:8000}"
MODEL="${MODEL_NAME:-mistralai/Mistral-7B-v0.1}"
DURATION=120
RATE=5
NUM_PREFIXES=3

usage() {
    echo "Usage: $0 [-e endpoint] [-m model] [-d duration_sec] [-r rate_rps] [-p num_prefixes]"
    exit 0
}

while getopts "e:m:d:r:p:h" opt; do
    case $opt in
        e) ENDPOINT="$OPTARG" ;;
        m) MODEL="$OPTARG" ;;
        d) DURATION="$OPTARG" ;;
        r) RATE="$OPTARG" ;;
        p) NUM_PREFIXES="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

# Shared system prompts — these simulate real workloads where many users
# share the same system prompt / RAG context / few-shot examples.
PREFIXES=(
    "You are a customer support agent for Acme Corp. You have access to the company knowledge base covering product returns, shipping policies, warranty claims, account management, and billing inquiries. Always respond professionally and reference relevant policy sections when applicable."
    "You are a code review assistant. You analyze code changes for correctness, performance, security vulnerabilities, and adherence to best practices. Provide specific line-level feedback and suggest improvements with code examples where appropriate."
    "You are a medical triage assistant. Based on the symptoms described, assess urgency level, suggest possible conditions, and recommend whether the patient should seek immediate care, schedule an appointment, or manage at home. Always include a disclaimer about consulting a healthcare professional."
    "You are a financial analyst assistant. Analyze the provided market data, earnings reports, and economic indicators to generate insights. Present your analysis with supporting data points and clearly separate facts from projections."
    "You are a travel planning assistant. Help users plan trips by suggesting destinations, accommodations, transportation, and activities based on their preferences, budget, and schedule constraints. Provide specific recommendations with estimated costs."
)

# User questions to append after the shared prefix
QUESTIONS=(
    "What is your return policy for electronics?"
    "How do I track my order?"
    "Can I change my shipping address after placing an order?"
    "What payment methods do you accept?"
    "How long does standard shipping take?"
    "Do you offer international shipping?"
    "What is the warranty period for laptops?"
    "How do I contact customer support?"
    "Can I get a refund for a digital purchase?"
    "What are your business hours?"
)

SLEEP_INTERVAL=$(awk "BEGIN {printf \"%.3f\", 1.0 / $RATE}")
END_TIME=$(($(date +%s) + DURATION))

echo "================================================================"
echo "  llm-d Tutorial — Shared Prefix Traffic Generator"
echo "================================================================"
echo "  Endpoint:      $ENDPOINT"
echo "  Model:         $MODEL"
echo "  Duration:      ${DURATION}s"
echo "  Rate:          ${RATE} req/s"
echo "  Prefixes:      $NUM_PREFIXES distinct system prompts"
echo "  Sleep:         ${SLEEP_INTERVAL}s between requests"
echo "================================================================"
echo ""

# Counters (indexed array — bash 3.2 compatible, no associative array needed)
PREFIX_COUNTS=()
TOTAL=0
ERRORS=0

for i in $(seq 0 $((NUM_PREFIXES - 1))); do
    PREFIX_COUNTS[$i]=0
done

# Trap Ctrl+C for clean shutdown
cleanup() {
    echo ""
    echo "================================================================"
    echo "  Results"
    echo "================================================================"
    echo "  Total requests sent:  $TOTAL"
    echo "  Errors:               $ERRORS"
    echo ""
    echo "  Requests per prefix:"
    for i in $(seq 0 $((NUM_PREFIXES - 1))); do
        echo "    Prefix $i: ${PREFIX_COUNTS[$i]} requests"
    done
    echo "================================================================"
    echo ""
    echo "Now check your Grafana dashboards and Prometheus metrics."
    echo "Compare request distribution across pods."
    exit 0
}
trap cleanup INT TERM

echo "Sending traffic... (Ctrl+C to stop and show results)"
echo ""

while [ "$(date +%s)" -lt "$END_TIME" ]; do
    # Pick a prefix (weighted: prefix 0 gets more traffic to simulate hot prefix)
    if (( RANDOM % 3 == 0 )); then
        prefix_idx=0
    else
        prefix_idx=$(( RANDOM % NUM_PREFIXES ))
    fi

    question_idx=$(( RANDOM % ${#QUESTIONS[@]} ))
    prompt="${PREFIXES[$prefix_idx]}\n\nUser: ${QUESTIONS[$question_idx]}"

    # Send request in background (non-blocking)
    (
        response=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            "${ENDPOINT}/v1/completions" \
            -H "Content-Type: application/json" \
            -d "{
                \"model\": \"${MODEL}\",
                \"prompt\": \"${prompt}\",
                \"max_tokens\": 30
            }" 2>/dev/null || echo "000")

        if [ "$response" != "200" ]; then
            echo "  [WARN] Request returned HTTP $response"
        fi
    ) &

    PREFIX_COUNTS[$prefix_idx]=$((${PREFIX_COUNTS[$prefix_idx]} + 1))
    TOTAL=$((TOTAL + 1))

    # Progress every 20 requests
    if (( TOTAL % 20 == 0 )); then
        elapsed=$(( $(date +%s) - (END_TIME - DURATION) ))
        remaining=$(( END_TIME - $(date +%s) ))
        echo "  [${elapsed}s] Sent $TOTAL requests | ${remaining}s remaining"
    fi

    sleep "$SLEEP_INTERVAL"
done

# Wait for any background requests to finish
wait 2>/dev/null

cleanup
