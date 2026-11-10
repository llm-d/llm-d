#!/usr/bin/env bash
# Provision the GCP Pub/Sub topics, subscriptions, and a service account for the
# multi-tenant async-processor demo.
#
# For the GCP Pub/Sub backend only. The Redis SortedSet backend needs no cloud
# resources — its queues are sorted-set keys created on first publish.
#
# Usage:
#   PROJECT_ID=my-project ./scripts/gcp-setup.sh
#   ./scripts/gcp-setup.sh my-project
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-${1:-}}"
[ -n "$PROJECT_ID" ] || { echo "PROJECT_ID required (env var or first arg)"; exit 1; }

SA_NAME="${SA_NAME:-async-processor}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
TEAMS=(premium standard batch)   # one topic/subscription per team
RESULT_TOPIC="results"

ensure_topic() {
  local topic="$1"
  if gcloud pubsub topics describe "$topic" --project "$PROJECT_ID" >/dev/null 2>&1; then
    echo "  topic $topic already exists"
  elif gcloud pubsub topics create "$topic" --project "$PROJECT_ID"; then
    echo "  created topic $topic"
  else
    echo "Error: failed to create or access topic $topic" >&2
    return 1
  fi
}

ensure_subscription() {
  local sub="$1" topic="$2"
  if gcloud pubsub subscriptions describe "$sub" --project "$PROJECT_ID" >/dev/null 2>&1; then
    echo "  subscription $sub already exists"
  elif gcloud pubsub subscriptions create "$sub" --topic "$topic" --project "$PROJECT_ID" \
    --ack-deadline=60; then
    echo "  created subscription $sub"
  else
    echo "Error: failed to create or access subscription $sub" >&2
    return 1
  fi
}

ensure_service_account() {
  if gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
    echo "  SA $SA_EMAIL already exists"
  elif gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT_ID" \
    --display-name="llm-d async processor (demo)"; then
    echo "  created SA $SA_EMAIL"
  else
    echo "Error: failed to create or access SA $SA_EMAIL" >&2
    return 1
  fi
}

echo ">> Request topics + subscriptions (one per team)"
for t in "${TEAMS[@]}"; do
  topic="team-${t}-requests"
  sub="team-${t}-requests-sub"
  ensure_topic "$topic"
  ensure_subscription "$sub" "$topic"
done

echo ">> Result topic (+ inspection subscription)"
ensure_topic "$RESULT_TOPIC"
ensure_subscription "results-sub" "$RESULT_TOPIC"

echo ">> Service account + IAM roles"
ensure_service_account

# pubsub.subscriber : consume from request subscriptions
# pubsub.publisher  : publish results to the result topic
# pubsub.viewer     : GetSubscription for the /readyz probe (roles/pubsub.subscriber
#                     does NOT include pubsub.subscriptions.get)
# monitoring.viewer : read the broker-backlog metric
for role in roles/pubsub.subscriber roles/pubsub.publisher roles/pubsub.viewer roles/monitoring.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" --role="$role" --condition=None >/dev/null
  echo "  bound $role"
done

cat <<EOF

Done.

Service account: ${SA_EMAIL}

Bind it to the async-processor Kubernetes SA via Workload Identity (GKE):
  gcloud iam service-accounts add-iam-policy-binding ${SA_EMAIL} \\
    --project ${PROJECT_ID} --role roles/iam.workloadIdentityUser \\
    --member "serviceAccount:${PROJECT_ID}.svc.id.goog[NAMESPACE/KSA_NAME]"
  kubectl annotate sa KSA_NAME -n NAMESPACE \\
    iam.gke.io/gcp-service-account=${SA_EMAIL}

The operator running the publisher needs roles/pubsub.publisher on the request
topics (your own gcloud credentials usually have this).
EOF
