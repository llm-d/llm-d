#!/usr/bin/env bash
set -Eeuo pipefail

# -----------------------------------------------------------------------------
# healthcheck.sh — Production smoke-test for llm-d deployments
#
# Verifies an llm-d deployment is healthy by probing:
#   - (optional) /health
#   - /v1/models
#   - /v1/completions and/or /v1/chat/completions (auto fallback)
#
# It measures latency, prints a structured report, and exits non-zero on failure.
#
# Notes:
# - curl is required.
# - jq is optional (enables JSON parsing, model auto-discovery, JSON output, and
#   response structure validation). Without jq, the script still performs an
#   HTTP-level smoke test, but MODEL_ID is required for inference requests.
# -----------------------------------------------------------------------------

# ── Defaults ─────────────────────────────────────────────────────────────────
ENDPOINT="${ENDPOINT:-http://localhost:8000}"
MODEL_ID="${MODEL_ID:-}"
TIMEOUT="${TIMEOUT:-30}"
MAX_LATENCY="${MAX_LATENCY:-0}"
OUTPUT_FORMAT="text"   # text|json
API_MODE="auto"        # auto|completions|chat
REQUIRE_HEALTH="false" # if true, /health must return 200 (otherwise warning)
PROMPT="San Francisco is a"
MAX_TOKENS=8

# ── Help ─────────────────────────────────────────────────────────────────────
show_help() {
  cat <<'EOF'
Usage: healthcheck.sh [OPTIONS]

Smoke-test an llm-d deployment and print a structured health report.

Options:
  -e, --endpoint URL         Base URL of the vLLM / gateway endpoint
                             (default: $ENDPOINT or http://localhost:8000)
  -m, --model MODEL_ID       Model to query; auto-discovered if omitted (jq required)
  -t, --timeout SECONDS      HTTP request timeout (default: 30)
  -l, --max-latency MS       Fail if inference latency exceeds this (0=skip)
  -o, --output FORMAT        Output format: text | json (default: text; json requires jq)
      --api MODE             Which OpenAI-compatible endpoint to probe:
                               auto | completions | chat (default: auto)
      --require-health       Treat /health non-200 as failure (default: warn)
  -h, --help                 Show this help and exit

Environment variables:
  ENDPOINT        Same as --endpoint
  MODEL_ID        Same as --model
  TIMEOUT         Same as --timeout
  MAX_LATENCY     Same as --max-latency

Examples:
  # Port-forwarded gateway
  ./healthcheck.sh -e http://localhost:8000

  # Force chat-completions
  ./healthcheck.sh -e http://gateway:80 --api chat

  # Remote endpoint with latency threshold
  ./healthcheck.sh -e http://inference.example.com -l 5000

  # JSON output for CI pipelines (requires jq)
  ./healthcheck.sh -o json
EOF
  exit 0
}

# ── Flag parsing ─────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    -e|--endpoint)        ENDPOINT="$2"; shift 2 ;;
    -m|--model)           MODEL_ID="$2"; shift 2 ;;
    -t|--timeout)         TIMEOUT="$2"; shift 2 ;;
    -l|--max-latency)     MAX_LATENCY="$2"; shift 2 ;;
    -o|--output)          OUTPUT_FORMAT="$2"; shift 2 ;;
    --api)                API_MODE="$2"; shift 2 ;;
    --require-health)     REQUIRE_HEALTH="true"; shift 1 ;;
    -h|--help)            show_help ;;
    *) echo "Unknown option: $1" >&2; show_help ;;
  esac
done

# Strip trailing slash
ENDPOINT="${ENDPOINT%/}"

# ── Dependencies ─────────────────────────────────────────────────────────────
if ! command -v curl &>/dev/null; then
  echo "Error: 'curl' is required but not found in PATH." >&2
  exit 1
fi

HAS_JQ="false"
if command -v jq &>/dev/null; then
  HAS_JQ="true"
fi

if [[ "$OUTPUT_FORMAT" == "json" && "$HAS_JQ" != "true" ]]; then
  echo "Error: JSON output (-o json) requires 'jq'." >&2
  exit 1
