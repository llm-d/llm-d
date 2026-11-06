#!/usr/bin/env bash
# One benchmark or gate run through the llm-d router with the ROCm MAD GLM-5.3 scripts
# (scripts/vllm_dissag at MAD_REF). Runs in the engine image, which has `vllm bench` and vLLM's
# benchmarks/multi_turn client, on CPU only.
#
#   MODE=multiturn  warm multi-turn sessions: RETAINED_TOKENS of cached context plus NEW_TOKENS
#                   per turn, CONCURRENCY sessions, ROUNDS rounds (benchmark_multiturn_retained.sh)
#   MODE=cold       PROMPT_TOKENS-token prompts with nothing cached, CONCURRENCY in flight,
#                   NUM_PROMPTS measured (the cell of benchmark_long_context.sh LC_DETAILED=1,
#                   pointed at the router)
#   MODE=gates      KV-transfer probe, GSM8K, NIAH, multi-turn needle (benchmark_gates.sh; P/D
#                   with the router on glm-5-3-pd-accuracy.values.yaml)
#
# Every request generates exactly 1,000 tokens. GPUS (every GPU of the deployment) and LAYOUT
# (pd | coloc) set the tok/s/GPU denominator and which engines' ITL counts; METRICS_ENDPOINTS
# ("prefill=<ip>:8000 decode=<ip>:8200" or "coloc=<ip>:<port> ...") are the engines' own /metrics.
# The defaults are the 256k-context, +30k-per-turn workload on pd-1p1d.
# Writes /results/<RUN_ID>/ and appends to /results/perf.csv.
set -uo pipefail

: "${GATEWAY_URL:?}" "${MODEL:?}" "${TOKENIZER:?}" "${MAD_REF:?}"
GATEWAY_URL=${GATEWAY_URL%/}
MODE=${MODE:-multiturn}
RETAINED_TOKENS=${RETAINED_TOKENS:-256000}
NEW_TOKENS=${NEW_TOKENS:-30000}
CONCURRENCY=${CONCURRENCY:-16}
GPUS=${GPUS:-16}
LAYOUT=${LAYOUT:-pd}
OSL=1000
CACHE=/results/cache
RUN_ID=${RUN_ID:-${MODE}-$(date -u +%Y%m%dT%H%M%SZ)}
RUN_DIR=/results/$RUN_ID
mkdir -p "$RUN_DIR" "$CACHE"
exec > >(tee -a "$RUN_DIR/bench.log") 2>&1
log() { echo "$(date -u +%FT%TZ) [bench $RUN_ID] $*"; }
log "mode=$MODE gateway=$GATEWAY_URL model=$MODEL gpus=$GPUS layout=$LAYOUT metrics='${METRICS_ENDPOINTS:-}'"

# A real completion: max_tokens=1 is an error under the glm45 reasoning parser.
for i in $(seq 1 30); do
  curl -sf -m 300 "$GATEWAY_URL/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL\",\"max_tokens\":64,\"messages\":[{\"role\":\"user\",\"content\":\"Say hello.\"}]}" \
    -o /dev/null && break
  [[ $i == 30 ]] && { log "router not answering"; exit 12; }
  log "router not answering yet ($i/30)"; sleep 20
done

mad=$CACHE/mad-${MAD_REF:0:12}
if [[ ! -f $mad/.ok ]]; then
  if ! { rm -rf "$mad" && git init -q "$mad" &&
         git -C "$mad" fetch -q --depth 1 https://github.com/ROCm/MAD.git "$MAD_REF" &&
         git -C "$mad" checkout -q FETCH_HEAD; }; then
    log "cannot fetch ROCm/MAD $MAD_REF"; exit 14
  fi
  touch "$mad/.ok"
fi
MAD=$mad/scripts/vllm_dissag
export METRICS_ENDPOINTS=${METRICS_ENDPOINTS:-} MODEL_NAME=GLM-5.3-Quark-MXFP4-AttnFP8
export MT_CORPUS=$CACHE/pg1184.txt

