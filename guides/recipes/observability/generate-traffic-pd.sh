#!/bin/bash

# Concurrent Traffic Generator for P/D Disaggregation Tracing
#
# This script generates high-concurrency traffic optimized to showcase P/D disaggregation
# benefits and highlight True TTFT vs vLLM TTFT metrics in distributed traces.
#
# Usage: ./generate-traffic-pd.sh [concurrent_workers] [duration_minutes] [endpoint]
#
# 1. CONCURRENT WORKERS: Multiple parallel request streams to stress the P/D pipeline
# 2. SUSTAINED LOAD: Reduced delays to generate continuous traffic
# 3. VARIED ISL/OSL RATIOS: Different worker patterns to show P/D benefits
# 4. BURSTY TRAFFIC: Some workers send bursts to show queueing behavior
#
# - True TTFT (coordinator) will be noticeably higher than vLLM TTFT (decode)
# - P/D coordination overhead visible with concurrent long prompts
# - Prefill vs Decode duration comparison shows stage separation
# - NIXL KV cache transfer metrics (transfer time, bytes transferred, post time)
# - Different ISL/OSL patterns show when P/D is most beneficial

set -e

# Configuration
ENDPOINT="${ENDPOINT:-http://localhost:8000/v1}"
CONCURRENT_WORKERS=${1:-6}
DURATION_MINUTES=${2:-5}
MODEL_NAME="${MODEL_NAME:-meta-llama/Llama-3.1-8B-Instruct}"

# Shared counter for statistics (macOS-compatible)
STATS_DIR="/tmp/load_gen_stats_$$"
mkdir -p "$STATS_DIR"
echo "0" > "$STATS_DIR/total"
echo "0" > "$STATS_DIR/success"
echo "0" > "$STATS_DIR/fail"

increment_stat() {
    local stat_type=$1  # total, success, or failure

    # Simple file-based counter (no flock needed - atomic on most filesystems)
    case $stat_type in
        success)
            echo "1" >> "$STATS_DIR/success"
            echo "1" >> "$STATS_DIR/total"
            ;;
        failure)
            echo "1" >> "$STATS_DIR/fail"
            echo "1" >> "$STATS_DIR/total"
            ;;
        total)
            echo "1" >> "$STATS_DIR/total"
            ;;
    esac
}

get_stats() {
    local total=$(cat "$STATS_DIR/total" 2>/dev/null | wc -l | tr -d ' ')
    local success=$(cat "$STATS_DIR/success" 2>/dev/null | wc -l | tr -d ' ')
    local fail=$(cat "$STATS_DIR/fail" 2>/dev/null | wc -l | tr -d ' ')
    echo "$total $success $fail"
}

echo "============================================================"
echo "   P/D Disaggregation Concurrent Traffic Generator"
echo "============================================================"
echo "Endpoint:     $ENDPOINT"
echo "Model:        $MODEL_NAME"
echo "Workers:      $CONCURRENT_WORKERS"
echo "Duration:     $DURATION_MINUTES minutes"
echo ""
echo "This script creates sustained concurrent load to showcase:"
echo "  ✓ True TTFT vs vLLM TTFT gap (coordinator view vs instance)"
echo "  ✓ Prefill/Decode stage separation and P/D coordination overhead"
echo "  ✓ NIXL KV cache transfer metrics (prefill→decode transfer time/size)"
echo "  ✓ Impact of varied ISL/OSL ratios on P/D performance"
echo "  ✓ Queueing behavior under concurrent load"
echo ""
echo "============================================================"
echo ""

# Verify endpoint is accessible
echo "Checking endpoint availability..."
if ! curl -s -f "$ENDPOINT/models" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach endpoint $ENDPOINT"
    echo "Make sure the llm-d gateway/service is running and accessible"
    exit 1
fi
echo "✓ Endpoint accessible"
echo ""

# Prompt templates for different ISL categories
# Short: ~10-50 tokens - may bypass P/D if selective threshold is set
SHORT_PROMPTS=(
    "What is Kubernetes?"
    "Explain machine learning."
    "What is 2+2?"
)

