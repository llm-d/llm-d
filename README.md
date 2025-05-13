<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Logo" src="./docs/assets/images/llm-d-logo.png" width=35%>
  </picture>
</p>

<h3 align="center">
Powering Distributed Gen AI Inference at Scale.
</h3>

 [![Documentation](https://img.shields.io/badge/Documentation-8A2BE2?logo=read-the-docs&logoColor=%23ffffff&color=%231BC070)](https://...) [![License](https://img.shields.io/github/license/neuralmagic/guidellm.svg)](https://github.com/neuralmagic/guidellm/blob/main/LICENSE) 


## Overview

llm-d is a Kubernetes-native, high-performance distributed LLM inference framework designed to unlock production-scale AI inference. This open source project focuses on providing distributed inferencing for Generative AI runtimes on any Kubernetes cluster. Its architecture is built for high performance and scalability, aiming to reduce costs through a range of hardware and software efficiency improvements.

llm-d prioritizes ease of deployment and use, addressing the operational needs of running large GPU clusters, including SRE concerns and day 2 operations. It is designed to be an expandable inference platform, featuring a set of core proven functionalities and incubating features. llm-d can be deployed as a production solution or used as components for experimentation to evolve distributed inference capabilities.


Built Leveraging Kubernetes, llm-d integrates advanced inference functionalities into enterprise IT infrastructures, empowering IT teams to meet diverse serving demands while maximizing efficiency and minimizing total cost of ownership (TCO).

Focused on distributed inferencing for Generative AI runtimes on any Kubernetes cluster, llm-d's architecture is designed for high performance and scalability, aiming to reduce costs through hardware and software efficiency improvements. The project prioritizes ease of deployment and use, as well as operational needs for running large GPU clusters, including SRE concerns and day 2 operations.

## Architecture

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img alt="llm-d Logo" src="./docs/assets/images/llm-d-arch.png" width=85%>
  </picture>
</p>

llm-d includes the main components:
- Prefill/decode disaggregation
- AI-aware router and scheduler with plug points for customizable scorers
- KV cache distribution, offloading, and storage hierarchy
- Operational telemetry for production, including Prometheus/Grafana
- NIXL-based KV transfer

### Core features 

- **Dynamic and pluggable AI-aware inference scheduler**: Provides scheduler components for routing AI inference requests within the LLM-d framework, including an "Endpoint Picker (EPP)" for optimized routing via Envoy's ext-proc feature. Built on Gateway API and GIE projects, it extends support with custom plugins like custom scorers and P/D Disaggregation.

### Incubating 

- llm-sim
- llm-d-benchmarking

## Getting Started

### Deploying as as solution

### Experimenting and developin

### Releases

Visit our [GitHub Releases page](https://github.com/llm-d/llm-d/releases) and review the release notes to stay updated with the latest releases.

### License

llm-d is licensed under the [Apache License 2.0](https://github.com/neuralmagic/guidellm/blob/main/LICENSE).

## Community

### Contribute

We appreciate contributions to the code, examples, integrations, documentation, bug reports, and feature requests! Your feedback and involvement are crucial in helping llm-d grow and improve. Below are some ways you can get involved:

- [**DEVELOPING.md**](https://github.com/llm-d/llm-d/blob/main/DEVELOPING.md) - Development guide for setting up your environment and making contributions.
- [**CONTRIBUTING.md**](https://github.com/llm-d/llm-d/blob/main/CONTRIBUTING.md) - Guidelines for contributing to the project, including code standards, pull request processes, and more.
- [**CODE_OF_CONDUCT.md**](https://github.com/llm-d/llm-d/blob/main/CODE_OF_CONDUCT.md) - Our expectations for community behavior to ensure a welcoming and inclusive environment.

### Join

We invite you to join our growing community of developers, researchers, and enthusiasts passionate about scalinng and optimizing inference. Whether you're looking for help, want to share your own experiences, or stay up to date with the latest developments, there are plenty of ways to get involved:

- [**llm-d Community Slack**](https://...) - Join our Slack channel to connect with other GuideLLM users and developers. Ask questions, share your work, and get real-time support.
- [**GitHub Issues**](https://github.com/llm-d/llm0d/issues) - Report bugs, request features, or browse existing issues. Your feedback helps us improve GuideLLM.
- [**Subscribe to Updates**](https://...) - Sign up for the latest news, announcements, and updates about GuideLLM, webinars, events, and more.
- [**Contact Us**](http://...) - Use our contact form for general questions about Neural Magic or GuideLLM.