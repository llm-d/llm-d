# llm-d Quick Start

## Overview

This quick start will walk you through the steps to install and deploy llm-d on a Kubernetes cluster and explain some of the key choices at each step as well as how to validate and remove your deployment.

## Prerequisites

### Run with sufficient permissions to deploy

Before running any deployment, ensure you have have sufficient permissions to deploy new custom resource definitions (CRDs), alter roles. Our guides are written for cluster administrators, especially for the prerequisites. Once prerequisites are configured, deploying model servers and new InferencePools typically requires only namespace editor permissions.

### Tool Dependencies

You will need to install some dependencies (like helm, yq, git, etc.) and have a HuggingFace token for most examples. We have documented these requirements and instructions in the [prereq/client-setup directory](./prereq/client-setup/README.md). To install the dependencies, use the provided [install-deps.sh](./prereq/client-setup/install-deps.sh) script.

### HuggingFace Token

A HuggingFace token is required to download models from the HuggingFace Hub. You must create a Kubernetes secret containing your HuggingFace token in the target namespace before deployment, see [instructions](./prereq/client-setup/README.md#huggingface-token).

### Gateway provider

Additionally, it is assumed you have configured and deployed your Kubernetes Gateway control plane and its prerequisite CRDs. For information see the [gateway-provider prereq](./prereq/gateway-provider/README.md).

### Target Platforms

llm-d can be deployed on a variety of Kubernetes platforms. Specific requirements, workarounds, and any other documentation relevant to these platforms will live in the [infra-providers directory](../docs/infra-providers/).

## Deployment

Select an appropriate guide from the list in the [README.md](./README.md). We recommend starting with the [inference scheduling](./inference-scheduling/README.md) well-lit path if you are looking to deploy vLLM in a recommended production serving configuration.

Navigate to the desired guide directory and follow its README instructions. For example:

```bash
cd quickstarts/guides/inference-scheduling  # Navigate to your desired example directory
# Follow the README.md instructions in the example directory
```

When you complete the deployment successfully, return here.

### Validation

You should be able to list all Helm releases to view the charts installed installed by the guide:

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
scrape targets when enabled with the appropriate Helm chart values. See [MONITORING.md](/docs/monitoring/README.md) for details.

In Kubernetes, Prometheus and Grafana can be installed from the prometheus-community
[kube-prometheus-stack helm charts](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack). In OpenShift, the built-in user workload monitoring Prometheus stack can be utilized to collect metrics.

### Uninstall

To remove llm-d resources from the cluster, refer to the uninstallation instructions in the specific guide README that you installed.
