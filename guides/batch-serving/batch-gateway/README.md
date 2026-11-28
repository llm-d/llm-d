# Batch Gateway

[Batch Gateway](https://github.com/llm-d/llm-d-batch-gateway) provides an OpenAI-compatible Batch API for submitting, tracking, and managing large-scale batch inference jobs. The Batch Processor sends requests to an inference gateway over HTTP by default. It can optionally dispatch through [llm-d-async](https://github.com/llm-d/llm-d-async) for queue-based processing.

## Overview

### When to Pick This Path

- You have **offline inference workloads** (such as evaluations, embeddings, dataset processing) that don't need real-time responses.
- You want to **utilize idle accelerator capacity** for batch work while protecting interactive traffic from interference.
- Your clients expect an **OpenAI-compatible Batch API** (`/v1/batches`, `/v1/files`) for job submission, tracking and management.
- You need **multi-tenant isolation** — each tenant's jobs, files, and results are separated.

For queue-based dispatch of individual requests through llm-d-async, see the [Asynchronous Processing](../asynchronous-processing/README.md) guide. For components, data flow, and the processing pipeline, see the [Batch Gateway Architecture](../../../docs/architecture/batch/batch-gateway.md).

### Key Features

- **Process large-scale batch jobs**: Accept up to 50,000 JSONL request lines per input file by default. Set `apiserver.config.fileAPI.maxLineCount` to change the limit.
- **Provide an OpenAI-compatible Batch API**: Full schema parity with OpenAI's `/v1/batches` and `/v1/files` endpoints.
- **Optimize resource utilization**: Built-in AIMD concurrency control adjusts dispatch to backend responses. For metrics-driven dispatch gates, use the optional llm-d-async integration.
- **Multi-tenant isolation**: Jobs, files, and results are scoped to a tenant identified by a trusted request header.

### Components

1. **API Server** — REST API server for batch job submission, management, tracking, and file management.
2. **Batch Processor** — Pulls jobs from a priority queue, builds per-model execution plans, dispatches inference requests to the llm-d Router, and writes results to output files.
3. **Garbage Collector** — Cleans up expired jobs and files.

### Storage Layer

PostgreSQL stores job and file metadata, the job priority queue, events, and status. Select file storage separately; async inference queues are used only when `dispatchMode=async`:

| Function | Available backends |
| -------- | ------------------ |
| Job and file metadata, job priority queue, events, status | PostgreSQL |
| Input and output files | Shared filesystem (RWX PVC), S3-compatible storage, GCS |
| Async inference request queue (`dispatchMode=async`) | Redis/Valkey sorted set by default, or experimental PostgreSQL SQL transport |
| Async inference result queue (`dispatchMode=async`) | Redis/Valkey list by default, or experimental PostgreSQL SQL transport |

## Prerequisites

Before installing Batch Gateway, ensure you have:

1. **Kubernetes and Helm**: A cluster that meets the requirements of the llm-d inference stack you deploy, and Helm 3.0 or later.
2. **llm-d Inference Stack**: An existing [optimized baseline](../../optimized-baseline/README.md) or another llm-d Router deployment.
3. **PostgreSQL 12 or later**: Required for job and file metadata, the job priority queue, events, and status. The Batch Gateway chart does not install PostgreSQL.
4. **File storage**: Use a shared `ReadWriteMany` PVC, S3-compatible storage, or GCS. See the component's [GCS storage guide](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/gcs-storage.md) for native GCS setup.
5. **Optional async queue**: Redis 6 or Valkey 8 is needed for the default `redis-sortedset` transport. The experimental SQL transport uses PostgreSQL and requires compatible Batch Gateway and llm-d-async `main` versions.

### Tenant identity and authentication

By default, the API server requires the `X-MaaS-Username` tenant header and returns `401` when it is missing. The API server does not authenticate credentials; production deployments need a trusted authentication proxy to set this header. For this local demo, the install command enables `apiserver.config.allowAnonymousTenant` so requests without a header use the `default` tenant. Never enable this field in production. See the component's [security guide](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/security.md).

For a production deployment with authentication, authorization, and TLS, follow the component's [Kubernetes deployment guide](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/deploy-k8s.md).

### Get the guide

The observability steps below apply alert rules and dashboards from a local clone of the [llm-d repository](https://github.com/llm-d/llm-d). Set the branch and clone the repo (if you already have a checkout, skip this and run the remaining commands from inside it):

```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
```

## Installation

### Step 1: Create the Namespace

```bash
export NAMESPACE=batch-gateway
kubectl create namespace ${NAMESPACE}
```

### Step 2: Create the Secrets

The chart expects an existing PostgreSQL database and does not install one. Replace the example URL with the address and credentials for your PostgreSQL service:

```bash
kubectl create secret generic batch-gateway-secrets -n ${NAMESPACE} \
  --from-literal=postgresql-url="postgresql://user:password@postgresql.postgresql.svc.cluster.local:5432/batchgateway"
```

For async dispatch with the default Redis transport, also add a `redis-url` key to this Secret. The experimental SQL transport uses the PostgreSQL URL and does not need Redis. The default synchronous dispatch mode needs neither async queue.

This guide uses the filesystem backend, so it does not need object-store credentials. For S3, set `global.fileClient.type=s3`, provide the bucket, region, and access key ID, and add `s3-secret-access-key` to the Secret. Native GCS uses Application Default Credentials; see the [GCS storage guide](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/gcs-storage.md).

### Step 3: Configure the llm-d Router URL

The Batch Processor needs to know where to send inference requests. This is configured via the `processor.config.globalInferenceGateway.url` Helm value, or per-model via `processor.config.modelGateways`.

**Single gateway** (all models route to one endpoint). With the [optimized baseline](../../optimized-baseline/README.md) in Standalone mode, that is its llm-d Router Service; in Gateway mode, use the Gateway address instead:

```bash
export INFERENCE_GW_URL="http://optimized-baseline-epp.llm-d-optimized-baseline.svc.cluster.local:80"
```

**Per-model gateways** (different endpoints per model): see the [Helm chart README](https://github.com/llm-d/llm-d-batch-gateway/blob/main/charts/batch-gateway/README.md) for `modelGateways` configuration.

### Step 4: Create the File Storage PVC

This guide uses the filesystem storage backend (`global.fileClient.type` defaults to `fs`). The API server, processor, and garbage collector share the same PVC, so it must support `ReadWriteMany` (RWX). Create the PVC referenced by the deploy step:

```bash
kubectl apply -n ${NAMESPACE} -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: batch-gateway-pvc
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 10Gi  # example size, tune to your workload
EOF
```

> **Note**: `ReadWriteMany` requires an RWX-capable storage class (e.g. NFS, CephFS). If your cluster only provides `ReadWriteOnce`, use S3-compatible or GCS object storage instead.

### Step 5: Deploy

**From current component source (`main`):**

```bash
git clone --branch main --depth 1 https://github.com/llm-d/llm-d-batch-gateway.git
helm install batch-gateway llm-d-batch-gateway/charts/batch-gateway \
  -n ${NAMESPACE} \
  --set processor.config.globalInferenceGateway.url="${INFERENCE_GW_URL}" \
  --set apiserver.config.allowAnonymousTenant=true \
  --set "apiserver.config.batchAPI.passThroughHeaders={Authorization}" \
  --set global.fileClient.fs.pvcName="batch-gateway-pvc"
```

This uses the component's `main` chart and its `latest` images. `apiserver.config.allowAnonymousTenant` is only for this local demo.

> **Note**: `passThroughHeaders` should include any authentication headers (e.g., `Authorization`) that the llm-d Router expects. The processor forwards these headers when dispatching individual inference requests.

## Detailed Deployment Guide

For a production deployment with authentication, authorization, and TLS on Kubernetes, see the [Kubernetes deployment guide](https://github.com/llm-d/llm-d-batch-gateway/blob/main/docs/guides/deploy-k8s.md) (Istio + Kuadrant + cert-manager).

## Verification

1. Check that all pods are running:

   ```bash
   kubectl get pods -n ${NAMESPACE}
   ```

   You should see pods for `apiserver`, `processor`, and `gc`, all in `Running` state.

2. Verify health endpoints:

   ```bash
   kubectl port-forward -n ${NAMESPACE} svc/batch-gateway-apiserver 8081:8081 &
   curl http://localhost:8081/health
   ```

## Usage

The examples below talk to the API server on port `8000`. Port-forward it first (the health check in the previous section uses a separate port, `8081`):

```bash
kubectl port-forward -n ${NAMESPACE} svc/batch-gateway-apiserver 8000:8000 &
```

### Upload an Input File

Prepare a JSONL input file with one request per line (see the [OpenAI Batch API format](https://platform.openai.com/docs/api-reference/batch)):

```json
{"custom_id": "req-001", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "my-model", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 100}}
{"custom_id": "req-002", "method": "POST", "url": "/v1/chat/completions", "body": {"model": "my-model", "messages": [{"role": "user", "content": "What is llm-d?"}], "max_tokens": 200}}
```

Upload the file:

```bash
curl -X POST http://localhost:8000/v1/files \
  -F "purpose=batch" \
  -F "file=@batch_input.jsonl"
```

### Create a Batch Job

```bash
curl -X POST http://localhost:8000/v1/batches \
  -H "Content-Type: application/json" \
  -d '{
    "input_file_id": "<file-id-from-upload>",
    "endpoint": "/v1/chat/completions",
    "completion_window": "24h"
  }'
```

### Monitor Job Status

```bash
curl http://localhost:8000/v1/batches/<batch-id> | jq '{status, request_counts}'
```

Batch status can be `validating`, `in_progress`, `cancelling`, `finalizing`, `completed`, `failed`, `expired`, or `cancelled`. `cancelling` is non-terminal; poll until `cancelled`.

### Download Results

Once the job status is `completed`, retrieve the output file:

```bash
# Get the output file ID
OUTPUT_FILE_ID=$(curl -s \
  http://localhost:8000/v1/batches/<batch-id> | jq -r '.output_file_id')

# Download the results
curl http://localhost:8000/v1/files/${OUTPUT_FILE_ID}/content > results.jsonl
```

## Enable Monitoring

Optional. Requires Prometheus and Grafana — see [Observability Setup](../../../docs/operations/observability/setup.md).

1. Enable metrics scraping in the chart, so Prometheus discovers the three components:

   ```bash
   helm upgrade batch-gateway llm-d-batch-gateway/charts/batch-gateway \
     -n ${NAMESPACE} --reuse-values \
     --set processor.podMonitor.enabled=true \
     --set gc.podMonitor.enabled=true \
     --set apiserver.serviceMonitor.enabled=true
   ```

   > [!NOTE]
   > This step still uses the component chart's own `PodMonitor`/`ServiceMonitor` templates. Per [llm-d#1983](https://github.com/llm-d/llm-d/issues/1983) these scrape manifests should eventually live in this repo as standalone recipes; the target path is still being settled, so this guide uses the chart flags in the meantime. The alerts and dashboards below are already served from this repo.

2. Apply the alerting rules:

   ```bash
   kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/recipes/observability/alerts/batch-gateway-alerting-rules.yaml
   ```

   These are scoped to `namespace="batch-gateway"`. If you changed `${NAMESPACE}`, edit the matchers first — see [Alerting](../../../docs/operations/observability/alerting.md#batch-gateway-batch-gatewayrules).

3. Load the dashboards:

   ```bash
   ${REPO_ROOT}/guides/recipes/observability/load-llm-d-dashboards.sh
   ```

   This loads every llm-d dashboard, including `llm-d-batch-gateway-apiserver`, `-processor`, and `-gc`. Select your namespace with each dashboard's `namespace` variable.

4. Verify:

   ```bash
   kubectl get podmonitors,servicemonitors,prometheusrules -n ${NAMESPACE}
   ```

For what the metrics mean and how to query them, see the [Batch Gateway metric reference](../../../docs/operations/observability/batch-gateway-metrics.md).

## Cleanup

```bash
helm uninstall batch-gateway -n ${NAMESPACE}
```

<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->

## Related

- [Batch Gateway Repository](https://github.com/llm-d/llm-d-batch-gateway) — source code, Helm chart, and detailed documentation.
- [Asynchronous Processing](../asynchronous-processing/README.md) — queue-based asynchronous inference for individual requests (complementary to Batch Gateway's job-oriented API).