fi

# ── Utility: epoch milliseconds ──────────────────────────────────────────────
now_ms() {
  if date +%s%3N &>/dev/null 2>&1 && [[ $(date +%s%3N) =~ ^[0-9]+$ ]]; then
    date +%s%3N
  else
    # macOS fallback: second-precision
    echo "$(( $(date +%s) * 1000 ))"
  fi
}

json_escape() {
  # Minimal JSON string escaping (quotes, backslashes, newlines, tabs)
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

# ── Collectors ───────────────────────────────────────────────────────────────
CHECKS_TOTAL=0
CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNED=0
FAILURES=""
WARNINGS=""

pass() { CHECKS_TOTAL=$((CHECKS_TOTAL + 1)); CHECKS_PASSED=$((CHECKS_PASSED + 1)); }
warn() {
  CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
  CHECKS_WARNED=$((CHECKS_WARNED + 1))
  WARNINGS="${WARNINGS}  - $1\n"
}
fail() {
  CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
  CHECKS_FAILED=$((CHECKS_FAILED + 1))
  FAILURES="${FAILURES}  - $1\n"
}

# ── Check 1: /health endpoint (optional) ─────────────────────────────────────
check_health() {
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time "$TIMEOUT" "${ENDPOINT}/health" 2>/dev/null) || http_code="000"

  if [[ "$http_code" == "200" ]]; then
    pass
  else
    # Many deployments don't expose /health on the gateway. Default: warn unless
    # explicitly required.
    if [[ "$REQUIRE_HEALTH" == "true" ]]; then
      fail "/health returned HTTP ${http_code}"
    else
      warn "/health returned HTTP ${http_code} (not required)"
    fi
  fi
  echo "$http_code"
}

# ── Check 2: /v1/models endpoint (readiness + optional model discovery) ──────
check_models() {
  local response http_code body
  response=$(curl -s -w "\n%{http_code}" \
    --max-time "$TIMEOUT" "${ENDPOINT}/v1/models" 2>/dev/null) || true

  http_code=$(echo "$response" | tail -n1)
  body=$(echo "$response" | sed '$d')

  if [[ "$http_code" != "200" ]]; then
    fail "/v1/models returned HTTP ${http_code}"
    echo "000|"
    return
  fi

  local model_count="-"
  if [[ "$HAS_JQ" == "true" ]]; then
    model_count=$(echo "$body" | jq -r '.data | length' 2>/dev/null || echo "0")
    if [[ "$model_count" == "0" ]]; then
      fail "/v1/models returned 0 models"
      echo "${http_code}|0"
      return
    fi

    # Auto-discover model if not set
    if [[ -z "$MODEL_ID" ]]; then
      MODEL_ID=$(echo "$body" | jq -r '.data[0].id' 2>/dev/null || true)
    fi
  else
    # No jq: can't parse model list. Still counts as readiness check.
    if [[ -z "$MODEL_ID" ]]; then
      warn "/v1/models reachable, but MODEL_ID not set and jq missing (cannot auto-discover)"
    fi
  fi

  pass
  echo "${http_code}|${model_count}"
}

# ── Internal: POST helper (returns "HTTP|LATENCY|PATH_USED") ─────────────────
post_inference() {
  local path="$1"
  local payload="$2"

  local start_ms end_ms latency_ms
  start_ms=$(now_ms)

  local response http_code body
  response=$(curl -s -w "\n%{http_code}" \
    -X POST "${ENDPOINT}${path}" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    --max-time "$TIMEOUT" 2>/dev/null) || true

  end_ms=$(now_ms)
  latency_ms=$((end_ms - start_ms))

  http_code=$(echo "$response" | tail -n1)
  body=$(echo "$response" | sed '$d')

  echo "${http_code}|${latency_ms}|${path}|${body}"
}

