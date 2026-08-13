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
TEAMS=(premium standard batch)
MODELS=(a b)   # one topic/subscription per (team, model)
RESULT_TOPIC="results"

echo ">> Request topics + subscriptions (team × model)"
for t in "${TEAMS[@]}"; do
  for m in "${MODELS[@]}"; do
    topic="team-${t}-${m}-requests"
    sub="team-${t}-${m}-requests-sub"
    gcloud pubsub topics create "$topic" --project "$PROJECT_ID" 2>/dev/null \
      && echo "  created topic $topic" || echo "  topic $topic already exists"
    gcloud pubsub subscriptions create "$sub" --topic "$topic" --project "$PROJECT_ID" \
      --ack-deadline=60 2>/dev/null \
      && echo "  created subscription $sub" || echo "  subscription $sub already exists"
  done
done

echo ">> Result topic (+ inspection subscription)"
gcloud pubsub topics create "$RESULT_TOPIC" --project "$PROJECT_ID" 2>/dev/null \
  && echo "  created topic $RESULT_TOPIC" || echo "  topic $RESULT_TOPIC already exists"
gcloud pubsub subscriptions create "results-sub" --topic "$RESULT_TOPIC" --project "$PROJECT_ID" 2>/dev/null \
  && echo "  created subscription results-sub" || echo "  subscription results-sub already exists"

echo ">> Service account + IAM roles"
gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT_ID" \
  --display-name="llm-d async processor (demo)" 2>/dev/null \
  && echo "  created SA $SA_EMAIL" || echo "  SA $SA_EMAIL already exists"

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
