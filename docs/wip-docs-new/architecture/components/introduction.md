# Architecture

High-level guide to llm-d architecture. Start here, then dive into specific guides.

## Core Components

llm-d has a layers architecture, providing maximum flexibility for deployment automation and infrastructure selection.

At it core, llm-d contains the following key layers:

- **Proxy** - Gateway API or Standalone Envoy Proxy coupled with an EndPointer Picker extension. It provides optimized routing and load balancing for serving Kubernetes self-hosted generative Artificial Intelligence (AI) workloads.

- **EndPoint Picker (EPP)** - An extendable component that makes selects which endpoint in the `InferencePool` is optimal for an specific inference request. The EPP is the "brains" of the scheduling decision that 

- **InferencePool** - The InferencePool API defines a group of Model Server Pods dedicated to serving AI models. Pods within an `InferencePool` share the same compute configuration, accelerator, and model server.

- **Model Server** - The Model Server (like vLLM or SGLang) runs a model on a particular node. The Model Servers can be deployed through any deployment process, joining an `InferencePool` via standard Kuberentes Labels and Selections.

![Basic architecture](../../assets/basic-architecture.svg)


## Advanced Components

- TBU
