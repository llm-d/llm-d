# Gateway Provider Prerequisite

This document will guide you through choosing a Gateway provider that can support the llm-d `inference-scheduler` component.

This prerequisite generally requires cluster administration rights.

## Before you begin

Prior to deploying a Gateway control plane, you must install the custom resource definitions (CRDs) that add the Kubernetes API objects:

- [Gateway API v1.3.0 CRDs](https://github.com/kubernetes-sigs/gateway-api/tree/v1.3.0/config/crd)
  - for more information see their [docs](https://gateway-api.sigs.k8s.io/guides/)
- [Gateway API Inference Extension CRDs v0.5.1](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/v0.5.1/config/crd)
  - for more information see their [docs](https://gateway-api-inference-extension.sigs.k8s.io/)

We have provided the [`install-gateway-provider-dependencies.sh`](./install-gateway-provider-dependencies.sh) script to handle that deployment:

```bash
./install-gateway-provider-dependencies.sh
```

To remove the created dependencies: 

```bash
./install-gateway-provider-dependencies.sh delete`
```

You may specify any valid git source control reference for versions as `GATEWAY_API_CRD_REVISION` and `GATEWAY_API_INFERENCE_EXTENSION_CRD_REVISION`:

```bash
export GATEWAY_API_CRD_REVISION="v1.2.0"
export GATEWAY_API_INFERENCE_EXTENSION_CRD_REVISION="v0.5.0"
./install-gateway-provider-dependencies.sh
```

## Supported Providers

`llm-d` guides cover the following Gateway providers. See the [inference gateway documentation](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/) for a complete list of supported Gateways.

- `kgateway`
- `istio`
- `gke` *

> [!IMPORTANT]
> While llm-d supports GKE Gateways, it comes setup out of the box on GKE, and so no action is required to deploy the control plane. If you are using GKE you may skip this document.

## Installation

To install the gateway control plane:

```bash
helmfile apply -f <your_gateway_choice>.helmfile.yaml # options: [`istio`, `kgateway`]
# ex: helmfile apply -f istio.helmfile.yaml
```

### Targeted install

If the CRDs already exist in your cluster and you do not wish to re-apply them, use the `--selector kind=gateway-control-plane` selector to limit your changes to the infrastructure:

```bash
# Install
helmfile apply -f <your_gateway_choice> --selector kind=gateway-control-plane
# Uninstall
helmfile destroy -f <your_gateway_choice> --selector kind=gateway-control-plane
```

If you wish to bump versions or customize your installs, check out our helmfiles for [istio](./istio.helmfile.yaml), and [kgateway](./kgateway.helmfile.yaml) respectively.
