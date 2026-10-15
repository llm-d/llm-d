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

echo ">> Deleting subscriptions"
for t in "${TEAMS[@]}"; do
  gcloud pubsub subscriptions delete "team-${t}-requests-sub" --project "$PROJECT_ID" --quiet 2>/dev/null \
    && echo "  deleted team-${t}-requests-sub" || true
done
gcloud pubsub subscriptions delete "results-sub" --project "$PROJECT_ID" --quiet 2>/dev/null || true

echo ">> Deleting topics"
for t in "${TEAMS[@]}"; do
  gcloud pubsub topics delete "team-${t}-requests" --project "$PROJECT_ID" --quiet 2>/dev/null \
    && echo "  deleted team-${t}-requests" || true
done
gcloud pubsub topics delete "results" --project "$PROJECT_ID" --quiet 2>/dev/null || true

if [ "${DELETE_SA:-0}" = "1" ]; then
  # Deleting a service account leaves its project-level bindings behind (as
  # deleted:serviceAccount:... members), so remove the roles gcp-setup.sh granted first.
  echo ">> Removing project IAM bindings"
  for role in roles/pubsub.subscriber roles/pubsub.publisher roles/pubsub.viewer roles/monitoring.viewer; do
    gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${SA_EMAIL}" --role="$role" --condition=None >/dev/null 2>&1 \
      && echo "  removed $role" || echo "  $role was not bound"
  done
  echo ">> Deleting service account"
  gcloud iam service-accounts delete "$SA_EMAIL" --project "$PROJECT_ID" --quiet 2>/dev/null \
    && echo "  deleted $SA_EMAIL" || true
fi

echo "Done."
