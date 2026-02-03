# [In Development] Offloading Prefix Cache to Lustre

>**NOTE:** This guide provides configuration to set up KV cache offloading to Lustre using the lmcache-connector. Performance benchmarking comparisons is currently under development.

## Overview

This guide provides recipes to offload prefix cache to GCP Lustre storage backend.

## Prerequisites

* All prerequisites from the [upper level](../README.md).
* Have the [proper client tools installed on your local system](../../prereq/client-setup/README.md) to use this guide.
* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../prereq/infrastructure/README.md).
* Create a namespace for installation.

  ```
  export NAMESPACE=llm-d-pfc-lustre # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

* [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../prereq/client-setup/README.md#huggingface-token) to pull models.
* [Choose an llm-d version](../../prereq/client-setup/README.md#llm-d-version)

## Cluster setup for provisioning a managed GCP Lustre instance (GKE cluster)

To create a new GKE cluster, you need to first set up a separate VPC for provisioning a managed GCP Lustre instance. Follow the [steps from Lustre guide](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/lustre-csi-driver-new-volume#vpc-setup) to setup a separate VPC network.

If you have an existing cluster, you can update the same VPC network to enable provisioning a managed GCP Lustre instance. Ensure you initialize the variable `NETWORK_NAME` with your existing network name and skip the network creation command in the [setup above](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/lustre-csi-driver-new-volume#vpc-setup).

Please update the `$NETWORK_NAME` and `$LOCATION` variables in [lustre-config.yaml](./manifests/lustre-config.yaml) to match your cluster configuration.

Ensure [Lustre CSI driver is enabled](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/lustre-csi-driver-new-volume#manage) on the cluster, otherwise it would fail to provision a managed GCP Lustre instance

## Installation
The below installation steps have been tested for GKE, you can customize the manifests if you run on other Kubernetes providers.

<!-- TABS:START -->
### GKE
```
cd guides/tiered-prefix-cache/lustre
```
#### Provision a managed GCP Lustre instance
Create a managed GCP Lustre instance in the required location

```bash
kubectl apply -f ./manifests/lustre-config.yaml -n ${NAMESPACE}
```

#### Deploy Gateway and HTTPRoute

Deploy the Gateway and HTTPRoute using the [gateway recipe](../../recipes/gateway/README.md).

#### Deploy vLLM Model Server


  <!-- TAB:LMCache-Lustre as local disk connector -->
##### LMCache-Lustre Connector

Deploy the vLLM model server using the LMCache connector, configured for KVCache offloading across tiered storage consisting of CPU RAM and a mounted managed GCP Lustre instance.

```bash
kubectl apply -k ./manifests/vllm/lustre-lmcache-connector -n ${NAMESPACE}
```

#### Deploy InferencePool

This guide currently uses the same tired prefix caching scoring configuration, so deploy the inferencepool following GKE specific command from [CPU offloading inferencepool guide](../cpu/README.md#gke). A follow up is to further optimize `inferencepool` configuration considering the storage tier.

<!-- TABS:END -->

## Verifying the installation

You can verify the installation by checking the status of the created resources.

### Check the Gateway

```bash
kubectl get gateway -n ${NAMESPACE}
```

You should see output similar to the following, with the `PROGRAMMED` status as `True`.

```
NAME                      CLASS                              ADDRESS     PROGRAMMED   AGE
llm-d-inference-gateway   gke-l7-regional-external-managed   <redacted>  True         16m
```

### Check the HTTPRoute

```bash
kubectl get httproute -n ${NAMESPACE}
```

```
NAME          HOSTNAMES   AGE
llm-d-route               17m
```

### Check the InferencePool

```bash
kubectl get inferencepool -n ${NAMESPACE}
```

```
NAME            AGE
llm-d-infpool   16m
```

### Check the PVC
```bash
kubectl get pvc -n ${NAMESPACE}
```
```
NAME         STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   VOLUMEATTRIBUTESCLASS   AGE
lustre-pvc   Bound    pvc-3c793698-XXXXXXX   36000Gi    RWX            lustre-class   <unset>                 6d
```

### Check the Pods

```bash
kubectl get pods -n ${NAMESPACE}
```

You should see the InferencePool's endpoint pod and the model server pods in a `Running` state.

```
NAME                                  READY   STATUS    RESTARTS   AGE
llm-d-infpool-epp-xxxxxxxx-xxxxx     1/1     Running   0          16m
llm-d-model-server-xxxxxxxx-xxxxx   1/1     Running   0          11m
llm-d-model-server-xxxxxxxx-xxxxx   1/1     Running   0          11m
```

Check the model server pod logs to confirm successful startup:
```bash
kubectl logs llm-d-model-server-xxxxxxxx-xxxxx -n ${NAMESPACE}
```
You should see the following lines in the logs 
```
(APIServer pid=1) INFO:     Started server process [1]
(APIServer pid=1) INFO:     Waiting for application startup.
(APIServer pid=1) INFO:     Application startup complete.
```
This indicates that the model server pod is now ready to serve requests. You can also verify if the requests are being served from local storage (Lustre in this case) by check the metric `lmcache:local_storaqe_usage` through following command.
```bash
export IP=localhost
export PORT=8000
kubectl exec -it llm-d-model-server-xxxx-xxxx -- curl -i http://${IP}:${PORT}/metrics | grep lmcache:local_storage_usage
```

The metric can also be viewed through Pantheon UI for a GCP project through `Metrics Explorer`: `Prometheus Target > lmcache > prometheus/lmcache:local_storage_usage/gauge`

## Cleanup

To remove the deployment:

```bash
helm uninstall llm-d-infpool -n ${NAMESPACE}
kubectl delete -k ./manifests/vllm/lustre-lmcache-connector -n ${NAMESPACE}
kubectl delete -k ../../recipes/gateway/gke-l7-regional-external-managed -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```