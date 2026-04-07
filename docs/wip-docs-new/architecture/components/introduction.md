# Architecture

High-level guide to llm-d architecture. Start here, then dive into specific guides.

## Layers

llm-d has a layers architecture, providing maximum flexibility for deployment automation and infrastructure selection.

At it core, llm-d contains the following key layers:

- **Proxy** - Gateway API or Standalone Envoy Proxy coupled with an EndPointer Picker extension. It provides optimized routing and load balancing for serving Kubernetes self-hosted generative Artificial Intelligence (AI) workloads.

- **EndPoint Picker (EPP)** - An extendable component that makes selects which endpoint in the `InferencePool` is optimal for an specific inference request. The EPP is the "brains" of the scheduling decision that 

- **InferencePool** - The InferencePool API defines a group of Model Server Pods dedicated to serving AI models. Pods within an `InferencePool` share the same compute configuration, accelerator, and model server.

- **Model Server** - The Model Server (like vLLM or SGLang) runs a model on a particular node. The Model Servers can be deployed through any deployment process, joining an `InferencePool` via standard Kuberentes Labels and Selections.

![Basic architecture](../../assets/basic-architecture.svg)


## Key Decision Points

The Inference Platform and Inference Workload owners have a few key considerations in their setup:

### Which Proxy?

llm-d provides two conceptual options for the Proxy:
- **Gateway API** - Envoy proxy is deployed via Gateway API, offering clean integration into modern production-grade Kubernetes networking and routing 
- **Standalone** - Envoy proxy is deployed as a sidecar to the EPP, offering lightweight flexible deployment pattern

Gateway API deployments require the Gateway implementation to support Gateway API Inference Extension (GAIE), many of which can be found [here](https://gateway-api-inference-extension.sigs.k8s.io/implementations/gateways/). 

The llm-d project validates most Well-Lit Paths against Istio, Kgateway and GKE Gateway.

> Gateway API based deployments are recommended for online production services. Standalone deployments are intended for workloads where the machinery of Gateway API creates too much operational overhead - such as clusters leveraging Ingress, basic testing and evaluations, batch inference, and RL.

See [Proxy](proxy.md) for more details on configuration of the proxy component.

### How To Configure EPP?

The EPP is the "brains" of the llm-d deployment, making LLM-aware scheduling decisions.

---> Add some basic details about how EPP works, scorers etc
---> Add some basic YAML

See [EPP](epp.md) for more details on configuration of the EPP component.

### How To Configure The Infernce Pool Model Server?

XXX

