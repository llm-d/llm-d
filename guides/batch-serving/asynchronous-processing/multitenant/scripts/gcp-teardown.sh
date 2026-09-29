#!/usr/bin/env bash
# Tear down the GCP resources created by gcp-setup.sh (GCP Pub/Sub backend only).
#
# Usage:
#   PROJECT_ID=my-project ./scripts/gcp-teardown.sh           # topics + subscriptions
#   PROJECT_ID=my-project DELETE_SA=1 ./scripts/gcp-teardown.sh  # also remove the SA
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-${1:-}}"
[ -n "$PROJECT_ID" ] || { echo "PROJECT_ID required (env var or first arg)"; exit 1; }

SA_NAME="${SA_NAME:-async-processor}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
TEAMS=(premium standard batch)
MODELS=(a b)

echo ">> Deleting subscriptions"
for t in "${TEAMS[@]}"; do
  for m in "${MODELS[@]}"; do
    gcloud pubsub subscriptions delete "team-${t}-${m}-requests-sub" --project "$PROJECT_ID" --quiet 2>/dev/null \
      && echo "  deleted team-${t}-${m}-requests-sub" || true
  done
done
gcloud pubsub subscriptions delete "results-sub" --project "$PROJECT_ID" --quiet 2>/dev/null || true

echo ">> Deleting topics"
for t in "${TEAMS[@]}"; do
  for m in "${MODELS[@]}"; do
    gcloud pubsub topics delete "team-${t}-${m}-requests" --project "$PROJECT_ID" --quiet 2>/dev/null \
      && echo "  deleted team-${t}-${m}-requests" || true
  done
done
gcloud pubsub topics delete "results" --project "$PROJECT_ID" --quiet 2>/dev/null || true

if [ "${DELETE_SA:-0}" = "1" ]; then
  echo ">> Deleting service account"
  gcloud iam service-accounts delete "$SA_EMAIL" --project "$PROJECT_ID" --quiet 2>/dev/null \
    && echo "  deleted $SA_EMAIL" || true
  echo "  (project-level IAM bindings for the SA are removed with the SA)"
fi

echo "Done."