# ── Check 3: Inference (/v1/completions and/or /v1/chat/completions) ─────────
check_inference() {
  if [[ -z "$MODEL_ID" && "$HAS_JQ" != "true" ]]; then
    fail "Inference skipped — MODEL_ID is required when jq is not available"
    echo "000|0|/v1/completions"
    return
  fi

  local used_path="/v1/completions"
  local payload
  if [[ "$API_MODE" == "chat" ]]; then
    used_path="/v1/chat/completions"
  elif [[ "$API_MODE" == "completions" ]]; then
    used_path="/v1/completions"
  fi

  # Build payload
  if [[ "$HAS_JQ" == "true" ]]; then
    if [[ "$used_path" == "/v1/chat/completions" ]]; then
      payload=$(jq -n \
        --arg model "$MODEL_ID" \
        --arg content "$PROMPT" \
        --argjson max_tokens "$MAX_TOKENS" \
        '{model: $model, messages: [{role:"user", content:$content}], max_tokens: $max_tokens}')
    else
      payload=$(jq -n \
        --arg model "$MODEL_ID" \
        --arg prompt "$PROMPT" \
        --argjson max_tokens "$MAX_TOKENS" \
        '{model: $model, prompt: $prompt, max_tokens: $max_tokens}')
    fi
  else
    # Minimal JSON without jq
    local esc_prompt
    esc_prompt="$(json_escape "$PROMPT")"
    if [[ "$used_path" == "/v1/chat/completions" ]]; then
      payload="{\"model\":\"$MODEL_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"$esc_prompt\"}],\"max_tokens\":$MAX_TOKENS}"
    else
      payload="{\"model\":\"$MODEL_ID\",\"prompt\":\"$esc_prompt\",\"max_tokens\":$MAX_TOKENS}"
    fi
  fi

  local result http_code latency_ms path body
  result="$(post_inference "$used_path" "$payload")"
  http_code="${result%%|*}"; result="${result#*|}"
  latency_ms="${result%%|*}"; result="${result#*|}"
  path="${result%%|*}"; body="${result#*|}"

  # Auto-fallback for API_MODE=auto
  if [[ "$API_MODE" == "auto" && "$http_code" != "200" ]]; then
    if [[ "$path" == "/v1/completions" ]]; then
      used_path="/v1/chat/completions"
    else
      used_path="/v1/completions"
    fi

    result="$(post_inference "$used_path" "$payload")"
    http_code="${result%%|*}"; result="${result#*|}"
    latency_ms="${result%%|*}"; result="${result#*|}"
    path="${result%%|*}"; body="${result#*|}"
  fi

  if [[ "$http_code" != "200" ]]; then
    fail "${path} returned HTTP ${http_code}"
    echo "${http_code}|${latency_ms}|${path}"
    return
  fi

  # Response structure validation (only if jq available)
  if [[ "$HAS_JQ" == "true" ]]; then
    local has_choices
    has_choices=$(echo "$body" | jq -r 'has("choices")' 2>/dev/null || echo "false")
    if [[ "$has_choices" != "true" ]]; then
      fail "${path} response missing 'choices' field"
      echo "${http_code}|${latency_ms}|${path}"
      return
    fi
  fi

  # Latency threshold check
  if [[ "$MAX_LATENCY" -gt 0 && "$latency_ms" -gt "$MAX_LATENCY" ]]; then
    fail "${path} latency ${latency_ms}ms exceeds threshold ${MAX_LATENCY}ms"
    echo "${http_code}|${latency_ms}|${path}"
    return
  fi

  pass
  echo "${http_code}|${latency_ms}|${path}"
}

