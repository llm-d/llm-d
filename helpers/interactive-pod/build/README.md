# interactive pod

This directory contains an interactive pod for benchmarking and testing the Gateway API Inference extension.

Features:
- **SLO-aware benchmarking** with TTFT/TPOT tracking (`gaie_slo_benchmark.py`)
- Modified guidellm supporting `max_completion_tokens` stripping
- Auto-discovery of gateway endpoints (`prepare-inference.sh`)
- kubectl access to `llm-d` resources

## SLO Benchmark (TTFT/TPOT Focus)

### Quick Start

The easiest way to run SLO benchmarks:

```bash
./run-slo-benchmark.sh
```

This automatically:
1. Discovers gateway endpoint and model name
2. Runs baseline (no SLO routing) tests
3. Runs SLO-aware routing tests across profiles
4. Generates comprehensive reports with TTFT/TPOT metrics

### Configuration

Configure via environment variables:

```bash
# Quick test (minimal concurrency, fewer prompts)
CONCURRENCIES="1 5 10" NUM_PROMPTS=100 ./run-slo-benchmark.sh

# Full benchmark suite
CONCURRENCIES="1 2 5 10 20 50 100" \
SLO_PROFILES="chatbot rag code_completion" \
NUM_PROMPTS=500 \
./run-slo-benchmark.sh

# Test specific SLO profile
SLO_PROFILES="strict" CONCURRENCIES="5 10" ./run-slo-benchmark.sh

# Skip baseline for faster results
NO_BASELINE=true ./run-slo-benchmark.sh
```

### Available SLO Profiles

| Profile | TTFT Target | TPOT Target | Use Case |
|---------|-------------|-------------|----------|
| `chatbot` | 200ms | 50ms | Interactive chat |
| `code_completion` | 150ms | 30ms | IDE autocomplete |
| `rag` | 300ms | 75ms | Retrieval augmented generation |
| `summarization` | 500ms | 100ms | Batch summarization |
| `strict` | 100ms | 20ms | Ultra-low latency |
| `relaxed` | 1000ms | 200ms | Background processing |

### Metrics Tracked

**TTFT (Time to First Token)**
- Average, P50, P90, P99 latencies
- Critical for perceived responsiveness

**TPOT (Time per Output Token)**
- Average, P50, P90, P99 latencies
- Impacts streaming quality

**SLO Attainment**
- % of requests meeting both TTFT and TPOT targets
- Goodput (throughput × attainment rate)

**Throughput**
- Request throughput (RPS)
- Token throughput (tokens/sec)

### Advanced Usage

Run Python script directly for more control:

```bash
source prepare-inference.sh

python3 /app/gaie_slo_benchmark.py \
  --endpoint "${GATEWAY_SERVICE_ENDPOINT}" \
  --model "${MODEL_NAME}" \
  --output-dir ./my_results \
  --tool genai-perf \
  --concurrencies 1 5 10 20 \
  --slo-profiles chatbot rag \
  --num-prompts 500 \
  --dataset sharegpt
```

Options:
- `--tool`: `genai-perf` (default) or `vllm`
- `--dataset`: `sharegpt` (default), `random`, `sonnet`
- `--no-baseline`: Skip baseline comparison
- `--output-dir`: Results directory

### Output Files

```
benchmark_results/
├── all_results.csv                      # Raw metrics (all tests)
├── summary_comparison.csv               # SLO improvements summary
├── latency_throughput_curves.png        # TTFT/TPOT vs throughput
├── slo_attainment_comparison.png        # SLO attainment rates
├── goodput_comparison.png               # Goodput across profiles
└── baseline_chatbot_c10_baseline/      # Per-test detailed results
    ├── genai_perf.csv
    └── genai_perf.json
```

## Guidellm Benchmark

For simpler load testing without SLO analysis:

```bash
source prepare-inference.sh
guidellm benchmark \
  --target "${GATEWAY_SERVICE_ENDPOINT}" \
  --rate-type sweep \
  --max-seconds 30 \
  --model "${MODEL_NAME}" \
  --data "prompt_tokens=256,output_tokens=128"
```