# Medium: ~200-500 tokens - triggers P/D disaggregation
MEDIUM_PROMPTS=(
    "Explain the differences between microservices and monolithic architectures, including deployment considerations and when each is appropriate."
    "Describe how distributed systems handle fault tolerance and what patterns are commonly used to ensure reliability."
    "What are the key concepts in container orchestration and how does Kubernetes address them?"
)

# Long: ~1000-2000 tokens - optimal for P/D disaggregation, generates meaningful KV cache transfer sizes
LONG_PROMPTS=(
    "Provide a comprehensive explanation of distributed tracing in cloud-native applications. Cover the OpenTelemetry standard, trace context propagation, span hierarchies, sampling strategies, and how tracing data helps optimize microservices performance. Include specific examples of using TraceQL to query distributed traces."
    "Explain prefill-decode disaggregation in LLM serving architectures. Describe how separating the prefill phase (processing input prompts) from the decode phase (generating output tokens) improves resource utilization. Discuss the role of KV cache transfer, the observability challenges this introduces, and why coordinator-level metrics are needed for True TTFT measurement."
    "Describe the architecture and benefits of KV cache-aware routing in distributed LLM inference systems. Explain how prompt prefix caching works, the scoring algorithms used to select endpoints with cache hits, and the performance impact on TTFT latency. Include details on how this interacts with P/D disaggregation."
)

# Very Long: ~3000-5000 tokens - maximum P/D benefit, generates largest NIXL KV cache transfers (~8MB+)
VERY_LONG_PROMPTS=(
    "I need a detailed technical analysis of modern LLM inference serving architectures. Start by explaining the fundamentals of transformer models and attention mechanisms, including how KV caches are generated and used during inference. Then describe the challenges of serving large language models at scale, including memory constraints, compute requirements, and latency targets. Cover advanced optimization techniques like continuous batching, prefix caching, and speculative decoding. Next, explain disaggregated serving architectures that separate prefill and decode phases, including the rationale for specialization (compute-bound prefill vs memory-bound decode), the mechanics of KV cache transfer over RDMA, and how to optimize the prefill-to-decode worker ratio. Discuss the observability gaps that disaggregation creates, specifically why vLLM-reported TTFT metrics are misleading in P/D mode and how distributed tracing with coordinator-level spans solves this problem. Finally, provide concrete recommendations for production deployments, including when to use disaggregation vs monolithic serving, how to tune selective P/D thresholds based on ISL/OSL ratios, and what TraceQL queries to use for performance analysis."
)