# ── Report: text ─────────────────────────────────────────────────────────────
report_text() {
  local health_code="$1" models_result="$2" infer_result="$3"
  local models_code models_count infer_code infer_latency infer_path

  models_code="${models_result%%|*}"
  models_count="${models_result#*|}"

  infer_code="${infer_result%%|*}"; infer_result="${infer_result#*|}"
  infer_latency="${infer_result%%|*}"; infer_path="${infer_result#*|}"

  local status="HEALTHY"
  [[ "$CHECKS_FAILED" -gt 0 ]] && status="UNHEALTHY"

  echo ""
  echo "╔══════════════════════════════════════════╗"
  echo "║         llm-d Health Check Report        ║"
  echo "╠══════════════════════════════════════════╣"
  printf "║  Status:    %-28s ║\n" "$status"
  printf "║  Endpoint:  %-28s ║\n" "$ENDPOINT"
  printf "║  Model:     %-28s ║\n" "${MODEL_ID:-(none)}"
  printf "║  API:       %-28s ║\n" "$API_MODE"
  printf "║  jq:        %-28s ║\n" "$HAS_JQ"
  echo "╠══════════════════════════════════════════╣"
  printf "║  /health          HTTP %-17s ║\n" "$health_code"
  printf "║  /v1/models       HTTP %-4s  count=%-5s ║\n" "$models_code" "${models_count:--}"
  printf "║  %-15s HTTP %-4s  %-5s ms   ║\n" "$infer_path" "$infer_code" "${infer_latency:--}"
  echo "╠══════════════════════════════════════════╣"
  printf "║  Checks: %d passed, %d failed, %d warned   ║\n" \
    "$CHECKS_PASSED" "$CHECKS_FAILED" "$CHECKS_WARNED"
  echo "╚══════════════════════════════════════════╝"

  if [[ -n "$FAILURES" ]]; then
    echo ""
    echo "Failures:"
    echo -e "$FAILURES"
  fi

  if [[ -n "$WARNINGS" ]]; then
    echo ""
    echo "Warnings:"
    echo -e "$WARNINGS"
  fi
}

# ── Report: json (requires jq) ───────────────────────────────────────────────
report_json() {
  local health_code="$1" models_result="$2" infer_result="$3"
  local models_code models_count infer_code infer_latency infer_path

  models_code="${models_result%%|*}"
  models_count="${models_result#*|}"

  infer_code="${infer_result%%|*}"; infer_result="${infer_result#*|}"
  infer_latency="${infer_result%%|*}"; infer_path="${infer_result#*|}"

  local status="healthy"
  [[ "$CHECKS_FAILED" -gt 0 ]] && status="unhealthy"

  jq -n \
    --arg status "$status" \
    --arg endpoint "$ENDPOINT" \
    --arg model "${MODEL_ID:-}" \
    --arg api_mode "$API_MODE" \
    --arg jq_present "$HAS_JQ" \
    --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --argjson checks_passed "$CHECKS_PASSED" \
    --argjson checks_failed "$CHECKS_FAILED" \
    --argjson checks_warned "$CHECKS_WARNED" \
    --arg health_http "$health_code" \
    --arg models_http "$models_code" \
    --arg models_count "${models_count:--}" \
    --arg infer_http "$infer_code" \
    --arg infer_latency_ms "${infer_latency:--}" \
    --arg infer_path "$infer_path" \
    --arg failures "${FAILURES:-}" \
    --arg warnings "${WARNINGS:-}" \
    '{
      status: $status,
      timestamp: $ts,
      endpoint: $endpoint,
      model: $model,
      api_mode: $api_mode,
      jq_present: ($jq_present == "true"),
      checks: { passed: $checks_passed, failed: $checks_failed, warned: $checks_warned },
      results: {
        health:      { http_status: $health_http },
        models:      { http_status: $models_http, model_count: $models_count },
        inference:   { path: $infer_path, http_status: $infer_http, latency_ms: $infer_latency_ms }
      },
      diagnostics: {
        failures: ( $failures | split("\n") | map(select(length>0)) ),
        warnings: ( $warnings | split("\n") | map(select(length>0)) )
      }
    }'
}

# ── Main ─────────────────────────────────────────────────────────────────────
health_code="$(check_health)"
models_result="$(check_models)"
infer_result="$(check_inference)"

case "$OUTPUT_FORMAT" in
  json) report_json "$health_code" "$models_result" "$infer_result" ;;
  text) report_text "$health_code" "$models_result" "$infer_result" ;;
  *)    echo "Unknown output format: $OUTPUT_FORMAT" >&2; exit 1 ;;
esac

# Bash exit codes are 0–255; keep it conventional.
if [[ "$CHECKS_FAILED" -gt 0 ]]; then
  exit 1
fi
exit 0
