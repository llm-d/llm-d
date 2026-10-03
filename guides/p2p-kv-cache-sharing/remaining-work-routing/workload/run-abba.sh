#!/usr/bin/env bash
# Run one or more counterbalanced C64/900s AgentX blocks.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
CAMPAIGN_ID=${CAMPAIGN_ID:-c64-$(date -u +%Y%m%d-%H%M%S)}
BLOCKS=${BLOCKS:-1}

if [[ ! "$BLOCKS" =~ ^[1-9][0-9]*$ ]]; then
  echo "BLOCKS must be a positive integer" >&2
  exit 2
fi
sequence=(baseline candidate candidate baseline)
for block in $(seq 1 "$BLOCKS"); do
  position=0
  for arm in "${sequence[@]}"; do
    position=$((position + 1))
    run_id=$(printf '%s-b%02d-p%02d-%s' \
      "$CAMPAIGN_ID" "$block" "$position" "$arm")
    echo
    echo "=== ${run_id}"
    RUN_ID="$run_id" "$HERE/run-agentx.sh" "$arm"
  done
done

echo
echo "campaign complete: ${CAMPAIGN_ID} (${BLOCKS} ABBA block(s))"
