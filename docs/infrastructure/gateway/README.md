# Gateway Guides

This directory contains guides for deploying a Kubernetes Gateway as a proxy for the **llm-d Router**.

> [!NOTE]
> Before deploying a Gateway provider, install the required CRDs using the [CRD installation guide](./install-crds.md).

> [!NOTE]
> To have an end-to-end working Gateway configuration, the guides require deploying one of the [well-lit paths](../../well-lit-paths/README.md).

## Why do you need a Gateway?

The **llm-d Router** provides an extension to [compatible Gateway providers](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/) that optimizes load balancing of LLM traffic across model server replicas.

The integration with a Gateway allows self-hosted models to be exposed in a [wide variety of network topologies including](https://gateway-api.sigs.k8s.io/concepts/use-cases/):

* Internet-facing services
* Internal to your cluster
* Through a service mesh

and take advantage of key Gateway features like:

* Traffic splitting for incremental rollout of new models
* TLS encryption of queries and responses

By integrating with a Gateway -- instead of developing an llm-d specific proxy layer -- llm-d can leverage the high performance of mature proxies and take advantage of existing operational tools for managing traffic to services. Compatible Gateway implementations may use proxies like [Envoy](https://www.envoyproxy.io/) or other high-performance data planes under the hood.

## Overview

The key elements of llm-d's Gateway integration are:

* The **llm-d Endpoint Picker (EPP)** is an external processing service that a Kubernetes Gateway consults to decide which model server a given request should go to
* The **`InferencePool` Custom Resource** that includes the spec for Kubernetes Gateway controllers to provision an llm-d Endpoint Picker as an inference extension to a Kubernetes Gateway
* The **Gateway Custom Resources** that define the Kubernetes-native Gateway API and how traffic reaches an `InferencePool`
* A **compatible Gateway implementation (control plane)** that provisions and configures load balancers and endpoint pickers in response to the Gateway API and InferencePool API

After completing these gateway setup steps, you will be able to create `InferencePool` objects on your cluster and route traffic to them.

> [!NOTE]
> Setting up a Gateway generally requires cluster administration rights.

## Supported Gateway Providers

llm-d requires you select a [Gateway implementation that supports the Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/). Your infrastructure may provide a default compatible implementation, or you may choose to deploy a gateway implementation onto your cluster.

* [GKE Gateway](./gke.md) - GKE's implementation of the Gateway API is through the GKE Gateway controller which provisions Google Cloud Load Balancers for Pods in GKE clusters. The GKE Gateway controller supports weighted traffic splitting, mirroring, advanced routing, multi-cluster load balancing and more. [Official GKE Docs](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/gateway-api).
* [Istio](./istio.md) - Istio is an open source service mesh and gateway implementation. It provides a fully compliant implementation of the Kubernetes Gateway API for cluster ingress traffic control. [Official Istio docs](https://istio.io/)
* [Agentgateway](./agentgateway.md) - Agentgateway is a high-performance, Rust-based AI gateway for LLM, MCP, and A2A workloads that can also serve as a Gateway API and Inference Gateway implementation. [Official Agentgateway docs](https://agentgateway.dev/).
* [Envoy AI Gateway](./envoy-ai-gateway.md) - Envoy AI Gateway is an open source project for using Envoy Gateway to handle request traffic from application clients to GenAI services that can also serve as a Gateway API and Inference Gateway implementation. [Official Envoy AI Gateway docs](https://aigateway.envoyproxy.io/).

## Other Providers

For other [compatible Gateway implementations](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/) not listed above, follow the installation instructions for your selected Gateway provider. Ensure the necessary CRDs for Gateway API and the Gateway API Inference Extension are installed.
