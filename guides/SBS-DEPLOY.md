# llm-d step by step deployment

## Overview

This document guides you through deploying shared components for the various functions listed below, allowing you to apply only the necessary changes.


- Intelligent Inference Scheduling
- Prefill/Decode Disaggregation
- Wide Expert-Parallelism
- Tiered Prefix Cache

## Prerequisites

### Configuring necessary infrastructure and your cluster

llm-d can be deployed on a variety of Kubernetes distributions and managed providers. The [infrastructure prerequisite](./prereq/infrastructure/README.md) will help you ensure your cluster is properly configured with the resources necessary to run LLM inference.

Specific requirements, workarounds, and any other documentation relevant to these platforms can be reviewed in the [infra-providers directory](../docs/infra-providers/). 

### Run with sufficient permissions to deploy

Before running any deployment, ensure you have sufficient permissions to deploy new custom resource definitions (CRDs) and alter roles. Our guides are written for cluster administrators, especially for the prerequisites. Once prerequisites are configured, deploying model servers and new InferencePools typically requires only namespace editor permissions.

> [!IMPORTANT]
> llm-d recommends separating infrastructure configuration -- like the inference gateway -- from workload deployment. Inference platform administrators are responsible for managing the cluster and dependencies while inference workload owners deploy and manage the lifecycle of the self-hosted model servers.
>
> The separation between these roles depends on the number of workloads present in your environment. A single production workload might see the same team managing all the software. In a large Internal Model as a Service deployment, the platform team might manage shared inference gateways and allow individual workload teams to directly manage the configuration and deployment of large model servers. See [the Inference Gateway docs](https://gateway-api-inference-extension.sigs.k8s.io/concepts/roles-and-personas/) for more examples of the role archetypes.

### Tool Dependencies

You will need to install some dependencies (like kubectl, helm, yq, git, etc.) and have a HuggingFace token for most examples. We have documented these requirements and instructions in the [prereq/client-setup directory](./prereq/client-setup/README.md). To install the dependencies, use the provided [install-deps.sh](./prereq/client-setup/install-deps.sh) script.

> [!IMPORTANT]
> We anticipate that almost all production deployments will leverage configuration management automation, GitOps, or CI/CD pipelines to automate repeatable deployments. Most users have an opinion about how to deploy workloads and there is high variation in the needs of the model server deployment. llm-d therefore minimizes the amount of tooling and parameterization in our guides and prioritizes demonstrating complete examples and concepts to allow you to adapt our configuration to your use case.


## Shared components

### Gateway provider (GatewayAPI Provider + GatewayAPI Inference Extension)

llm-d integrates with the [Kubernetes Gateway API](https://gateway-api.sigs.k8s.io/) to optimize load balancing to your model server replicas and have access to the full set of service management features you are likely to need in production, such as traffic splitting and authentication / authorization.

You must select an [appropriate Gateway implementation for your infrastructure and deploy the Gateway control plane and its prerequisite CRDs](./prereq/gateway-provider/README.md).

> [!IMPORTANT]
> We recommend selecting a Gateway implementation provided by your infrastructure, if available. If not, we test and verify our guides with both [kgateway](https://kgateway.dev/docs/main/quickstart/) and [istio](https://istio.io/latest/docs/setup/getting-started/).

### Monitoring Stack (kube-prometheus-stack and Grafana)

llm-d charts include support for metrics collection from vLLM pods. llm-d applies PodMonitors to trigger Prometheus
scrape targets when enabled with the appropriate Helm chart values. See [MONITORING.md](../docs/monitoring/README.md) for details.

In Kubernetes, Prometheus and Grafana can be installed from the prometheus-community
[kube-prometheus-stack helm charts](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack). In OpenShift, the built-in user workload monitoring Prometheus stack can be utilized to collect metrics.

> [!IMPORTANT]
> We strongly recommend enabling monitoring and observability of llm-d components. LLM inference can bottleneck in multiple ways and troubleshooting performance may involve inspecting gateway, vLLM, OS, and hardware level metrics.



### Namespace for installation

Create a namespace to install llm-d.

```
export NAMESPACE=llm-d-system # or any other namespace (shorter names recommended)
kubectl create namespace ${NAMESPACE}
```

### HuggingFace Token

A HuggingFace token is required to download models from the HuggingFace Hub. You must create a Kubernetes secret containing your HuggingFace token in the target namespace before deployment, see [instructions](./prereq/client-setup/README.md#huggingface-token).

> [!IMPORTANT]
> vLLM by default will load models from HuggingFace as needed. Since in production environments downloading models is a source of startup latency and a potential point of failure (if the model provider is down), most deployments should cache downloads across multiple restarts and host copies of their models within the same failure domain as their replicas.

### llm-d-infra

To begin with, you have to install Helm repository on your cluster.

```
helm repo add llm-d-infra https://llm-d-incubation.github.io/llm-d-infra/
helm repo update
```

After that you have to create the values file defining the gateway class name deployed on your own cluster.

The example below is for the gateway provider we tested and verified.

```
gateway:
  gatewayClassName: <kgateway / istio>
```

Then install llm-d-infra with the values.yaml file.

```
helm install llm-d-infra llm-d-infra/llm-d-infra -f values.yaml -n ${NAMESPACE}
```


## Deploy each features

Select an appropriate guide from the list in the [README.md](./README.md).

You must create the values.yaml files for GAIE and llm-d-modelservice to align with the selected features before deploying.

Before starting deploy them, you have to add the repository for llm-d-modelservice.

```
helm repo add llm-d-modelservice https://llm-d-incubation.github.io/llm-d-modelservice/
helm repo update
```

> [!IMPORTANT]
> Always verify the contents of the values.yaml files for GAIE and llm-d-modelservice, as well as helmfile.yaml.gotmpl

### Example

Take [Intelligent Inference Scheduling](https://github.com/llm-d/llm-d/tree/main/guides/inference-scheduling) deployed on vanila Kubernetes as an example.

#### 1. GAIE

Prepare the values.yaml file.

You have to look at [helmfile.yaml.gotmpl](https://github.com/llm-d/llm-d/blob/202beece2cea62d2d8e4a40ce3bb45bbb76a46ca/guides/inference-scheduling/helmfile.yaml.gotmpl#L57) and [values.yaml for gaie-inference-scheduling
](https://github.com/llm-d/llm-d/blob/main/guides/inference-scheduling/gaie-inference-scheduling/values.yaml) before deploying it to align with the latest schema.


[Example (Using istio as an Gateway Provider)](./sts-deploy-example/gaie-inference-scheduling.yaml)

> [!IMPORTANT]
> `provider.istio.destinationRule.host` is set `llm-d-gaie-epp.llm-d-system.svc.cluster.local`. This have to be set along with your helm release name and namespace name like `<Helm Release name>.<Namespace name>.svc.cluster.local`.

Then deploy GAIE components with helm.

```
IGW_CHART_VERSION=$(curl -s https://api.github.com/repos/kubernetes-sigs/gateway-api-inference-extension/releases \
  | jq -r '.[] | select(.prerelease == false) | .tag_name' \
  | sort -V \
  | tail -n1)

helm install llm-d-gaie oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
-f values.yaml --version $IGW_CHART_VERSION --namespace $NAMESPACE
```

#### 2. llm-d-modelservice

Prepare the values.yaml file.

You have to look at [helmfile.yaml.gotmpl](https://github.com/llm-d/llm-d/blob/202beece2cea62d2d8e4a40ce3bb45bbb76a46ca/guides/inference-scheduling/helmfile.yaml.gotmpl#L97) and [values.yaml for ms-inference-scheduling
](https://github.com/llm-d/llm-d/blob/main/guides/inference-scheduling/ms-inference-scheduling/values.yaml) before deploying it to align with the latest schema.


[Example (Using Qwen/Qwen3-0.6B on 1 NVIDIA GPU in 2 Replica)](./sts-deploy-example/ms-inference-scheduling-values.yaml)

Then deploy llm-d-modelservice with helm.

```
helm install llm-d-ms llm-d-modelservice/llm-d-modelservice \
-f values.yaml --namespace $NAMESPACE
```

#### 3. HTTPRoute

Prepare the manifest file.

You have to look at [httproute.yaml](https://github.com/llm-d/llm-d/blob/main/guides/inference-scheduling/httproute.yaml) before applying it to align with the latest schema.

[Example](./sts-deploy-example/httproute.yaml)

> [!IMPORTANT]
> `spec.parentRefs[].name` is set `llm-d-infra-inference-gateway`. This have to be set along with your helm release name of llm-d-infra like `<llm-d-infra's Helm Release name>-inference-gateway`.
> And `spec.rules[].backendRefs[].name` is set `llm-d-gaie`. This have to be set along with your helm release name of GAIE.

Then apply it.

```
kubectl apply -f httproute.yaml -n $NAMESPACE
```

### Validation

You should be able to list all Helm releases to view the charts installed by the guide:

```bash
helm list -n ${NAMESPACE}
```

You can view all resources in your namespace with:

```bash
kubectl get all -n ${NAMESPACE}
```

**Note:** This assumes no other guide deployments in your given `${NAMESPACE}`.

### Making inference requests to your deployments

For instructions on getting started with making inference requests, see [getting-started-inferencing.md](../docs/getting-started-inferencing.md).


### Uninstall

To remove llm-d resources from the cluster, refer to the uninstallation instructions in your selected guide README.