case "$MODE" in
multiturn)
  MT_RETAINED_TOKENS=$RETAINED_TOKENS MT_NEW_TOKENS=$NEW_TOKENS BENCHMARK_CON=$CONCURRENCY \
  MT_ROUNDS=${ROUNDS:-6} MT_OSL=$OSL MT_BASE_URL=$GATEWAY_URL SERVED_MODEL_NAME=$MODEL \
  TOKENIZER=$TOKENIZER MT_GPUS=$GPUS MT_LAYOUT=$LAYOUT MT_RUN_DIR=$RUN_DIR/run \
    bash "$MAD/benchmark_multiturn_retained.sh"
  ;;
cold)
  : "${PROMPT_TOKENS:?}" "${NUM_PROMPTS:?}"
  [[ -n $METRICS_ENDPOINTS ]] || { log "cold needs METRICS_ENDPOINTS to reset the prefix caches"; exit 2; }
  cell=$RUN_DIR/run
  mkdir -p "$cell"
  common=(--backend vllm --base-url "$GATEWAY_URL" --endpoint /v1/completions --model "$MODEL"
          --tokenizer "$TOKENIZER" --trust-remote-code --dataset-name random --random-prefix-len 0
          --random-range-ratio 0 --ignore-eos --request-rate inf --disable-tqdm)
  timeout 900 vllm bench serve "${common[@]}" --random-input-len 32 --random-output-len 32 \
    --num-prompts 16 --max-concurrency 1 --seed 7 > "$cell/warmup-global.log" 2>&1 || log "global warm-up stalled"
  timeout 2400 vllm bench serve "${common[@]}" --random-input-len "$PROMPT_TOKENS" --random-output-len "$OSL" \
    --num-prompts 8 --max-concurrency 4 --seed 1000 > "$cell/warmup-shape.log" 2>&1 || log "shape warm-up stalled"
  # Nothing cached for the measured prompts. Needs VLLM_SERVER_DEV_MODE=1; fails while blocks are
  # still held, so retry.
  for kv in $METRICS_ENDPOINTS; do
    for i in $(seq 1 30); do
      curl -s -m 60 -X POST "http://${kv#*=}/reset_prefix_cache" | grep -q '"success": *true' && break
      [[ $i == 30 ]] && { log "reset_prefix_cache failed on ${kv#*=}"; exit 15; }
      sleep 10
    done
  done
  bash "$MAD/scrape_engine_metrics.sh" snapshot "$cell" before
  bash "$MAD/scrape_engine_metrics.sh" sample "$cell" & sampler=$!
  # shellcheck disable=SC2054  # comma-separated values of one flag
  cmd=(vllm bench serve "${common[@]}" --random-input-len "$PROMPT_TOKENS" --random-output-len "$OSL"
       --max-concurrency "$CONCURRENCY" --num-prompts "$NUM_PROMPTS" --num-warmups 0 --seed 0
       --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,90,99,99.9
       --save-result --save-detailed --result-dir "$cell" --result-filename bench_serve.json)
  printf '%q ' "${cmd[@]}" > "$cell/benchmark_command.txt"
  timeout "${STEP_TIMEOUT:-7200}" "${cmd[@]}" > "$cell/bench_serve.stdout" 2>&1 || log "measured run failed or stalled"
  date -u +%s.%N > "$cell/t_end"
  kill "$sampler" 2>/dev/null
  bash "$MAD/scrape_engine_metrics.sh" snapshot "$cell" after
  python3 "$MAD/multiturn_metrics.py" "$cell" --mode bench_serve --gpus "$GPUS" --isl "$PROMPT_TOKENS" \
    --osl "$OSL" --layout "$LAYOUT"
  python3 "$MAD/parse_to_csv.py" "$cell/result.json" --result-json --append --perf-csv /results/perf.csv \
    --model-name "$MODEL_NAME" --workload "cold isl=${PROMPT_TOKENS} con=${CONCURRENCY}"
  ;;
gates)
  GATES_BASE_URL=$GATEWAY_URL SERVED_MODEL_NAME=$MODEL TOKENIZER=$TOKENIZER GATES_RUN_DIR=$RUN_DIR/run \
  GATES_PERF_CSV=/results/perf.csv bash "$MAD/benchmark_gates.sh"
  ;;
*) log "unknown MODE=$MODE"; exit 2 ;;
esac
