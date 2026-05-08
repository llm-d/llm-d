# GCP Pub/Sub Implementation

This implementation uses GCP Pub/Sub as the backend for the request and result queues. It's ideal for cloud-native deployments on Google Cloud.

## Prerequisites

1. **GCP Project**: Ensure you have a GCP project with the Pub/Sub API enabled.
2. **Workload Identity**: Your Kubernetes service account must have permissions to publish to and subscribe from Pub/Sub topics.

## Topic setup, Configuration and Deployment:

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

## Configuration and Deployment

We provide a `values.yaml` for this implementation in `guides/asynchronous-processing/gcp-pubsub/values.yaml`.

Edit the `values.yaml` file with your specific GCP project and resources:

```yaml
ap:
  gcpPubSub:
    requestSubscriberId: "projects/<your-project>/subscriptions/async-proc-requests-sub"
    resultTopicId: "projects/<your-project>/topics/async-proc-results"
```

For deployment instructions, please refer to the [main README](../README.md#installation).

## Testing

1. **Publish a message**:
   ```bash
   gcloud pubsub topics publish $REQUEST_TOPIC_NAME --message='{"id" : "testmsg", "payload":{ "model":"your-model", "prompt":"Hi, good morning "}, "deadline" :"1999999999" }'
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