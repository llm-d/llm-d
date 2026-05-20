#!/bin/bash
set -euo pipefail

# Internal = optimized-baseline-vllm-lb (direct vLLM, bypasses EPP)
INTERNAL_IP="http://35.240.207.135:80"
# External = precise-prefix-cache-aware-epp-lb (through gateway/EPP)
EXTERNAL_IP="http://34.124.184.34:80"

# Model + tokenizer (vLLM serves Qwen/Qwen3-32B in this cluster)
MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"

# EPP deployment to roll-restart whenever its plugin config changes
DEPLOYMENT_NAME="predicted-latency-based-scheduling-epp"

# GCS bucket for reports
GCS_BUCKET="${GCS_BUCKET:-kaushikmitra-llm-ig-benchmark}"

CONCURRENCY_LEVELS="${CONCURRENCY_LEVELS:-50 150 250 350 450}"
#CONCURRENCY_LEVELS="${CONCURRENCY_LEVELS:-70 80 90 100}"
#CONCURRENCY_LEVELS="${CONCURRENCY_LEVELS:-24 28 32 36 40}"
TURNS_PER_CONV="${TURNS_PER_CONV:-3}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKLOAD_DIR="."

# Available workloads (each is a yaml file under ./workloads/, sized for 10x Qwen3-32B):
#   interactive-chat   reasoning   deep-research
#   reasoning          batch-summarization-rag   batch-synthetic-data-generation
#
# Per-scenario keys (workload= is required, the rest match the original shape):
#   run=<name>                     — report file prefix
#   workload=<catalog-folder>      — which workload-catalog entry to use
#   url=$INTERNAL_IP|$EXTERNAL_IP  — target base URL
#   skip_config=true|false         — skip EPP reconfigure + rollout
#   w_prefix w_queue w_kv w_pred   — scheduling weights for server-config template
#   tau tau_global                 — affinity-gate thresholds
#   slo_tpot slo_ttft              — request SLO headers (ms; 0 = unset)
#   stream=false|false              — streaming completions
#   shed=nonsheddable|sheddable    — gateway inference objective
#   flow=false|false                — toggles flowControl gate in EPP config
#   max_concurrency=<int>          — concurrency-detector PER ENDPOINT cap, in TOKENS
#                                    (concurrencyMode=tokens). Default 8192 = vLLM
#                                    max_num_batched_tokens. Trigger threshold =
#                                    max_concurrency × (1 + headroom).
#   headroom=<float>               — multiplier above max_concurrency. Trigger
#                                    threshold = max_concurrency × (1 + headroom).
#                                    Default 3 → allows up to 4× max_num_batched_tokens
#                                    of in-flight prefill work. With inflight-load-producer
#                                    (includeOutputTokens=false) and the EPP image's
#                                    cache discount, the counter measures actual
#                                    prefill work (uncached input tokens) directly.
#   penalty=<int>                  — maxTokensInFlightPenalty for prefix-cache-affinity
#                                    filter (in tokens, default 64000). Higher values
#                                    let affinity hold longer before load-balancing
#                                    counter-pressure kicks in.
#   output=<gcs-subpath>           — report directory under gs://$GCS_BUCKET

