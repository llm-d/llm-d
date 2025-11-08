#!/bin/bash
# Convenience wrapper for running GAIE SLO benchmarks
# Automatically discovers gateway endpoint and runs benchmarks

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Parse arguments
OUTPUT_DIR="${OUTPUT_DIR:-./benchmark_results}"
TOOL="${TOOL:-genai-perf}"
CONCURRENCIES="${CONCURRENCIES:-1 2 5 10 20}"
SLO_PROFILES="${SLO_PROFILES:-chatbot rag}"
NUM_PROMPTS="${NUM_PROMPTS:-500}"
DATASET="${DATASET:-sharegpt}"
NO_BASELINE="${NO_BASELINE:-false}"

usage() {
  cat <<'EOF'
Run GAIE SLO-aware routing benchmarks with TTFT/TPOT tracking

Usage:
  run-slo-benchmark.sh [OPTIONS]

Environment Variables:
  OUTPUT_DIR       Output directory (default: ./benchmark_results)
  TOOL             Benchmark tool: genai-perf or vllm (default: genai-perf)
  CONCURRENCIES    Space-separated list of concurrency levels (default: "1 2 5 10 20")
  SLO_PROFILES     Space-separated SLO profiles (default: "chatbot rag")
                   Available: chatbot, code_completion, rag, summarization, strict, relaxed
  NUM_PROMPTS      Number of prompts per test (default: 500)
  DATASET          Input dataset: sharegpt, random, sonnet (default: sharegpt)
  NO_BASELINE      Skip baseline comparison: true or false (default: false)

Examples:
  # Quick test with minimal concurrency
  CONCURRENCIES="1 5" NUM_PROMPTS=100 ./run-slo-benchmark.sh

  # Full benchmark suite
  CONCURRENCIES="1 2 5 10 20 50 100" SLO_PROFILES="chatbot rag strict" ./run-slo-benchmark.sh

  # Test specific SLO profile
  SLO_PROFILES="code_completion" CONCURRENCIES="5 10" ./run-slo-benchmark.sh

  # Skip baseline for faster results
  NO_BASELINE=true ./run-slo-benchmark.sh
EOF
}

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

echo "=== GAIE SLO Benchmark Runner ==="
echo ""

# Discover gateway endpoint
echo "🔍 Discovering gateway endpoint..."
if [[ ! -f "${SCRIPT_DIR}/prepare-inference.sh" ]]; then
  echo "❌ Error: prepare-inference.sh not found"
  exit 1
fi

source "${SCRIPT_DIR}/prepare-inference.sh"

if [[ -z "${GATEWAY_SERVICE_ENDPOINT}" || -z "${MODEL_NAME}" ]]; then
  echo "❌ Error: Failed to discover gateway endpoint or model name"
  exit 1
fi

echo "✅ Gateway endpoint: ${GATEWAY_SERVICE_ENDPOINT}"
echo "✅ Model: ${MODEL_NAME}"
echo ""

# Build benchmark command
BENCHMARK_CMD="/app/gaie_slo_benchmark.py \
  --endpoint ${GATEWAY_SERVICE_ENDPOINT} \
  --model ${MODEL_NAME} \
  --output-dir ${OUTPUT_DIR} \
  --tool ${TOOL} \
  --num-prompts ${NUM_PROMPTS} \
  --dataset ${DATASET}"

# Add concurrencies
BENCHMARK_CMD="${BENCHMARK_CMD} --concurrencies ${CONCURRENCIES}"

# Add SLO profiles
BENCHMARK_CMD="${BENCHMARK_CMD} --slo-profiles ${SLO_PROFILES}"

# Add no-baseline flag if set
if [[ "${NO_BASELINE}" == "true" ]]; then
  BENCHMARK_CMD="${BENCHMARK_CMD} --no-baseline"
fi

echo "🚀 Starting benchmark..."
echo "   Output: ${OUTPUT_DIR}"
echo "   Concurrencies: ${CONCURRENCIES}"
echo "   SLO Profiles: ${SLO_PROFILES}"
echo "   Prompts per test: ${NUM_PROMPTS}"
echo ""

# Run the benchmark
python3 ${BENCHMARK_CMD}

BENCHMARK_EXIT=$?

if [[ ${BENCHMARK_EXIT} -eq 0 ]]; then
  echo ""
  echo "✅ Benchmark completed successfully!"
  echo "📊 Results saved to: ${OUTPUT_DIR}"
  echo ""
  echo "Key metrics tracked:"
  echo "  • TTFT (Time to First Token) - p50, p90, p99"
  echo "  • TPOT (Time per Output Token) - p50, p90, p99"
  echo "  • SLO Attainment Rate (%)"
  echo "  • Goodput (requests/sec meeting SLO)"
  echo ""
  echo "Generated files:"
  echo "  • ${OUTPUT_DIR}/all_results.csv - Raw metrics"
  echo "  • ${OUTPUT_DIR}/summary_comparison.csv - SLO improvements"
  echo "  • ${OUTPUT_DIR}/latency_throughput_curves.png - Performance curves"
  echo "  • ${OUTPUT_DIR}/slo_attainment_comparison.png - SLO attainment"
  echo "  • ${OUTPUT_DIR}/goodput_comparison.png - Goodput analysis"
else
  echo ""
  echo "❌ Benchmark failed with exit code ${BENCHMARK_EXIT}"
  exit ${BENCHMARK_EXIT}
fi
