# [Experimental] Batch Gateway

[Batch Gateway](https://github.com/llm-d-incubation/batch-gateway) provides an OpenAI-compatible Batch API for submitting, tracking, and managing large-scale batch inference jobs. It is designed to efficiently process batch workloads alongside interactive workloads on shared infrastructure.

## Overview

### Key Features

- **Process large-scale batch jobs**: Process batch jobs with up to 50,000 (configurable) inference requests per job, with progress tracking and job management capabilities.
- **Provide an OpenAI-compatible Batch API**: Full schema parity with OpenAI's `/v1/batches` and `/v1/files` endpoints.
- **Optimize resource utilization**: Enables batch and interactive workloads to coexist on shared infrastructure. Integrates with flow control mechanisms to adjust the batch dispatch rate based on downstream metrics.
- **Multi-tenant isolation**: Per-tenant data isolation with pluggable authentication and authorization.

### Components

1. **API Server** — REST API server for batch job submission, management, tracking, and file management.
2. **Batch Processor** — Pulls jobs from a priority queue, builds per-model execution plans, dispatches inference requests to the llm-d Router, and writes results to output files.
3. **Garbage Collector** — Cleans up expired jobs and files.

### Storage Layer

Batch Gateway uses pluggable storage backends. Each function is backed by a single plug-in, chosen at deployment time:

| Function | Available plug-ins |
|----------|-------------------|
| Jobs and files metadata | PostgreSQL, Redis/Valkey (development/test only) |
| Priority queue, events, status updates | Redis/Valkey |
| File storage (input/output) | S3, Filesystem |

## Prerequisites

Before installing Batch Gateway, ensure you have:

1. **Kubernetes cluster**: A running Kubernetes cluster (v1.25+).
   - For local development, you can use `Kind` or `Minikube`.
   - For production, `OpenShift`, `GKE` or `AKS` are supported.
2. **Helm**: 3.0+
3. **llm-d Inference Stack**: Batch Gateway requires an existing [optimized baseline](../optimized-baseline/README.md) stack to dispatch requests to.
4. **PostgreSQL**: 12+ for metadata storage. Redis/Valkey are available as an alternative for development/test only.
5. **Redis/Valkey**: Redis 6+ or Valkey 8+ for priority queue, events, and status updates.
6. **S3 or Filesystem**: For batch input and output file storage.

## Installation

### Step 1: Create the Namespace

```bash
export NAMESPACE=batch-gateway
kubectl create namespace ${NAMESPACE}
```

### Step 2: Create the Secrets

Batch Gateway requires a Kubernetes Secret with database and storage credentials:

```bash
kubectl create secret generic batch-gateway-secrets -n ${NAMESPACE} \
  --from-literal=redis-url="redis://redis-master.redis.svc.cluster.local:6379/0" \
  --from-literal=postgresql-url="postgresql://user:password@postgresql.postgresql.svc.cluster.local:5432/batchgateway" \
  --from-literal=s3-secret-access-key="<your-s3-secret-key>"
```

### Step 3: Configure the llm-d Router URL

The Batch Processor needs to know where to send inference requests. This is configured via the `processor.config.globalInferenceGateway.url` Helm value, or per-model via `processor.config.modelGateways`.

**Single gateway** (all models route to one endpoint):

```bash
export INFERENCE_GW_URL="http://infra-inference-scheduling-inference-gateway-istio.llm-d-inference-scheduler.svc.cluster.local:80"
```

**Per-model gateways** (different endpoints per model): see the [Helm chart README](https://github.com/llm-d-incubation/batch-gateway/blob/main/charts/batch-gateway/README.md) for `modelGateways` configuration.

### Step 4: Deploy

**From OCI Registry:**

```bash
helm install batch-gateway oci://ghcr.io/llm-d-incubation/charts/batch-gateway \
  -n ${NAMESPACE} \
  --set processor.config.globalInferenceGateway.url="${INFERENCE_GW_URL}" \
  --set "apiserver.config.batchAPI.passThroughHeaders={Authorization}" \
  --set global.fileClient.fs.pvcName="batch-gateway-pvc"
```

**From Source:**

```bash
helm install batch-gateway ./charts/batch-gateway \
  -n ${NAMESPACE} \
  --set processor.config.globalInferenceGateway.url="${INFERENCE_GW_URL}" \
  --set "apiserver.config.batchAPI.passThroughHeaders={Authorization}" \
  --set global.fileClient.fs.pvcName="batch-gateway-pvc"
```

> **Note**: `passThroughHeaders` should include any authentication headers (e.g., `Authorization`) that the llm-d Router expects. The processor forwards these headers when dispatching individual inference requests.

## Detailed Deployment Guide

For a production deployment with authentication, authorization, and TLS on Kubernetes, see the [Kubernetes deployment guide](https://github.com/llm-d-incubation/batch-gateway/blob/main/docs/guides/deploy-k8s.md) (Istio + Kuadrant + cert-manager).

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

### Download Results

Once the job status is `completed`, retrieve the output file:

```bash
# Get the output file ID
OUTPUT_FILE_ID=$(curl -s \
  http://localhost:8000/v1/batches/<batch-id> | jq -r '.output_file_id')

# Download the results
curl http://localhost:8000/v1/files/${OUTPUT_FILE_ID}/content > results.jsonl
```

## Cleanup

```bash
helm uninstall batch-gateway -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```

## Related

- [Batch Gateway Repository](https://github.com/llm-d-incubation/batch-gateway) — source code, Helm chart, and detailed documentation.
- [Asynchronous Processing](../asynchronous-processing/README.md) — queue-based asynchronous inference for individual requests (complementary to Batch Gateway's job-oriented API).
