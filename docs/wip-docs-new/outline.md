### Getting Started

#### Introduction
* Introduce the project and key features, similar to https://gateway-api-inference-extension.sigs.k8s.io/ 
* Introduce the concept of well-lit paths
* Introduce concept of Gateway API vs Standalone Proxy

#### Quickstart - Hello, World
* Simplest possible deployment to get started

#### Feature Matrix
* Support matrix of “well-lit path" by model server / hardware

#### Artifacts

* List of every artifact we created for the release

### Architecture

* Layers — describe each part of the architecture and what it does
   * Introduction (Arch Diagram): Proxy → EPP → InferencePool → Model Server
   * Proxy (Standalone vs Gateway API)
   * EPP (Overall design, Concept of a scorer, Concept of flow control, Concept of latency predictor)
   * InferencePool API (API for sets of model servers (Point back to API docs))
   * Model Servers (vllm, sgl, deploy however you want)

* Component Design
   * EPP (Inference Scheduling, Flow Control)
   * Async Processor
   * Latency Predictor
   * Disaggregation (EPP, Sidecar, Protocols, DP-Aware)
   * KV-Indexer
   * KV Offloading (CPU, N/S Disk, E/W Disk)
   * Workload Autocaling

### Well-Lit Paths
* Do we want to just point back to the github? Each one should have:
- Arch diagram
- Configuration / knobs
- Workload
- Benchmarks
- Monitoring

### User Guides
* User Facing APIs (/v1/completions, tokens-in/tokens-out)
* Monitoring (how to setup, dashboards)
    * Metrics
    * Tracing
* Deploying multiple models
* RMDA

### API Reference
 
 * TBD - need to discuss with the teams