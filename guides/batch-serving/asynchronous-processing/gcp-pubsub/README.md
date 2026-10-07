# GCP Pub/Sub Implementation

This implementation uses GCP Pub/Sub as the backend for the request and result queues. It's ideal for cloud-native deployments on Google Cloud.

## Prerequisites

1. **GCP Project**: Ensure you have a GCP project with the Pub/Sub API enabled.
2. **Workload Identity**: the chart runs the processor under a Kubernetes service account named after the Helm release (`llm-d-async` in the namespace you install into) and does not annotate it. Grant that identity access to Pub/Sub as described in [Grant the processor access to Pub/Sub](#grant-the-processor-access-to-pubsub) below.

## Topic setup, Configuration and Deployment

### Topic Setup

We recommend setting-up a <u>topic per model+priority</u>, i.e., per inference objective.

For a simple one model & one usecase create a single topic.

```bash
export REQUEST_TOPIC_NAME=async-proc-requests # choose topic name for requests
gcloud pubsub topics create $REQUEST_TOPIC_NAME
```

For each request topic create a **subscription** with the following configurations:

- Exactly-once delivery.
- Retries with exponential backoff.
- Dead Letter Queue (DLQ).

<u>Note:</u> If DLQ is NOT configured for the request topic. Retried messages will be counted multiple times in the <i>number_of_requests</i> metric.

Example:

```bash
export SUBSCRIPTION_NAME=async-proc-requests-sub # choose subscription name for each request topic
export DLQ_NAME=async-proc-requests-dlq # choose DLQ name
export RESULT_TOPIC_NAME=async-proc-results # choose topic name for results
```

```bash
gcloud pubsub topics create $DLQ_NAME
gcloud pubsub topics create $RESULT_TOPIC_NAME
```

```bash
# create subscription for DLQ topic so messages will not get lost
gcloud pubsub subscriptions create sub-$DLQ_NAME \
    --topic=$DLQ_NAME
```

```bash
# create subscription for request topic
gcloud pubsub subscriptions create $SUBSCRIPTION_NAME \
    --topic=$REQUEST_TOPIC_NAME \
    --dead-letter-topic=$DLQ_NAME \
    --max-delivery-attempts=35   \
    --enable-exactly-once-delivery
```

Pub/Sub forwards to the dead-letter topic as its own service agent, so that agent must be allowed to publish to the DLQ topic and to subscribe to the request subscription. `gcloud` only warns when these grants are missing, and undeliverable messages are then never dead-lettered:

```bash
export PROJECT_ID=$(gcloud config get-value project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
export PUBSUB_SA="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

gcloud pubsub topics add-iam-policy-binding $DLQ_NAME \
    --member="serviceAccount:${PUBSUB_SA}" --role=roles/pubsub.publisher
gcloud pubsub subscriptions add-iam-policy-binding $SUBSCRIPTION_NAME \
    --member="serviceAccount:${PUBSUB_SA}" --role=roles/pubsub.subscriber
```

### Grant the processor access to Pub/Sub

The processor needs the following roles:

| Role | Used for |
| --- | --- |
| `roles/pubsub.subscriber` | pulling requests from `$SUBSCRIPTION_NAME` |
| `roles/pubsub.publisher` | publishing results to `$RESULT_TOPIC_NAME` |
| `roles/pubsub.viewer` | the readiness probe's `GetSubscription` on an idle subscription (a permission-denied answer is tolerated, but a granted viewer role gives you a real probe) |
| `roles/monitoring.viewer` | the `llm_d_async_async_broker_backlog` gauge, which reads the subscription backlog from Cloud Monitoring; without it the gauge is absent and `llm_d_async_async_broker_backlog_source_available` stays `0` |

With [Workload Identity Federation for GKE](https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity) you grant them to the Kubernetes service account's principal directly; no Google service account or annotation is needed. `NAMESPACE` must be the namespace you pass to `helm install` in the [main README](../README.md#installation):

```bash
export NAMESPACE=llm-d-async
export KSA_PRINCIPAL="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog/subject/ns/${NAMESPACE}/sa/llm-d-async"

for role in roles/pubsub.subscriber roles/pubsub.publisher roles/pubsub.viewer roles/monitoring.viewer; do
  gcloud projects add-iam-policy-binding ${PROJECT_ID} \
      --member="${KSA_PRINCIPAL}" --role="${role}" --condition=None
done
```

If you would rather use a Google service account, give it the same roles, bind it with `roles/iam.workloadIdentityUser` for `${PROJECT_ID}.svc.id.goog[${NAMESPACE}/llm-d-async]`, and annotate the Kubernetes service account after the Helm install:

```bash
kubectl annotate serviceaccount llm-d-async -n ${NAMESPACE} \
    iam.gke.io/gcp-service-account=<gsa-name>@${PROJECT_ID}.iam.gserviceaccount.com
```

The multi-tenant guide's [`gcp-setup.sh`](../multitenant/scripts/gcp-setup.sh) scripts this service-account variant.

## Configuration and Deployment

We provide a `values.yaml` for this implementation in `guides/batch-serving/asynchronous-processing/gcp-pubsub/values.yaml`.

Edit the `values.yaml` file with your specific GCP project and resources:

```yaml
ap:
  transport: "gcp-pubsub"
  transportConfig:
    project_id: "<your-project>"
    result_topic_id: "projects/<your-project>/topics/async-proc-results"
    topics:
      - subscriber_id: "projects/<your-project>/subscriptions/async-proc-requests-sub"
        request_path_url: "/v1/completions"
        igw_base_url: "http://<igw-host>:80"
```

For deployment instructions, please refer to the [main README](../README.md#installation).

## Testing

1. **Publish a message**:

   Publish the request on its own — on Pub/Sub, unlike Redis, it is **not** wrapped
   in an `InternalRequest` envelope. The consumer builds that itself from the Pub/Sub
   message. Note that `deadline` and `created` are Unix-seconds **numbers**, not
   strings — a quoted `deadline` fails to decode.

   ```bash
   gcloud pubsub topics publish $REQUEST_TOPIC_NAME --message='{"id":"testmsg","created":1700000000,"deadline":1999999999,"payload":{"model":"your-model","prompt":"Hi, good morning"}}'
   ```

2. **Pull from results subscription**:
   First, create a subscription for the results topic if you haven't already:

   ```bash
   gcloud pubsub subscriptions create async-proc-results-sub --topic=$RESULT_TOPIC_NAME
   ```

   Then pull the result:

   ```bash
   gcloud pubsub subscriptions pull async-proc-results-sub --auto-ack --limit=1
   ```
