# Gateway Provider Prerequisite

This document will guide you through configuring a [Kubernetes Gateway](https://gateway-api.sigs.k8s.io/) provider that can support the llm-d [`inference-scheduler`](https://github.com/llm-d/llm-d-inference-scheduler) component.

The key elements are:

* The `inference-scheduler` is an **endpoint picker (EPP)** that decides which model server a given request should go to
* The **Inference Gateway `InferencePool` Custom Resource** that provisions and configures an `inference-scheduler` on a Kubernetes cluster
* The **Gateway Custom Resources** that define the Kubernetes-native Gateway API and how traffic reaches an `InferencePool`
* A **compatible Gateway implementation (control plane)** that provisions and configures load balancers and endpoint pickers in response to the Gateway API and InferencePool API

After this prerequisite is complete you will be able to create `InferencePool` objects on cluster and route traffic to them.

This prerequisite generally requires cluster administration rights.

## Why do you need a Gateway?

The inference scheduler provides an extension to [compatible Gateway providers](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/) that optimizes load balancing of LLM traffic across model server replicas.

The integration with a Gateway allows self-hosted models to be exposed in a [wide variety of network topologies including](https://gateway-api.sigs.k8s.io/concepts/use-cases/):

* Internet-facing services
* Internal to your cluster
* Through a service mesh

and take advantage of key Gateway features like:

* Traffic splitting for incremental rollout of new models
* TLS encryption of queries and responses

By integrating with a Gateway -- instead of developing an llm-d specific proxy layer -- llm-d can leverage the high performance of mature proxies like [Envoy](https://www.envoyproxy.io/) and take advantage of existing operational tools for managing traffic to services.

## Select and install an `inference-scheduler` compatible Gateway implementation

llm-d requires you select a [Gateway implementation that supports the inference-scheduler](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/). Your infrastructure may provide a default compatible implementation, or you may choose to deploy a gateway implementation onto your cluster.

### Use an infrastructure provided Gateway implementation

We recommend using the infrastructure provided Gateway with our guides if available.

#### Google Kubernetes Engine (GKE)

GKE automatically enables an inference-compatible Gateway control plane when you enable the `HttpLoadBalancing` addon.  

The key choice for deployment is whether you want to create a regional internal Application Load Balancer - accessible only workloads within your VPC (class name: `gke-l7-rilb`) - or a regional external Application Load Balancer - accessible to the internet (class name: `gke-l7-regional-external-managed`).

The following steps from the [GKE Inference Gateway deployment documentation](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway) should be run:

1. [Verify your prerequisites](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#before-you-begin)
2. [Prepare your environment](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#prepare-environment)
3. [Create the Gateway](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway#create-gateway)

The other steps are optional and are not necessary to continue with your guide.

### Self-installed Gateway implementations

llm-d provides a Helm chart that installs and configures the `kgateway` or `istio` Gateway implementations.

#### Before you begin

Prior to deploying a Gateway control plane, you must install the custom resource definitions (CRDs) configuration that adds the Kubernetes API objects:

- [Gateway API v1.3.0 CRDs](https://github.com/kubernetes-sigs/gateway-api/tree/v1.3.0/config/crd)
  - for more information see their [docs](https://gateway-api.sigs.k8s.io/guides/)
- [Gateway API Inference Extension CRDs v1.0.1](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/v1.0.1/config/crd)
  - for more information see their [docs](https://gateway-api-inference-extension.sigs.k8s.io/)

We have provided the [`install-gateway-provider-dependencies.sh`](./install-gateway-provider-dependencies.sh) script:

```bash
./install-gateway-provider-dependencies.sh
```

To remove the created dependencies: 

```bash
./install-gateway-provider-dependencies.sh delete
```

You may specify any valid git source control reference for versions as `GATEWAY_API_CRD_REVISION` and `GATEWAY_API_INFERENCE_EXTENSION_CRD_REVISION`:

```bash
export GATEWAY_API_CRD_REVISION="v1.2.0"
export GATEWAY_API_INFERENCE_EXTENSION_CRD_REVISION="v0.5.0"
./install-gateway-provider-dependencies.sh
```

#### Installation

To install the gateway control plane:

```bash
helmfile apply -f <your_gateway_choice>.helmfile.yaml # options: [`istio`, `kgateway`]
# ex: helmfile apply -f istio.helmfile.yaml
```

#### Targeted install

If the CRDs already exist in your cluster and you do not wish to re-apply them, use the `--selector kind=gateway-control-plane` selector to limit your changes to the infrastructure:

```bash
# Install
helmfile apply -f <your_gateway_choice> --selector kind=gateway-control-plane
# Uninstall
helmfile destroy -f <your_gateway_choice> --selector kind=gateway-control-plane
```

If you wish to bump versions or customize your installs, check out our helmfiles for [istio](./istio.helmfile.yaml), and [kgateway](./kgateway.helmfile.yaml) respectively.

### Other Gateway implementations

For other [compatible Gateway implementations](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/) follow the instructions for your selected Gateway. Ensure the necessary CRDs for Gateway API and the Gateway API Inference Extension are installed.

## Verify your installation

Once the prerequisite steps are complete, you should be able to verify that `InferencePool` is installed on your cluster with:

```bash
# Verify the v1 APIs are installed, specifically InferencePool
kubectl api-resources --api-group=inference.networking.k8s.io
# Verify other APIs are installed
kubectl api-resources --api-group=inference.networking.x-k8s.io
```

If successful, the first command should return at least the `v1` version of `InferencePool`, and you should also see a `v1alpha2` or newer version of `InferenceObjective`.
