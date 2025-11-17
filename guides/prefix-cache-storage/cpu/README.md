# Offloading Prefix Cache to CPU Memory

## Overview

This guide provides recipes to offload prefix cache to CPU RAM via the vLLM native offloading connector and the LMCache connector.

## Prerequisites

* All prerequisites from the [upper level](../README.md).
* Have the [proper client tools installed on your local system](../../prereq/client-setup/README.md) to use this guide.
* Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../../prereq/infrastructure/README.md).

## Installation

First, set up a namespace for the deployment and create the HuggingFace token secret.

```bash
export NAMESPACE=llm-d-pfc-cpu # or any other namespace
kubectl create namespace ${NAMESPACE}

# NOTE: You must have your HuggingFace token stored in the HF_TOKEN environment variable.
export HF_TOKEN="<your-hugging-face-token>"
kubectl create secret generic llm-d-hf-token --from-literal=HF_TOKEN=${HF_TOKEN} -n ${NAMESPACE}
```

### 1. Deploy Gateway and HTTPRoute

Deploy the Gateway and HTTPRoute using the [gateway recipe](../../recipes/gateway/README.md).

### 2. Deploy InferencePool

Deploy the InferencePool using the [InferencePool recipe](../../recipes/inferencepool/README.md).

### 3. Deploy vLLM Model Server

=== "LMCache"
    Deploy the vLLM model server with the `LMCache` connector enabled.
    ```bash
    kubectl apply -k ./manifests/vllm/lm-cache-connector -n ${NAMESPACE}
    ```
=== "Offloading"
    Deploy the vLLM model server with the `OffloadingConnector` enabled.
    ```bash
    kubectl apply -k ./manifests/vllm/offloading-connector -n ${NAMESPACE}
    ```

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

## Cleanup

To remove the deployment:

```bash
helm uninstall llm-d-infpool -n ${NAMESPACE}
kubectl delete -k ./manifests/vllm/offloading-connector -n ${NAMESPACE}
kubectl delete -k ../../../../recipes/gateway/gke-l7-regional-external-managed -n ${NAMESPACE}
kubectl delete namespace ${NAMESPACE}
```
