# Google TPU optimized baseline Deployment Guide

## **Automated Testing Coverage** : None (currently, not part of nightly testing by llm-d maintainers)

## Overview

This document provides complete steps for deploying the optimized baseline service on a Google Kubernetes Engine (GKE) cluster using TPU accelerators and the `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8` model, utilizing the [Run:ai Model Streamer](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/) to load model weights directly from Google Cloud Storage (GCS) for high performance.

For broader context on optimized baseline, gateway options, and architecture, refer to the [main optimized baseline guide](./README.md).

## Hardware Requirements

This guide uses Cloud TPU v7x accelerators on Google Cloud Platform (GCP). The default topology configured in `values_tpu_v7.yaml` requests a `2x2x1` TPU v7x topology. 

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
- Create a namespace for installation.

  ```bash
  export NAMESPACE=llm-d-inference-scheduler # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md)

## Installation

### Step 1: Prepare the GCS Bucket and Model (Run:ai Model Streamer)

This deployment utilizes the Run:ai Model Streamer to load model weights directly from Google Cloud Storage (GCS) for a faster inference server start up time. Because there is currently no public bucket available with the required model, you must host it in your own bucket.

1. Download the [Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8](https://huggingface.co/Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8) model and upload it to a GCS bucket you control. You can do this in many ways, but one option is to deploy a [transfer job](https://gke-ai-labs.dev/docs/tutorials/storage/hf-gcs-transfer/) to hydrate the GCS bucket with model weights from Hugging Face.
2. Open `ms-optimized-baseline/values_tpu_v7.yaml` and replace `<Insert GCS URI here>` with the GCS URI containing your model weights and your desired path for the XLA compilation cache. For example, if your GCS bucket is `my-gcs-bucket` and the model weights are at the path `Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8`, you would set `--model=gs://my-gcs-bucket/Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8`. You should also set `VLLM_XLA_CACHE_PATH` to any valid GCS URI within the bucket where you want to store the XLA compilation cache, such as: `gs://my-gcs-bucket/xla-cache`.
3. Open `ms-optimized-baseline/values_tpu_v7.yaml` and replace `placeholder-sa` with the Kubernetes Service account you will create in Step 2 below.

### Step 2: Configure Workload Identity for GCS Access

To allow the vLLM pods to read the model from your GCS bucket, you must configure a Kubernetes Service Account linked to a Google Cloud Service Account with Workload Identity.

```bash 
export PROJECT_ID=$(gcloud config get project)
export PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format="value(projectNumber)") 
export BUCKET_NAME=<Insert GCS bucket name here>
export KSA_NAME=<Insert KSA name here>
kubectl create sa $KSA_NAME -n $NAMESPACE
gcloud storage buckets add-iam-policy-binding gs://${BUCKET_NAME} --member "principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT_ID.svc.id.goog/subject/ns/${NAMESPACE}/sa/${KSA_NAME}" --role "roles/storage.objectUser"
gcloud storage buckets add-iam-policy-binding gs://${BUCKET_NAME} --member "principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$PROJECT_ID.svc.id.goog/subject/ns/${NAMESPACE}/sa/${KSA_NAME}" --role "roles/storage.bucketViewer"
```

### Step 3: Install the Stack via Helmfile

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-inference-scheduler` in this example.

```bash
cd guides/optimized-baseline
helmfile apply -e gke_tpu_v7 -n ${NAMESPACE}
```

### Step 4: Install HTTPRoute
Apply the GKE-specific HTTPRoute configuration to route traffic through your gateway:

```bash
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

## Next Steps: Verification, Benchmarking, and Cleanup

For all subsequent steps—including verifying your pods are running, benchmarking the deployment, and cleaning up resources—please follow the instructions in the [main optimized baseline guide](./README.md).
