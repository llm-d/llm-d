# Proxy

This document describes the proxy level of llm-d, which recieves the initial inference request from the client.

## Introduction

llm-d leverages Envoy's [External Processing](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter) to extend production  proxies that support ext-procs into "inference-aware" proxy by offloading request scheduling to the llm-d EPP. This enables llm-d to re-use the rich existing ecosystem of high-performance, production quality proxy technologies in Kuberentes ecosystem. 

llm-d provides two conceptual options for the Proxy:
- **Gateway API** - proxy is deployed via Gateway API, offering clean integration into modern production-grade Kubernetes networking and routing 
- **Standalone** - proxy is deployed as a sidecar to the EPP, offering lightweight flexible deployment pattern

## Gateway API

[Gateway API](https://gateway-api.sigs.k8s.io/) is an official Kubernetes project focused on L4 and L7 routing in Kubernetes, representing the next generation of Kubernetes Ingress, Load Balancing, and Service Mesh APIs. From the outset, it has been designed to be generic, expressive, and role-oriented.

The [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/) leverages Envoy's External Processing to extend any gateway that supports both ext-proc and Gateway API into an inference gateway. This extension extends popular gateways like Envoy Gateway, Istio, kgateway, and GKE Gateway - to become Inference Gateway - supporting inference platform teams self-hosting Generative Models (with a current focus on large language models) on Kubernetes. This integration makes it easy to expose and control access to your local OpenAI-compatible chat completion endpoints to other workloads on or off cluster, or to integrate your self-hosted models alongside model-as-a-service providers in a higher level AI Gateways like LiteLLM, Gloo AI Gateway, or Apigee.

Conceptually, the architecture looks like this:

![Architecture](../../../assets/endpoint-picker.svg)


## Standalone