# llm-d Quick Start

## Overview

This quick start will walk you through the steps to install and deploy llm-d on a Kubernetes cluster and explain some of the key choices at each step as well as how to validate and remove your deployment.

## Prerequisites

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

### HuggingFace Token

A HuggingFace token is required to download models from the HuggingFace Hub. You must create a Kubernetes secret containing your HuggingFace token in the target namespace before deployment, see [instructions](./prereq/client-setup/README.md#huggingface-token).

> [!IMPORTANT]
> vLLM by default will load models from HuggingFace as needed. Since in production environments downloading models is a source of startup latency and a potential point of failure (if the model provider is down), most deployments should cache downloads across multiple restarts and host copies of their models within the same failure domain as their replicas.

### Configuring necessary infrastructure and your cluster

llm-d can be deployed on a variety of Kubernetes distributions and managed providers. The [infrastructure prerequisite](./prereq/infrastructure/README.md) will help you ensure your cluster is properly configured with the resources necessary to run LLM inference.

Specific requirements, workarounds, and any other documentation relevant to these platforms can be reviewed in the [infra-providers directory](../docs/infra-providers/). 

### Gateway provider

llm-d integrates with the [Kubernetes Gateway API](https://gateway-api.sigs.k8s.io/) to optimize load balancing to your model server replicas and have access to the full set of service management features you are likely to need in production, such as traffic splitting and authentication / authorization.

You must select an [appropriate Gateway implementation for your infrastructure and deploy the Gateway control plane and its prerequisite CRDs](./prereq/gateway-provider/README.md).

> [!IMPORTANT]
> We recommend selecting a Gateway implementation provided by your infrastructure, if available. If not, we test and verify our guides with both [kgateway](https://kgateway.dev/docs/main/quickstart/) and [istio](https://istio.io/latest/docs/setup/getting-started/).

## Deployment

Select an appropriate guide from the list in the [README.md](./README.md).

> [!IMPORTANT]
> We recommend starting with the [inference scheduling](./inference-scheduling/README.md) well-lit path if you are looking to deploy vLLM in a recommended production serving configuration. Use of an intelligent load balancer is broadly applicable to all environments and streamlines gathering the most critical operational metrics.

Navigate to the desired guide directory and follow its README instructions. For example:

```bash
cd quickstarts/guides/inference-scheduling  # Navigate to your desired example directory
# Follow the README.md instructions in the example directory
```

When you complete the deployment successfully, return here.

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

### Metrics collection

llm-d charts include support for metrics collection from vLLM pods. llm-d applies PodMonitors to trigger Prometheus
scrape targets when enabled with the appropriate Helm chart values. See [MONITORING.md](../docs/monitoring/README.md) for details.

In Kubernetes, Prometheus and Grafana can be installed from the prometheus-community
[kube-prometheus-stack helm charts](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack). In OpenShift, the built-in user workload monitoring Prometheus stack can be utilized to collect metrics.

> [!IMPORTANT]
> We strongly recommend enabling monitoring and observability of llm-d components. LLM inference can bottleneck in multiple ways and troubleshooting performance may involve inspecting gateway, vLLM, OS, and hardware level metrics.

### Uninstall

To remove llm-d resources from the cluster, refer to the uninstallation instructions in your selected guide README.
