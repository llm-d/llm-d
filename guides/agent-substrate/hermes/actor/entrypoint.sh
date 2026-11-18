#!/bin/sh
# Renders Hermes config from ActorTemplate environment variables, starts
# `hermes gateway run`, and hands readiness gating to `warmup.py` so GKE Agent
# Substrate captures the golden gVisor snapshot after first-turn warmup (~110 ms
# first real turn vs. ~3.4 s cold start).
set -eu

: "${API_SERVER_KEY:?API_SERVER_KEY must be set (at least 16 characters)}"
export MODEL_NAME="${MODEL_NAME:-Qwen/Qwen3-32B}"
export MODEL_BASE_URL="${MODEL_BASE_URL:?MODEL_BASE_URL must be set, e.g. http://agent-substrate-epp.llm-d-agent-substrate.svc.cluster.local:80/v1}"
export MAX_TOKENS="${MAX_TOKENS:-64}"
export CONTEXT_LENGTH="${CONTEXT_LENGTH:-65536}"

mkdir -p "$HERMES_HOME"
python3 - <<'EOF'
import os
import string

with open("/opt/hermes/config.yaml.tmpl", encoding="utf-8") as src_f:
    rendered = string.Template(src_f.read()).substitute(os.environ)
dst = os.path.join(os.environ["HERMES_HOME"], "config.yaml")
with open(dst, "w", encoding="utf-8") as dst_f:
    dst_f.write(rendered)
EOF
[ -f /data/AGENTS.md ] || cp /opt/hermes/hermes-persona.md /data/AGENTS.md

hermes gateway run &
exec python3 /opt/hermes/warmup.py "$!"
