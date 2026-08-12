# Downloading the Model to Your GCS Bucket for Deployment


The `RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block` model is ~560GB in size and downloading a model of this scale directly from HuggingFace can take over an hour, and because every deployment triggers a new download, it creates a significant bottleneck.
To save time and accelerate the deployment process, the page shows how to download the model to a Google Cloud Storage (GCS) bucket once and accessing it directly from there.


## Create the Cloud Storage Bucket

1. In your development environment, run the command:

```
gcloud storage buckets create gs://llm-models --location=<BUCKET_LOCATION>
```

If the request is successful, the command returns the following message:
```
Creating gs://llm-models/
```
For more detailed information, check https://docs.cloud.google.com/storage/docs/creating-buckets#command-line

## Enable the Cloud Storage FUSE CSI driver
For Autopilot clusters, please skip this step as Cloud Storage FUSE CSI driver is enabled by default for Autopilot clusters.

For Standard clusters, run the command:
```
gcloud container clusters create <CLUSTER_NAME> \
    --addons GcsFuseCsiDriver \
    --cluster-version=<VERSION> \
    --location=<LOCATION> \
    --workload-pool=<PROJECT_ID>.svc.id.goog
```
To verify that the Cloud Storage FUSE CSI driver is enabled on the cluster, run the command:
```
gcloud container clusters describe <CLUSTER_NAME> \
    --location=<LOCATION> \
    --project=<PROJECT_ID> \
    --format="value(addonsConfig.gcsFuseCsiDriverConfig.enabled)"
```    
For more detailed information, check https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cloud-storage-fuse-csi-driver-setup#enable


## Configure Access to Cloud Storage Buckets
To make your Cloud Storage buckets accessible by your GKE cluster, authenticate using Workload Identity Federation for GKE with the Cloud Storage bucket that you want to mount in your Pod specification:
1. Make sure you have Workload Identity Federation for GKE enabled, if not, follow [these steps](
"https://docs.cloud.google.com/kubernetes-engine/docs/how-to/workload-identity#enable_on_clusters_and_node_pools
) to enable it

2. Get credentials for your cluster:
```
gcloud container clusters get-credentials <CLUSTER_NAME> \
    --location=<LOCATION>
```
3. Create a namespace to use for the [Kubernetes ServiceAccount](https://kubernetes.io/docs/concepts/security/service-accounts/). You can also use the default namespace or any existing namespace.
```
kubectl create namespace <NAMESPACE>
```
4. Create a Kubernetes ServiceAccount for your application to use. You can also use any existing Kubernetes ServiceAccount in any namespace.
```
kubectl create serviceaccount <KSA_NAME> \
    --namespace <NAMESPACE>
```
5. Grant one of the IAM roles for Cloud Storage to the Kubernetes ServiceAccount
```
gcloud storage buckets add-iam-policy-binding gs://llm-models \
    --member "principal://iam.googleapis.com/projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/<PROJECT_ID>.svc.id.goog/subject/ns/<NAMESPACE>/sa/<KSA_NAME>" \
    --role "<ROLE_NAME>"
```
For more detailed information, check https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cloud-storage-fuse-csi-driver-setup#authentication


## Download the Model
1. Make sure you have `Hugging Face CLI` installed. Otherwise, install it
```
curl -LsSf https://hf.co/cli/install.sh | bash
```
2. Download the model from HuggingFace to your local disk first
```
hf download RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block \
  --local-dir ./NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block \
  --token <YOUR_HF_TOKEN>
```
3. Upload to GCS
```
gcloud storage cp -r ./NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block gs://llm-models/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block
```

## Deploy the Model Server 
Once the model is stored in your GCS, please refer back to https://github.com/llm-d/llm-d/blob/main/guides/agentic-serving/nemotron-3-ultra-550b-h200.md#2-deploy-the-model-server-gpus to continue the deployment.

> [!NOTE]
> Please set `INFRA_PROVIDER` = `gke` to leverage this deployment.


