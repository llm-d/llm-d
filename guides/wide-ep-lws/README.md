# Well-lit Path: Wide Expert Parallelism (EP/DP) with LeaderWorkerSet

## Overview

This guide demonstrates how to deploy DeepSeek-R1-0528 using vLLM's P/D disaggregation support with NIXL in a wide expert parallel pattern with LeaderWorkerSets. This guide has been validated on a cluster with 24xH200 GPUs split across three nodes with InfiniBand networking.

> WARNING: We are still investigating and optimizing performance for other hardware and networking configurations

In this example, we will demonstrate a deployment of `DeepSeek-R1-0528` with:

- 1 DP=8 Prefill Workers
- 2 DP=8 Decode Workers

## Hardware Requirements

This guide requires 24 Nvidia H200 GPUs and InfiniBand RDMA. It requires 1024 Gi of memory across and 128 Gi of ephemeral storage all 3 pods (512 Gi memory and 64 Gi storage for both Decode pods together and 512 Gi memory and 64 Gi storage for the prefill pod).

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../prereq/infrastructure/README.md)
  - You must have high speed inter-accelerator networking
  - The pods leveraging inter-node EP must be deployed within the same networking domain
  - You have deployed the [LeaderWorkerSet optional controller](../prereq/infrastructure/README.md#optional-install-leaderworkerset-for-multi-host-inference)
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md).
- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.
- Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.

## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-wide-ep` in this example.

```bash
export NAMESPACE=llm-d-wide-ep # or any other namespace
cd guides/wide-ep-lws/
kubectl create namespace ${NAMESPACE}
```

### Deploy Model Servers

GKE and CoreWeave are tested Kubernetes providers for this well-lit path. You can customize the manifests if you run on other Kubernetes providers.

```bash
# Deploy on GKE
kubectl apply -k ./manifests/modelserver/gke -n ${NAMESPACE}

# OR, deploy on CoreWeave
kubectl apply -k ./manifests/modelserver/coreweave  -n ${NAMESPACE}
```

### Deploy InferencePool

```bash
# For GKE
helm install deepseek-r1 \
  -n ${NAMESPACE} \
  -f inferencepool.values.yaml \
  --set "provider.name=gke" \
  --set "inferencePool.apiVersion=inference.networking.k8s.io/v1" \
  --set "inferenceExtension.monitoring.gke.enable=true" \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool --version v1.0.1

# For Istio
helm install deepseek-r1 \
  -n ${NAMESPACE} \
  -f inferencepool.values.yaml \
  --set "provider.name=istio" \
  --set "inferenceExtension.monitoring.prometheus.enable=true" \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool --version v1.0.1

# For Kgateway
helm install deepseek-r1 \
  -n ${NAMESPACE} \
  -f inferencepool.values.yaml \
  oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool --version v1.0.1
```

### Deploy Gateway and HTTPRoute

```bash
# Deploy a gke-l7-regional-external-managed gateway.
kubectl apply -k ./manifests/gateway/gke-l7-regional-external-managed -n ${NAMESPACE}

# Deploy an Istio gateway.
kubectl apply -k ./manifests/gateway/istio -n ${NAMESPACE}

# Deploy a kgateway gateway.
kubectl apply -k ./manifests/gateway/kgateway -n ${NAMESPACE}

# Deploy a kgateway gateway on Openshift Container Platform (OCP).
kubectl apply -k ./manifests/gateway/kgateway-openshift -n ${NAMESPACE}
```

### Gateway options

To see what gateway options are supported refer to our [gateway provider prereq doc](../prereq/gateway-provider/README.md#supported-providers). Gateway configurations per provider are tracked in the [gateway-configurations directory](../prereq/gateway-provider/common-configurations/).

You can also customize your gateway, for more information on how to do that see our [gateway customization docs](../../docs/customizing-your-gateway.md).

## Tuning Selective PD

As with PD, the `wide-ep-lws` guide supports selective PD. For information on this refer to [this section of the PD docs](../pd-disaggregation/README.md#tuning-selective-pd).

## Verifying the installation

- Firstly, you should be able to list all helm releases installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME            NAMESPACE       REVISION    UPDATED                                 STATUS      CHART                       APP VERSION
deepseek-r1     llm-d-wide-ep   1           2025-08-24 13:14:53.355639 -0700 PDT    deployed    inferencepool-v1.0          v0.3.0
```

- Out of the box with this example you should have the following resources (if using Istio):

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                         READY   STATUS    RESTARTS   AGE
pod/infra-wide-ep-inference-gateway-istio-74d5c66c86-h5mfn   1/1     Running   0          2m22s
pod/wide-ep-llm-d-decode-0                   2/2     Running   0          2m13s
pod/wide-ep-llm-d-decode-0-1                 2/2     Running   0          2m13s
pod/deepseek-r1-epp-84dd98f75b-r6lvh         1/1     Running   0          2m14s
pod/wide-ep-llm-d-prefill-0                  1/1     Running   0          2m13s

NAME                                            TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)                        AGE
service/infra-wide-ep-inference-gateway-istio   ClusterIP      10.16.1.34    10.16.4.2     15021:30312/TCP,80:33662/TCP   2m22s
service/wide-ep-ip-1e480070                     ClusterIP      None          <none>        54321/TCP                      2d4h
service/wide-ep-llm-d-decode                    ClusterIP      None          <none>        <none>                         2m13s
service/deepseek-r1-epp                         ClusterIP      10.16.1.137   <none>        9002/TCP                       2d4h
service/wide-ep-llm-d-prefill                   ClusterIP      None          <none>        <none>                         2m13s

NAME                                                    READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/infra-wide-ep-inference-gateway-istio   1/1     1            1           2m22s
deployment.apps/deepseek-r1-epp       1/1     1            1           2m14s

NAME                                                               DESIRED   CURRENT   READY   AGE
replicaset.apps/infra-wide-ep-inference-gateway-istio-74d5c66c86   1         1         1       2m22s
replicaset.apps/deepseek-r1-epp-55bb9857cf       1         1         1       2m14s

NAME                                                      READY   AGE
statefulset.apps/wide-ep-llm-d-decode     1/1     2m13s
statefulset.apps/wide-ep-llm-d-decode-0   1/1     2m13s
statefulset.apps/wide-ep-llm-d-prefill    1/1     2m13s
```

**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}` and you have not changed the default release names via the `${RELEASE_NAME}` environment variable.

## Using the stack

For instructions on getting started making inference requests see [our docs](../../docs/getting-started-inferencing.md)

**_NOTE:_** This example particularly benefits from utilizing stern as described in the [getting-started-inferencing docs](../../docs/getting-started-inferencing.md#following-logs-for-requests), because while we only have 3 inferencing pods, it has 16 vllm servers or ranks.

**_NOTE:_** Compared to the other examples, this one takes anywhere between 7-10 minutes for the vllm API servers to startup so this might take longer before you can interact with this example.

## Cleanup

To remove the deployment:

```bash
# From examples/wide-ep-lws
helm uninstall deepseek-r1 -n ${NAMESPACE}
kubectl delete -k ./manifests/modelserver/<gke|coreweave> -n ${NAMESPACE}
kubectl delete -k ./manifests/gateway/<gke-l7-regional-external-managed|istio|kgateway|kgateway-openshift> -n ${NAMESPACE}
```

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../docs/customizing-a-guide.md)