# Function to send a request and capture timing
send_request() {
    local worker_id=$1
    local request_num=$2
    local prompt=$3
    local max_tokens=$4
    local stream=${5:-false}

    local start_time=$(date +%s%N)

    # Generate unique request ID for tracing
    local request_id="worker${worker_id}-req${request_num}-$(date +%s%N | tail -c 8)"

    local response=$(curl -s -w "\n%{http_code}" -X POST "$ENDPOINT/chat/completions" \
        -H "Content-Type: application/json" \
        -H "X-Request-ID: $request_id" \
        -d "{
            \"model\": \"$MODEL_NAME\",
            \"messages\": [
                {\"role\": \"user\", \"content\": $(echo "$prompt" | jq -Rs .)}
            ],
            \"max_tokens\": $max_tokens,
            \"temperature\": 0.7,
            \"stream\": $stream
        }" 2>&1)

    local end_time=$(date +%s%N)
    local duration_ms=$(( (end_time - start_time) / 1000000 ))

    local http_code=$(echo "$response" | tail -n1)
    local prompt_length=${#prompt}

    if [ "$http_code" = "200" ]; then
        increment_stat success
        echo "[$(date '+%H:%M:%S')] W${worker_id}-${request_num} | ${prompt_length}ch/${max_tokens}t | ${duration_ms}ms | ✓"
    else
        increment_stat failure
        echo "[$(date '+%H:%M:%S')] W${worker_id}-${request_num} | ${prompt_length}ch/${max_tokens}t | ${duration_ms}ms | ✗ HTTP${http_code}"
    fi
}

# Worker function with specific load pattern
worker_load_generator() {
    local worker_id=$1
    local pattern=$2
    local end_time=$3

    local request_count=0

    echo "[Worker $worker_id] Started with pattern: $pattern"

    while [ $(date +%s) -lt $end_time ]; do
        request_count=$((request_count + 1))

        # Select prompt and parameters based on pattern
        case $pattern in
            "long_isl_short_osl")
                # Long prompts, short outputs - highlights prefill stage, NIXL KV transfers, and P/D coordination overhead
                # This pattern shows maximum True TTFT vs vLLM TTFT gap
                if [ $((request_count % 3)) -eq 0 ]; then
                    idx=$(( RANDOM % ${#VERY_LONG_PROMPTS[@]} ))
                    prompt="${VERY_LONG_PROMPTS[$idx]}"
                    max_tokens=50
                else
                    idx=$(( RANDOM % ${#LONG_PROMPTS[@]} ))
                    prompt="${LONG_PROMPTS[$idx]}"
                    max_tokens=75
                fi
                sleep_time=0.3
                ;;

            "long_isl_long_osl")
                # Long prompts, long outputs - shows full P/D disaggregation benefit
                # Good for showing prefill vs decode duration breakdown
                idx=$(( RANDOM % ${#LONG_PROMPTS[@]} ))
                prompt="${LONG_PROMPTS[$idx]}"
                max_tokens=$((150 + RANDOM % 100))  # 150-250 tokens
                sleep_time=0.5
                ;;

            "mixed_balanced")
                # Realistic production mix - varied ISL and OSL
                case $((request_count % 5)) in
                    0|1)
                        idx=$(( RANDOM % ${#MEDIUM_PROMPTS[@]} ))
                        prompt="${MEDIUM_PROMPTS[$idx]}"
                        max_tokens=$((50 + RANDOM % 50))
                        ;;
                    2)
                        idx=$(( RANDOM % ${#LONG_PROMPTS[@]} ))
                        prompt="${LONG_PROMPTS[$idx]}"
                        max_tokens=$((100 + RANDOM % 100))
                        ;;
                    3)
                        idx=$(( RANDOM % ${#SHORT_PROMPTS[@]} ))
                        prompt="${SHORT_PROMPTS[$idx]}"
                        max_tokens=30
                        ;;
                    4)
                        idx=$(( RANDOM % ${#VERY_LONG_PROMPTS[@]} ))
                        prompt="${VERY_LONG_PROMPTS[$idx]}"
                        max_tokens=$((50 + RANDOM % 100))
                        ;;
                esac
                sleep_time=0.4
                ;;

            "bursty_long")
                # Bursty traffic with long prompts - shows queueing and coordination overhead
                # Send 3 requests quickly, then pause
                if [ $((request_count % 4)) -eq 3 ]; then
                    sleep_time=3.0  # Pause after burst
                else
                    sleep_time=0.1  # Quick succession
                fi
                idx=$(( RANDOM % ${#LONG_PROMPTS[@]} ))
                prompt="${LONG_PROMPTS[$idx]}"
                max_tokens=$((50 + RANDOM % 100))
                ;;

            "streaming_focused")
                # Focus on streaming requests to test streaming behavior in P/D
                idx=$(( RANDOM % ${#MEDIUM_PROMPTS[@]} ))
                prompt="${MEDIUM_PROMPTS[$idx]}"
                max_tokens=$((100 + RANDOM % 100))
                send_request "$worker_id" "$request_count" "$prompt" "$max_tokens" true
                sleep 0.5
                continue
                ;;

            *)
                # Default: medium prompts
                idx=$(( RANDOM % ${#MEDIUM_PROMPTS[@]} ))
                prompt="${MEDIUM_PROMPTS[$idx]}"
                max_tokens=100
                sleep_time=0.5
                ;;
        esac

        send_request "$worker_id" "$request_count" "$prompt" "$max_tokens" false
        sleep "$sleep_time"
    done

    echo "[Worker $worker_id] Completed with $request_count requests"
}

# Distribute workers across different patterns
# This creates a realistic, varied load that showcases different P/D scenarios
patterns=(
    "long_isl_short_osl"   # Worker 1: Show True TTFT gap clearly
    "long_isl_long_osl"    # Worker 2: Show prefill/decode breakdown
    "mixed_balanced"       # Worker 3: Realistic production traffic
    "bursty_long"          # Worker 4: Test queueing and coordination
    "streaming_focused"    # Worker 5: Streaming with P/D
    "long_isl_short_osl"   # Worker 6: More True TTFT gap data
)

# Start workers in background
end_time=$(($(date +%s) + DURATION_MINUTES * 60))

echo "Starting $CONCURRENT_WORKERS concurrent workers..."
echo ""

for i in $(seq 1 $CONCURRENT_WORKERS); do
    pattern_idx=$(( (i - 1) % ${#patterns[@]} ))
    pattern="${patterns[$pattern_idx]}"
    worker_load_generator "$i" "$pattern" "$end_time" &

    # Stagger worker starts slightly to avoid thundering herd
    sleep 0.2
done

# Monitor progress
start_time=$(date +%s)
echo "============================================================"
echo "Traffic generation in progress... (Ctrl+C to stop)"
echo "============================================================"
echo ""

# Show periodic statistics
while [ $(date +%s) -lt $end_time ]; do
    sleep 10
    read total success fail <<< "$(get_stats)"
    current_time=$(date +%s)
    elapsed=$((current_time - start_time))
    remaining=$((end_time - current_time))

    if [ $total -gt 0 ]; then
        success_rate=$((success * 100 / total))
        throughput=$(awk "BEGIN {printf \"%.1f\", $total / $elapsed}")
    else
        success_rate=0
        throughput=0.0
    fi

    echo "[$(date '+%H:%M:%S')] Progress: ${total} total | ${success} success | ${fail} failed | ${success_rate}% success | ${throughput} req/s | Remaining: ${remaining}s"
done

# Wait for all workers to complete
wait

# Final statistics
read total success fail <<< "$(get_stats)"
duration=$(($(date +%s) - start_time))
throughput=$(awk "BEGIN {printf \"%.2f\", $total / $duration}")

echo ""
echo "============================================================"
echo "   Traffic Generation Complete"
echo "============================================================"
echo "Duration:        ${duration}s (${DURATION_MINUTES} minutes)"
echo "Total Requests:  $total"
echo "Successful:      $success"
echo "Failed:          $fail"
echo "Success Rate:    $(awk "BEGIN {printf \"%.1f\", $success * 100 / $total}")%"
echo "Avg Throughput:  ${throughput} req/s"
echo ""
echo "Worker Distribution:"
echo "  - Long ISL + Short OSL: Shows maximum True TTFT vs vLLM TTFT gap + large NIXL transfers"
echo "  - Long ISL + Long OSL:  Shows prefill vs decode duration breakdown + NIXL metrics"
echo "  - Mixed Balanced:       Realistic production traffic patterns with varied KV cache sizes"
echo "  - Bursty Long:          Tests queueing and coordination overhead"
echo "  - Streaming Focused:    Tests streaming behavior with P/D"
echo ""
echo "Expected Trace Spans:"
echo "  - llm_d.pd_proxy.decode: ~$total spans with True TTFT metrics"
echo "  - llm_d.pd_proxy.prefill: ~$total spans (when P/D disaggregation active)"
echo "  - vllm.llm_request (prefill): ~$total spans on prefill instances"
echo "  - vllm.llm_request (decode): ~$total spans on decode instances"
echo ""
echo "Next Steps:"
echo "============================================================"
echo "1. Open Grafana P/D Coordinator Dashboard:"
echo "   http://localhost:3000/d/pd-coordinator-metrics"
echo ""
echo "2. Check key metrics in dashboard:"
echo "   • True TTFT (Coordinator) - should be higher than vLLM TTFT (Decode)"
echo "   • Coordinator Overhead - P/D coordination overhead (sidecar processing)"
echo "   • Prefill vs Decode Duration - shows stage separation"
echo "   • Total Request Duration - end-to-end client experience"
echo "   • NIXL KV Transfer Metrics:"
echo "     - Avg KV Transfer Time: ~50-100ms (prefill→decode KV cache transfer)"
echo "     - Avg MB per Transfer: varies by ISL (longer prompts = larger KV cache)"
echo "     - Total KV Transfers: should match number of P/D requests"
echo ""
echo "3. Explore traces in Grafana (Tempo):"
echo "   Query: {resource.service.name=\"llm-d-pd-proxy\" && name=\"llm_d.pd_proxy.decode\"}"
echo ""
echo "============================================================"

# Cleanup
rm -rf "$STATS_DIR"