SCENARIOS=(
  "run=reasoning-3turn-infperf-epp-penalty6k-req-scorer-max-picker-no-exp-det-2-safe workload=reasoning stream=false flow=false output=workload-catalog-runs url=$EXTERNAL_IP skip_config=false penalty=6000 prefix_weight=0 queue_weight=0 kv_weight=0 active_weight=1 token_load_weight=0 token_load_threshold=4194304 picker=max-score-picker"
  "run=reasoning-3turn-infperf-epp-penalty6k-kv-scorer-max-picker-no-exp-det-2-safe workload=reasoning stream=false flow=false output=workload-catalog-runs url=$EXTERNAL_IP skip_config=false penalty=6000 prefix_weight=0 queue_weight=0 kv_weight=1 active_weight=0 token_load_weight=0 token_load_threshold=4194304 picker=max-score-picker"

  "run=reasoning-3turn-infperf-epp-max-score-det-2-safe workload=reasoning stream=false flow=false output=workload-catalog-runs url=$EXTERNAL_IP skip_config=false penalty=0 prefix_weight=3 queue_weight=2 kv_weight=2 lru_weight=2 token_load_weight=0 token_load_threshold=96000 picker=max-score-picker"
  "run=reasoning-3turn-infperf-epp-penalty5s-latency-predictor-det-3-safe workload=reasoning stream=false flow=false output=workload-catalog-runs url=$EXTERNAL_IP skip_config=false penalty=0 ttft_penalty=5000 prefix_weight=0 queue_weight=0 kv_weight=0 token_load_weight=0 pred_weight=1 token_load_threshold=96000"
  "run=reasoning-3turn-infperf-epp-penalty5s-latency-predictor-det-4-safe workload=reasoning stream=false flow=false output=workload-catalog-runs url=$EXTERNAL_IP skip_config=true penalty=0 ttft_penalty=5000 prefix_weight=0 queue_weight=0 kv_weight=0 token_load_weight=0 pred_weight=1 token_load_threshold=96000"
  "run=reasoning-3turn-infperf-baseline-det-2-safe stream=false flow=false workload=reasoning output=workload-catalog-runs url=$INTERNAL_IP skip_config=true penalty=0 prefix_weight=1 queue_weight=1 kv_weight=1 token_load_weight=0 token_load_threshold=96000"
)


apply_epp_config() {
  export MAX_TOKENS_IN_FLIGHT_PENALTY MAX_TTFT_PENALTY_MS
  export WEIGHT_PREFIX_CACHE WEIGHT_QUEUE WEIGHT_KV_UTIL
  export WEIGHT_TOKEN_LOAD TOKENS_IN_FLIGHT_SCORER_THRESHOLD
  export WEIGHT_PREDICTED_LATENCY WEIGHT_NO_HIT_LRU
  export WEIGHT_ACTIVE_REQUESTS
  export PICKER_PLUGIN
  export AFFINITY_GATE_TAU AFFINITY_GATE_TAU_GLOBAL
  export MAX_CONCURRENCY HEADROOM
  export FLOW_CONTROL_GATE

  if awk -v w="${WEIGHT_PREDICTED_LATENCY:-0}" 'BEGIN { exit (w > 0 ? 0 : 1) }'; then
    echo "[1/4] Applying EndpointPickerConfig (penalty=$MAX_TOKENS_IN_FLIGHT_PENALTY, ttft_penalty=$MAX_TTFT_PENALTY_MS, with predicted latency)..."
    envsubst < "$SCRIPT_DIR/server-config.yaml" | kubectl apply -f -
  else
    echo "[1/4] Applying EndpointPickerConfig (penalty=$MAX_TOKENS_IN_FLIGHT_PENALTY, ttft_penalty=$MAX_TTFT_PENALTY_MS)..."
    if [ "${MAX_TOKENS_IN_FLIGHT_PENALTY:-0}" -eq 0 ]; then
      envsubst < "$SCRIPT_DIR/server-config-no-predicted-latency.yaml" | sed 's/- pluginRef: prefix-cache-affinity-filter/# - pluginRef: prefix-cache-affinity-filter/g' | kubectl apply -f -
    else
      envsubst < "$SCRIPT_DIR/server-config-no-predicted-latency.yaml" | kubectl apply -f -
    fi
  fi
  echo "[2/4] Restarting Deployment $DEPLOYMENT_NAME..."
  kubectl rollout restart "deployment/$DEPLOYMENT_NAME"
  kubectl rollout status "deployment/$DEPLOYMENT_NAME" --timeout=10m
  echo "      Waiting 30s for endpoints to stabilize..."
  sleep 30
}

cd "$SCRIPT_DIR"
export PREDICTED_LATENCY_PARAMS=""
export MODEL_NAME GCS_BUCKET

