# ModelService: Declarative Inference Serving on llm-d

## Summary

**ModelService** is a Helm chart that simplifies LLM deployment on llm-d by declaratively managing Kubernetes resources for serving base models and LoRA adapters. It enables reproducible, scalable, and tunable model deployments through modular presets, and clean integration with llm-d ecosystem components (including vLLM, Gateway API Inference Extension, LeaderWorkerSet). It provides an opinionated but flexible path for deploying, benchmarking, and tuning LLM inference workloads.

## Motivation

As large language models (LLMs) become foundational to modern applications, the infrastructure required to deploy and operate them at scale has grown increasingly complex. Inference owners must coordinate a diverse set of Kubernetes resources: compute-intensive workloads, routing, RBAC, model and LoRA artifact management, multi-node orchestration, prefill-decode disaggregation, distributed KV caching, and model-specific tuning, across heterogeneous environments.

While tools like vLLM and the Gateway API Inference Extension (GIE) provide powerful primitives, they lack a unifying abstraction that declaratively binds these components into coherent, reproducible model deployments. This absence creates friction, slows onboarding, and leads to misconfiguration.

At the same time, the performance and cost-efficiency of an LLM deployment depend critically on finding the proper configuration: tensor/pipeline/expert/data parallelism, caching strategies, node placement, and LoRA composition—all of which are highly model- and hardware-specific. Without standardized support for benchmarking and configuration search, platform teams and model owners are forced to resort to ad-hoc experimentation, which is difficult to scale or automate.

ModelService addresses these challenges by defining a Helm chart that can be used to deploy all the Kubernetes resources associated with a given base model and LoRA adapters, while exposing clean integration points for benchmarking tools, workload generators, and configuration optimization frameworks. It enables platform operators to define reusable presets and inference owners to declaratively layer in overrides, converging on the best setup for their models—quickly, safely, and with repeatability.

## Goals

**Declarative model deployment:** Provide a Helm chart to declaratively manage all resources required to serve base models and LoRA adapters on llm-d, including compute workloads, routing, and RBAC configurations.

**Preset-based reusability:** Enable platform operators to define reusable model deployment presets (preset-values.yaml) and allow model owners to layer in model-specific configurations on top of existing presets (model-specific-values.yaml).

**Prefill-Decode (PD)-Disaggregation:** Support disaggregated prefill-decode deployment patterns, including heterogeneous scaling and scheduling.

**Artifact flexibility:** Support loading models and adapters from multiple sources (e.g., Hugging Face, PVC, OCI), with declarative control over caching, mounting, and LoRA injection.

**Composability with llm-d and its ecosystem:** Integrate cleanly with the Gateway API Inference Extension, distributed KV caching components, and LeaderWorkerSet for multi-node inference. Allow inference workloads to be autoscaled using Kubernetes-native tools like HPA, or custom mechanisms developed under llm-d.

**Ease of benchmarking:** Cleanly integrate with ecosystem components such as workload generators and `llm-d-benchmarking` assets to enable reliable and repeatable benchmarking of model deployment configurations.

**Ease of configuration optimization:** Seamlessly integrate with ecosystem components, such as grid search and Bayesian optimization tools for optimizing the deployment configuration.


## Non-Goals

**Going beyond llm-d (initially):** ModelService is purpose-built for llm-d, with a strict focus on model and LoRA deployment on the llm-d platform.

**Prioritize non-vLLM serving engines (initially):** llm-d follows a vLLM-first but not vLLM-only design principle. ModelService follows the same.

**Production-readiness:** ModelService is meant for getting started on llm-d, learning, experimentation, benchmarking, configuration tuning, development, and research. It is not meant to be a production-grade model deployment solution for llm-d, or offer advanced orchestration and lifecycle features.

## Proposal

