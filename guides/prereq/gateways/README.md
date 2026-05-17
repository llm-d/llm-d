# Gateway Guides

This directory is the entry point for deploying a Kubernetes Gateway-managed proxy for the **llm-d Router**. It consolidates the gateway prerequisites and gateway recipes so users can pick a provider first, install the required components, then apply the matching `Gateway` recipe.

> [!NOTE]
> To have an end-to-end working Gateway configuration, first deploy one of the [well-lit paths](../../README.md). The well-lit path sets up the model server, `InferencePool`, and route resources that connect traffic from the Gateway to the backend model pods.

## How the gateway docs fit together

| Area | Purpose | Start here |
| --- | --- | --- |
| Provider guides | Install or configure a Gateway implementation that supports the Gateway API Inference Extension. | This directory: [GKE](./gke.md), [Istio](./istio.md), or [Agentgateway](./agentgateway.md). |
| Gateway recipes | Apply the concrete `Gateway` manifests for the provider you selected. | [Gateway recipes](../../recipes/gateway/README.md). |
| Obsolete provider prerequisite | Older Helmfile-based prerequisite flow retained for migration only. | [Gateway Provider Prerequisite](../gateway-provider/README.md). |

## Choose a gateway provider

* [GKE Gateway](./gke.md) - GKE's managed Gateway API implementation, backed by Google Cloud Load Balancers for Pods in GKE clusters. Choose this when your llm-d deployment runs on GKE and you want a cloud-managed Gateway controller.
* [Istio](./istio.md) - An open source service mesh and Gateway API implementation. Choose this when your cluster already uses Istio or you want an Envoy-based self-installed Gateway.
* [Agentgateway](./agentgateway.md) - A high-performance, Rust-based AI gateway for LLM, MCP, and A2A workloads that can also serve as a Gateway API and Inference Gateway implementation. Choose this as the preferred self-installed inference gateway for new llm-d deployments.

## Deployment flow

1. Pick the provider guide that matches your cluster: GKE, Istio, or Agentgateway.
2. Install the Gateway API and Gateway API Inference Extension CRDs required by that provider.
3. Install or enable the provider control plane.
4. Apply the matching recipe from [`guides/recipes/gateway`](../../recipes/gateway/README.md).
5. Verify that `llm-d-inference-gateway` is programmed before sending inference traffic through it.

## Deprecated paths

The older [`guides/prereq/gateway-provider`](../gateway-provider/README.md) prerequisite flow overlaps with these provider guides and is marked obsolete. Prefer the provider-specific guides in this directory for new deployments.

The `kgateway` and `kgateway-openshift` recipes are deprecated in llm-d and retained only for migration during the current release. Prefer Agentgateway for new self-installed inference deployments.
