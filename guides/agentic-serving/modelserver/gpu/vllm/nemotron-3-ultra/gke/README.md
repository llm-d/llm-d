# Deploy


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
gcloud container clusters create llm-models \
    --addons GcsFuseCsiDriver \
    --cluster-version=<VERSION> \
    --location=<LOCATION> \
    --workload-pool=<PROJECT_ID>.svc.id.goog
```
To verify that the Cloud Storage FUSE CSI driver is enabled on the cluster, run the command:
```
gcloud container clusters describe llm-models \
    --location=<LOCATION> \
    --project=<PROJECT_ID> \
    --format="value(addonsConfig.gcsFuseCsiDriverConfig.enabled)"
```    
For more detailed information, check https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cloud-storage-fuse-csi-driver-setup#enable


## Configure Access to Cloud Storage Buckets





For instructions on creating the GCS bucket and enabling the FUSE CSI Driver, please refer to the [page](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/cloud-storage-fuse-csi-driver-setup#authentication).
> The following installation and configuration steps assume that the `RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block` model has already been stored in your GCS bucket in the folder structure `llm-models/RedHatAI/NVIDIA-Nemotron-3-Ultra-550B-A55B-FP8-block` (`llm-models` is the bucket name).
> ${INFRA\_PROVIDER} is defaulted to `gke`.