This proposal introduces **ModelService** as a Helm chart for serving base models and LoRA adapters on the llm-d platform. The Helm chart will encapsulate all relevant components (deployments, services, RBAC, EPP, inference model, and pool) into a single coherent package. Users will be able to enable/disable specific components (e.g., prefill, decode) via flags in `values.yaml`.

The Helm chart supports the declarative deployment of base models and LoRA adapters using flexible artifact sources (Hugging Face, PVC, OCI), disaggregated prefill-decode workloads, and multi-node inference with LeaderWorkerSets. HPA integration will be supported via conditional template blocks and `values.yaml`-driven configuration. The Helm chart will also allow test-specific ModelService configurations to be deployed in isolation for benchmarking performance and cost efficiency. In particular, it will play well with llm-d-benchmark processes and tooling. Finally, through its values schema, it will expose clean integration points for external configuration search tools (e.g., grid search, Bayesian optimization), enabling automated exploration of parameters like parallelism levels and KV caching strategies.

The ModelService Helm chart will only deploy namespace-scoped resources. Helm’s layering model allows platform owners to define reusable `preset-values.yaml` for platform defaults (e.g., accelerator type, sidecars, readiness), while model owners can supply `model-values.yaml` to override only the specifics (e.g., artifact URI, replicas, args). This structure encourages safe customization and sharing. Any last-mile modifications can also be handled using [kustomize's built-in helm chart support](https://github.com/kubernetes-sigs/kustomize/blob/master/examples/chart.md).

Overall, the Helm-based architecture will simplify onboarding, enable reproducibility, and provide a robust foundation for experimentation and configuration search on llm-d—paving the way toward a more production-ready deployment system in the future.


## Implementation status

An initial prototype of ModelService is [here](https://github.com/llm-d/llm-d-model-service/tree/main), and it was featured as part of the llm-d launch demos at Red Hat Summit, 2025. The initial design documented in this repo (as of May 29th, 2025; the time of writing this proposal) is based on a ModelService Kubernetes operator (CRD + controller). Our plan is to migrate from the CRD approach to a Helm chart.

## Success criteria

The project should graduate from “incubation” to “core” after demonstrating the following.

* Well-documented samples for a few well-known models that cover whole GPU, tensor/expert/data/node parallel scenarios in vLLM across heterogeneous hardware.
* Samples that demonstrate static LoRA adapter registration and loading.
* Well-documented integration with the llm-d-benchmark tooling and process.

## Alternative: Manual Deployment

Manual composition via raw Kubernetes resources:  One possible approach is for platform or model owners to hand-craft Deployments, Services, ConfigMaps, InferencePool, HTTPRoute, and RBAC configurations. While this provides flexibility, it significantly increases complexity and error surface, especially for PD-disaggregated and multi-node inference deployments.

## Alternative: Extending KServe

KServe provides a general-purpose model serving abstraction but was originally designed for traditional predictive models; it now provides several LLM specific features as of release 0.15 (see [this blogpost](https://kserve.github.io/website/blog/kserve-0.15-release) for details). As we evolve the ModelService Helm chart, we also plan to collaborate with the KServe community to develop production-grade KServe mechanisms that integrate well with llm-d. This collaboration will support a variety of useful features, including prefill/decode disaggregation, dynamic LoRA loading, and configuration search and tuning.

## Acknowledgements

This proposal has been shaped by inputs from several members of llm-d and kserve communities in the leadup to llm-d launch and beyond. (See [this gdoc for a some comments](https://docs.google.com/document/d/1HA-2yNZpc1F4KhyeYA30shjZpYEDqGIJXqVgDVv3SWU/edit?usp=sharing)).

A partial list of these members (in alphabetical order) is as follows.

1. Abhishek Malvankar
2. Chen Wang
3. Greg Pereira
4. Jing Chen
5. Lionel Villard
6. Michael Kalantar
7. Pravein Govindan Kannan
8. Srinivasan Parthasarathy
9. Tamar Eilam
10. Tom Coufal
11. Yuan Tang
