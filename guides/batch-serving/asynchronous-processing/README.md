# Asynchronous Processing with Async Processor

The [Async Processor](https://github.com/llm-d/llm-d-async) provides a way to process inference requests asynchronously using a queue-based architecture. This is ideal for latency-insensitive workloads or for filling "slack" capacity in your inference pool.

This page deploys the Async Processor for a single model. The queue backend — GCP Pub/Sub (default) or a Redis Sorted Set — is chosen in [Installation](#step-2-set-up-the-queue-backend), in tabs.

The **[Multi-tenant guide](./multitenant/README.md)** builds on it: the **advanced** setup of **team × tier × model** with per-team reserved/overflow quota (classifying `redis-quota`), tier-priority dispatch, and per-model saturation back-off across two `InferencePool`s. It runs on either queue backend.

> [!NOTE]
> For production sizing, scaling, and container-resource guidance, see [Async Processor Operations](../../../docs/operations/components/async-processor.md).

## Overview

This guide deploys the Async Processor (Helm chart `llm-d-async`) in front of an existing [optimized baseline](../../optimized-baseline/README.md) stack. It consumes requests from one message queue — GCP Pub/Sub by default, or a Redis Sorted Set, set up in [Step 2](#step-2-set-up-the-queue-backend) — and dispatches them to the llm-d Router, either directly to the router Service (Standalone mode) or through the Gateway (Gateway mode).

Clients enqueue work and read results from the result topic or list later instead of holding an HTTP connection open, and retries happen without touching real-time traffic. Dispatch gates, set in the Helm values, decide when queued work is released — for example only while the model servers have spare capacity.

For how the processor works — dispatch gates, worker pools, merge policies, retries and deadlines, and queue semantics — see the [Async Processor Architecture](../../../docs/architecture/advanced/batch/async-processor.md).

### When to Use This Path

- **Batch Inference**: Processing large datasets where completion time is measured in minutes or hours rather than milliseconds.
- **Slack Capacity Filling**: Using idle GPU cycles between real-time request spikes to perform background tasks like document summarization or embedding generation.
- **Offline Evaluation**: Running model evaluation pipelines without competing for production resources.

## Prerequisites

Before installing Async Processor, ensure you have:

1. **Kubernetes cluster**: A running Kubernetes cluster (v1.31+).
   - For local development, you can use **Kind** or **Minikube**.
   - For production, GKE, AKS, or OpenShift are supported.
2. **Gateway control plane** (Gateway mode only): if you run the optimized baseline behind a Gateway, configure and deploy your [Gateway control plane](../../../docs/infrastructure/gateway/README.md) (e.g., Istio) before installation. In Standalone mode the Async Processor dispatches to the llm-d Router directly and no Gateway is needed.
3. **llm-d Inference Stack**: Async Processor requires an existing [optimized baseline](../../optimized-baseline/README.md) stack to dispatch requests to.

## Installation

Async Processor can be installed via Helm. We recommend following the pattern used in the [optimized baseline](../../optimized-baseline/README.md) guide.

#### Step 1: Deploy llm-d Router

Apply the [optimized baseline](../../optimized-baseline/README.md) guide and get the llm-d Router's IP address:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
# If using Standalone Mode:
export IP=$(kubectl get service optimized-baseline-epp -n llm-d-optimized-baseline -o jsonpath='{.spec.clusterIP}')

# If using Gateway Mode:
export IP=$(kubectl get gateway llm-d-inference-gateway -n llm-d-optimized-baseline -o jsonpath='{.status.addresses[0].value}')
```

#### Step 2: Set up the Queue Backend

Set up one queue backend and fill in its `values.yaml`; [Step 3](#step-3-deploy-async-processor) installs the chart with it.

<!-- tabs:start group=queue -->
<details open>
<summary><b>GCP Pub/Sub</b></summary>

GCP Pub/Sub is the default backend: cloud-native, scalable messaging on Google Cloud. You need a GCP project with the Pub/Sub API enabled. The chart runs the processor under a Kubernetes service account named after the Helm release (`llm-d-async` in the namespace you install into) and does not annotate it; grant that identity access as described below.

**Topics and subscription.** We recommend a topic *per model and priority*, i.e. per inference objective. For one model and one use case, create a single request topic, and a result topic and a dead-letter topic (DLQ):

<!-- llm-d-cicd:skip start -->
```bash
export REQUEST_TOPIC_NAME=async-proc-requests   # request topic
export SUBSCRIPTION_NAME=async-proc-requests-sub # subscription for the request topic
export DLQ_NAME=async-proc-requests-dlq          # dead-letter topic
export RESULT_TOPIC_NAME=async-proc-results      # result topic

gcloud pubsub topics create $REQUEST_TOPIC_NAME
gcloud pubsub topics create $DLQ_NAME
gcloud pubsub topics create $RESULT_TOPIC_NAME
```
<!-- llm-d-cicd:skip end -->

Give each request topic a subscription with exactly-once delivery, retries with exponential backoff, and a DLQ. Without a DLQ, retried messages are counted multiple times in the *number_of_requests* metric. Also subscribe to the DLQ topic so dead-lettered messages are not lost:

<!-- llm-d-cicd:skip start -->
```bash
gcloud pubsub subscriptions create sub-$DLQ_NAME \
    --topic=$DLQ_NAME

gcloud pubsub subscriptions create $SUBSCRIPTION_NAME \
    --topic=$REQUEST_TOPIC_NAME \
    --dead-letter-topic=$DLQ_NAME \
    --max-delivery-attempts=35   \
    --enable-exactly-once-delivery
```
<!-- llm-d-cicd:skip end -->

Pub/Sub forwards to the dead-letter topic as its own service agent, so that agent must be allowed to publish to the DLQ topic and to subscribe to the request subscription. `gcloud` only warns when these grants are missing, and undeliverable messages are then never dead-lettered:

<!-- llm-d-cicd:skip start -->
```bash
export PROJECT_ID=$(gcloud config get-value project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
export PUBSUB_SA="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

gcloud pubsub topics add-iam-policy-binding $DLQ_NAME \
    --member="serviceAccount:${PUBSUB_SA}" --role=roles/pubsub.publisher
gcloud pubsub subscriptions add-iam-policy-binding $SUBSCRIPTION_NAME \
    --member="serviceAccount:${PUBSUB_SA}" --role=roles/pubsub.subscriber
```
<!-- llm-d-cicd:skip end -->

**Grant the processor access to Pub/Sub.** The processor needs the following roles:

| Role | Used for |
| --- | --- |
| `roles/pubsub.subscriber` | pulling requests from `$SUBSCRIPTION_NAME` |
| `roles/pubsub.publisher` | publishing results to `$RESULT_TOPIC_NAME` |
| `roles/pubsub.viewer` | the readiness probe's `GetSubscription` on an idle subscription (a permission-denied answer is tolerated, but a granted viewer role gives you a real probe) |
| `roles/monitoring.viewer` | the `llm_d_async_async_broker_backlog` gauge, which reads the subscription backlog from Cloud Monitoring; without it the gauge is absent and `llm_d_async_async_broker_backlog_source_available` stays `0` |

With [Workload Identity Federation for GKE](https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity) you grant them to the Kubernetes service account's principal directly; no Google service account or annotation is needed. `NAMESPACE` must be the namespace you install into in [Step 3](#step-3-deploy-async-processor):

<!-- llm-d-cicd:skip start -->
```bash
export NAMESPACE=llm-d-async
export KSA_PRINCIPAL="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog/subject/ns/${NAMESPACE}/sa/llm-d-async"

for role in roles/pubsub.subscriber roles/pubsub.publisher roles/pubsub.viewer roles/monitoring.viewer; do
  gcloud projects add-iam-policy-binding ${PROJECT_ID} \
      --member="${KSA_PRINCIPAL}" --role="${role}" --condition=None
done
```
<!-- llm-d-cicd:skip end -->

If you would rather use a Google service account, give it the same roles, bind it with `roles/iam.workloadIdentityUser` for `${PROJECT_ID}.svc.id.goog[${NAMESPACE}/llm-d-async]`, and annotate the Kubernetes service account after the Helm install in Step 3:

<!-- llm-d-cicd:skip start -->
```bash
kubectl annotate serviceaccount llm-d-async -n ${NAMESPACE} \
    iam.gke.io/gcp-service-account=<gsa-name>@${PROJECT_ID}.iam.gserviceaccount.com
```
<!-- llm-d-cicd:skip end -->

The multi-tenant guide's [`gcp-setup.sh`](./multitenant/scripts/gcp-setup.sh) scripts this service-account variant.

**Values.** In [`gcp-pubsub/values.yaml`](./gcp-pubsub/values.yaml), replace `REPLACE_WITH_YOUR_PROJECT` in `project_id`, `result_topic_id`, and `subscriber_id` with your project ID (and the topic and subscription names, if you changed them). Leave `igw_base_url`; Step 3 sets it.

</details>
<details>
<summary><b>Redis Sorted Set</b></summary>

A Redis Sorted Set is a persisted queue that orders requests by deadline (the score). You need a Redis instance reachable from the cluster. To install one without authentication:

<!-- llm-d-cicd:skip start -->
```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install redis bitnami/redis -n redis --create-namespace --set auth.enabled=false
```
<!-- llm-d-cicd:skip end -->

Or with authentication, plus a Secret holding the full connection URL for the Async Processor. The chart is installed into this namespace in Step 3, but the Secret has to exist first:

<!-- llm-d-cicd:skip start -->
```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
export REDIS_PASSWORD=your-secure-password
helm install redis bitnami/redis -n redis --create-namespace --set auth.enabled=true --set auth.password=$REDIS_PASSWORD

kubectl create namespace llm-d-async --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic redis-creds -n llm-d-async \
  --from-literal=url="redis://:$REDIS_PASSWORD@redis-master.redis.svc.cluster.local:6379"
```
<!-- llm-d-cicd:skip end -->

**Values.** [`redis/values.yaml`](./redis/values.yaml) points `urlSecret.url` at the unauthenticated `redis-master.redis.svc.cluster.local:6379`, and the chart creates the Secret. With authentication, comment out `urlSecret.url` and set `urlSecret.name: redis-creds` and `urlSecret.key: url` instead. Leave `igw_base_url`; Step 3 sets it.

</details>
<!-- tabs:end -->

#### Step 3: Deploy Async Processor

Deploy the Async Processor using the selected queue implementation's configuration:

```bash
export NAMESPACE=llm-d-async
export MQ_PROVIDER=gcp-pubsub # options are gcp-pubsub or redis
export ASYNC_VERSION=v0.10.0   # llm-d-async release

[ "$MQ_PROVIDER" = "redis" ] && TARGET_KEY="ap.transportConfig.queues[0].igw_base_url" || TARGET_KEY="ap.transportConfig.topics[0].igw_base_url"

helm install llm-d-async \
    oci://ghcr.io/llm-d/charts/llm-d-async \
    -f ${REPO_ROOT}/guides/batch-serving/asynchronous-processing/${MQ_PROVIDER}/values.yaml \
    --set ${TARGET_KEY}=http://${IP}:80 \
    -n ${NAMESPACE} --create-namespace --version ${ASYNC_VERSION}
```

## Testing

Wait for the Async Processor to be ready:

<!-- llm-d-cicd:skip start -->
```bash
kubectl get pods -n ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->

Then enqueue a request and read its result. In the request, `deadline` and `created` are Unix-seconds **numbers**, not strings — a quoted `deadline` fails to decode.

<!-- tabs:start group=queue -->
<details open>
<summary><b>GCP Pub/Sub</b></summary>

1. **Publish a message.** On Pub/Sub the request is published on its own, **not** wrapped in an `InternalRequest` envelope; the consumer builds that itself from the Pub/Sub message.

   <!-- llm-d-cicd:skip start -->
   ```bash
   gcloud pubsub topics publish $REQUEST_TOPIC_NAME --message='{"id":"testmsg","created":1700000000,"deadline":1999999999,"payload":{"model":"your-model","prompt":"Hi, good morning"}}'
   ```
   <!-- llm-d-cicd:skip end -->

2. **Pull the result.** Create a subscription for the result topic if you haven't already, then pull from it:

   <!-- llm-d-cicd:skip start -->
   ```bash
   gcloud pubsub subscriptions create async-proc-results-sub --topic=$RESULT_TOPIC_NAME
   gcloud pubsub subscriptions pull async-proc-results-sub --auto-ack --limit=1
   ```
   <!-- llm-d-cicd:skip end -->

</details>
<details>
<summary><b>Redis Sorted Set</b></summary>

Requests are consumed as an `InternalRequest` envelope: a `request_kind` tag (`redis` for the sorted-set queue) wrapping the caller-visible request under `data`. If you installed Redis with authentication, add `-a $REDIS_PASSWORD` after `-h $REDIS_IP` in both commands.

1. **Publish a message** with the Redis CLI:

   <!-- llm-d-cicd:skip start -->
   ```bash
   export REDIS_IP=$(kubectl get svc -n redis redis-master -o jsonpath='{.spec.clusterIP}')
   kubectl run --rm -i -t publishmsgbox --image=redis --restart=Never -- /usr/local/bin/redis-cli -h $REDIS_IP ZADD request-sortedset 1999999999 '{"request_kind":"redis","internal":{},"data":{"id":"testmsg","created":1700000000,"deadline":1999999999,"payload":{"model":"your-model","prompt":"Hi, good morning"}}}'
   ```
   <!-- llm-d-cicd:skip end -->

2. **Check for results:**

   <!-- llm-d-cicd:skip start -->
   ```bash
   kubectl run --rm -i -t resultbox --image=redis --restart=Never -- /usr/local/bin/redis-cli -h $REDIS_IP RPOP result-list
   ```
   <!-- llm-d-cicd:skip end -->

</details>
<!-- tabs:end -->

## Cleanup

```bash
helm uninstall llm-d-async -n ${NAMESPACE}
```

## Related

- [Async Processor Operations](../../../docs/operations/components/async-processor.md) — concurrency, container sizing, and horizontal scaling.
- [Async Processor Architecture](../../../docs/architecture/advanced/batch/async-processor.md) — internal mechanics, gates, and queue integrations.
