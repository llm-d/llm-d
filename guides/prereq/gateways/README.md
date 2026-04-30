# Gateway Guides

This directory contains guides for deploying a k8s Gateway managed proxy for the **llm-d Router**.

> [!NOTE]
> To have an end-to-end working Gateway configuration, the guides require deploying one of the [well-lit paths](../../guides/README.md).

* [GKE Gateway](./gke.md) - GKE's implementation of the Gateway API is through the GKE Gateway controller which provisions Google Cloud Load Balancers for Pods in GKE clusters. The GKE Gateway controller supports weighted traffic splitting, mirroring, advanced routing, multi-cluster load balancing and more. [Official GKE Docs](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/gateway-api).
* [Istio](./istio.md) - Istio is an open source service mesh and gateway implementation. It provides a fully compliant implementation of the Kubernetes Gateway API for cluster ingress traffic control. [Official Istio docs](https://istio.io/)
* [AgentGateway](./agentgateway.md) - Agentgateway is a high-performance, Rust-based AI gateway for LLM, MCP, and A2A workloads that can also serve as a Gateway API and Inference Gateway implementation. [Official Agentgateway docs](https://agentgateway.dev/).