for scenario in "${SCENARIOS[@]}"; do
  RUN_NAME="" WORKLOAD="" BASE_URL="" SKIP_CONFIG="false" MAX_TOKENS_IN_FLIGHT_PENALTY="64000" MAX_TTFT_PENALTY_MS="0"
  WEIGHT_PREFIX_CACHE="${WEIGHT_PREFIX_CACHE:-1}"
  WEIGHT_QUEUE="${WEIGHT_QUEUE:-0}"
  WEIGHT_KV_UTIL="${WEIGHT_KV_UTIL:-1}"
  WEIGHT_TOKEN_LOAD="${WEIGHT_TOKEN_LOAD:-0}"
  WEIGHT_ACTIVE_REQUESTS="${WEIGHT_ACTIVE_REQUESTS:-0}"
  WEIGHT_PREDICTED_LATENCY="${WEIGHT_PREDICTED_LATENCY:-0}"
  WEIGHT_NO_HIT_LRU="${WEIGHT_NO_HIT_LRU:-0}"
  TOKENS_IN_FLIGHT_SCORER_THRESHOLD="${TOKENS_IN_FLIGHT_SCORER_THRESHOLD:-0}"
  PICKER_PLUGIN="weighted-random-picker"
  AFFINITY_GATE_TAU="" AFFINITY_GATE_TAU_GLOBAL="" OUTPUT_DIR=""
  SLO_TPOT_MS="0" SLO_TTFT_MS="0" STREAMING_MODE="false"
  SHEDDABLE="nonsheddable" FLOW_CONTROL="false"
  MAX_CONCURRENCY="8192" HEADROOM="3"

  for pair in $scenario; do
    key="${pair%%=*}"; val="${pair#*=}"
    case "$key" in
      run)         RUN_NAME="$val" ;;
      workload)    WORKLOAD="$val" ;;
      url)         BASE_URL="$val" ;;
      skip_config) SKIP_CONFIG="$val" ;;
      penalty)     MAX_TOKENS_IN_FLIGHT_PENALTY="$val" ;;
      ttft_penalty) MAX_TTFT_PENALTY_MS="$val" ;;
      prefix_weight|w_prefix) WEIGHT_PREFIX_CACHE="$val" ;;
      queue_weight|w_queue)  WEIGHT_QUEUE="$val" ;;
      kv_weight|w_kv)     WEIGHT_KV_UTIL="$val" ;;
      token_load_weight)    WEIGHT_TOKEN_LOAD="$val" ;;
      active_weight|w_active) WEIGHT_ACTIVE_REQUESTS="$val" ;;
      pred_weight|w_pred)   WEIGHT_PREDICTED_LATENCY="$val" ;;
      lru_weight)    WEIGHT_NO_HIT_LRU="$val" ;;
      token_load_threshold) TOKENS_IN_FLIGHT_SCORER_THRESHOLD="$val" ;;
      picker)        PICKER_PLUGIN="$val" ;;
      tau)           AFFINITY_GATE_TAU="$val" ;;
      tau_global)    AFFINITY_GATE_TAU_GLOBAL="$val" ;;
      output)        OUTPUT_DIR="$val" ;;
      slo_tpot)      SLO_TPOT_MS="$val" ;;
      slo_ttft)      SLO_TTFT_MS="$val" ;;
      stream)        STREAMING_MODE="$val" ;;
      shed)          SHEDDABLE="$val" ;;
      flow)          FLOW_CONTROL="$val" ;;
      max_concurrency) MAX_CONCURRENCY="$val" ;;
      headroom)      HEADROOM="$val" ;;
      *)             echo "WARNING: unknown key '$key'" >&2 ;;
    esac
  done

  export WEIGHT_PREFIX_CACHE WEIGHT_QUEUE WEIGHT_KV_UTIL WEIGHT_PREDICTED_LATENCY
  export WEIGHT_ACTIVE_REQUESTS
  export BASE_URL AFFINITY_GATE_TAU AFFINITY_GATE_TAU_GLOBAL
  export OUTPUT_DIR SLO_TPOT_MS SLO_TTFT_MS STREAMING_MODE SHEDDABLE
  export MAX_CONCURRENCY HEADROOM MAX_TOKENS_IN_FLIGHT_PENALTY MAX_TTFT_PENALTY_MS PICKER_PLUGIN
  export WEIGHT_TOKEN_LOAD WEIGHT_NO_HIT_LRU TOKENS_IN_FLIGHT_SCORER_THRESHOLD

  if [ -z "$WORKLOAD" ]; then
    echo "ERROR: scenario missing workload= key: $scenario" >&2
    exit 1
  fi

  if [ "$FLOW_CONTROL" = "true" ]; then
    export FLOW_CONTROL_GATE="- flowControl"
  else
    export FLOW_CONTROL_GATE=""
  fi

  echo "################################################################"
  echo "SCENARIO: $RUN_NAME  target=$BASE_URL  penalty=$MAX_TOKENS_IN_FLIGHT_PENALTY  ttft_penalty=$MAX_TTFT_PENALTY_MS  prefix_weight=$WEIGHT_PREFIX_CACHE  queue_weight=$WEIGHT_QUEUE  kv_weight=$WEIGHT_KV_UTIL  token_weight=$WEIGHT_TOKEN_LOAD  active_weight=$WEIGHT_ACTIVE_REQUESTS  pred_weight=$WEIGHT_PREDICTED_LATENCY  lru_weight=$WEIGHT_NO_HIT_LRU  token_threshold=$TOKENS_IN_FLIGHT_SCORER_THRESHOLD  picker=$PICKER_PLUGIN"
  echo "Multi-stage: stages and num_conversations from $WORKLOAD.yaml"
  echo "################################################################"

  if [ "$SKIP_CONFIG" = "false" ]; then
    apply_epp_config
  else
    echo "[SKIP] Skipping server configuration and restart."
  fi

  # --- PHASE 2: BUILD CONFIG + SUBMIT JOB ---
  echo "[3/4] Submitting inference-perf Jobs across c-levels: $CONCURRENCY_LEVELS"
  for conc in $CONCURRENCY_LEVELS; do
    export CONC="$conc"
    export SEED="$conc"
    
    export NUM_CONVERSATIONS="$conc"
    #export NUM_CONVERSATIONS=20
    export MAX_REQUESTS=$((NUM_CONVERSATIONS * TURNS_PER_CONV))
    #export MAX_REQUESTS=120

    export SUFFIX="$(date +%s)"
    export REPORT_PREFIX="${RUN_NAME}-c${conc}-${SUFFIX}"

    job_name="inference-perf-${SUFFIX}"
    echo "    → c=$conc  max_requests=$MAX_REQUESTS  seed=$SEED  target=$BASE_URL  job=$job_name"

    workload_yaml="$WORKLOAD_DIR/$WORKLOAD.yaml"
    if [ ! -f "$workload_yaml" ]; then
      echo "ERROR: workload not found: $workload_yaml" >&2
      exit 1
    fi
    CONFIG_FILE="$(mktemp -t inference-perf-config-${SUFFIX}.XXXXXX.yml)"
    envsubst < "$workload_yaml" > "$CONFIG_FILE"
    kubectl create configmap "inference-perf-config-${SUFFIX}" \
        --from-file=config.yml="$CONFIG_FILE" \
        --dry-run=client -o yaml | kubectl apply -f -
    envsubst < bench-job.yaml | kubectl apply -f -
    rm -f "$CONFIG_FILE"

    echo "      Waiting for completion ($job_name)..."
    kubectl wait --for=condition=complete "job/$job_name" --timeout=4h || {
      echo "      WARN: job did not complete cleanly; collecting logs and moving on"
      kubectl logs -l "job-name=$job_name" --tail=50 || true
    }

    # --- PHASE 3: CLEANUP ---
    echo "      Cleaning up $job_name..."
    kubectl delete job "$job_name" --wait=false >/dev/null 2>&1 || true
    kubectl delete configmap "inference-perf-config-${SUFFIX}" --wait=false >/dev/null 2>&1 || true
  done

  echo "Finished $RUN_NAME."
  sleep 5
done
