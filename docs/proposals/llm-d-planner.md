# An llm-d-planner for rapid llm-d configuration planning

**Authors**: Andre Fredette (_Red Hat_), Amit Oren (_Red Hat_), Jing Chen (_IBM_), Nick Masluk (_IBM_)

## Summary

[_Config Explorer_](https://github.com/llm-d/llm-d-benchmark/tree/main/config_explorer)
is a capacity planning tool within llm-d-benchmark that estimates GPU memory
requirements, evaluates parallelism strategies, and recommends cost-effective
hardware configurations using roofline analysis.
[_NeuralNav_](https://github.com/redhat-et/neuralnav) is a tool that guides
users from natural-language requirements through SLO target generation,
model-GPU recommendations based on real benchmarks, Kubernetes manifest
creation, and one-click deployment.

The following are presentations given during recent llm-d sig-benchmarking calls for additional info:

- [llm-d sig-benchmarking call, 3/10/2026](https://drive.google.com/file/d/1Ywlgjd1lz44OzLSJMH5UN8gxdsMHersv)
  - _NeuralNav_ demo: 2:39-8:28
  - Questions/Discussion: 8:28-15:45
  - Overview of integration w/_Config Explorer_: 15:45-21:38
- [llm-d sig-benchmarking call, 1/27/2026](https://www.youtube.com/watch?v=Y26i69zI6Ag)
  - _Config Explorer_ demo

Platform teams deploying LLMs on llm-d today must navigate a multitude of
interacting configuration knobs across llm-d components, with no single tool
that reasons across all of them. _Config Explorer_ handles the hardware side well
(memory estimation, roofline modeling, GPU ranking) but cannot capture business
requirements or generate deployments. _NeuralNav_ handles the user side well
(conversational intent gathering, manifest generation, one-click deployment) but
lacks the analytical depth to reason about configuration trade-offs.

This proposal unifies the two into **llm-d-planner**: _NeuralNav_ serves as the
user-facing orchestration layer while _Config Explorer_ serves as the
recommendation engine underneath. The combined system uses real benchmark data
when an exact match exists and falls back to performance estimates when it does
not, eliminating the costly trial-and-error that platform teams face today.

## Motivation

Deploying LLMs and LLM-serving stacks like llm-d remains a costly
trial-and-error process. Platform engineering teams today must choose across
models, hardware options, vLLM engine parameters, inference-scheduler and scorer
settings, prefill-decode disaggregation jobs, and autoscaling policies. Each
component has its own trade-off against latency, throughput, accuracy, and cost.
The plethora of configuration dimensions makes it difficult for teams to know
how to deploy an llm-d stack that meets their business requirements without
expensive experimentation.

Real benchmark data exists in _llm-d-benchmark_, but it is hard to search and
harder to map to a specific business scenario. Worse, there is no guarantee that
a benchmark matching a team's exact model, hardware, and workload combination
has ever been run, as it is costly to do so. Teams are left choosing between
incomplete data and blind experimentation.

Today, _llm-d-benchmark_'s _Config Explorer_ module addresses part of the
problem. Given a model and workload, it estimates GPU memory, evaluates
parallelism strategies, and recommends the most cost-effective hardware
configuration. It is grounded in empirically validated memory models, but it
stops at the infrastructure boundary. It does not capture business-level
requirements, generate deployment manifests, or orchestrate the serving stack.

_NeuralNav_ solves the deployment guidance problem. It walks users from a
natural-language description of their use case through SLO target generation,
model-GPU recommendation, Kubernetes manifest creation, and one-click
deployment. Its recommendations, however, rely on pre-existing benchmark data
and cannot reason about model-hardware combinations that have not been
benchmarked or explore the full llm-d configuration space.

This results in a gap in the end-to-end workflow:

1. **Configuration complexity**: platform teams face dozens of interacting knobs
   (vLLM, inference scheduler, P/D, autoscaler) with no tool that reasons across
   all of them jointly.
2. **Benchmark data that is hard to leverage**: real results exist but are
   difficult to discover and filter to a team's specific business requirement.
   Many scenarios also have no benchmark coverage at all.
3. **Prefill/decode disaggregation deployment difficulty**: as a core llm-d
   topology, P/D disaggregation requires careful configuration of each worker
   pod, parallelism strategies, KV cache transfer configuration, and more. No
   existing tool provides a unified recommendation for P/D splits for llm-d.
4. **Fragmented tooling**: users need to context-switch between separate tools
   for capacity planning and deployment and have to manually transfer parameters
   and assumptions.
5. **No closed feedback loop**: neither tool alone connects pre-deployment
   estimation to post-deployment benchmark results, so configuration choices are
   never validated against real serving performance.
6. **Duplicated efforts**: both projects independently maintain GPU databases,
   cost tables, and performance heuristics that drift out of sync.

Connecting the two projects into a unified planner would close the gap. Platform
teams get a single path from business need to recommended configuration, backed
by either a real benchmark when one exists, or by an accurate performance
estimation model when it does not exist. The result is a realistic expectation
of the best configurations for the team's requirements, without the
trial-and-error expense.

A key advantage of the integrated planner is that it enables several reinforcing
feedback loops, each closing a gap that exists when the tools operate in
isolation.

1. **Deployment validation loop**: after a recommended configuration is
   deployed, live serving metrics are compared against the pre-deployment
   estimates. When the serving stack meets or exceeds expectation, the result is
   recorded as a valid benchmark. When it falls short, the deviation is surfaced
   to the user with revised recommendations.
2. **Workload adaptation loop**: traffic patterns shift over time. Continuous
   monitoring of the live stack detects these shifts and triggers re-evaluation
   of the current configuration. We can leverage llm-d-observability and the
   workload-variant-autoscaler for this purpose.
3. **Estimation accuracy loop**: every pair of predicted performance and actual
   benchmark result becomes a training signal for the inference performance
   estimation engine. As real serving performance data flows back, the
   recommendations become more accurate next time.

### Goals

- Unify _Config Explorer_ and _NeuralNav_ into a single **llm-d-planner** tool that
  takes platform teams from business requirements to running llm-d deployments.
- Replace _NeuralNav_ s coarse recommendation engine with _Config Explorer_'s
  architecture-aware memory estimation, roofline analysis, and GPU ranking.
- Use real benchmark data when an exact match exists and fall back to
  performance estimates when it does not. Work with the Prism project to
  converge on a common benchmark database format.
- Support prefill/decode disaggregation configuration as a first-class
  deployment topology.
- Close the feedback loop between pre-deployment estimates and post-deployment
  benchmark results.
- Design a pluggable interface for inference performance estimation engines,
  leveraging existing open-source tools where possible.
- Allow pathways for integrations with benchmark visualization and analysis
  tools.

### Non-Goals

- Replacing either project's existing capabilities wholesale; the integration
  builds on each project's strengths.
- Building a new UI framework from scratch; the existing NeuralNav
  conversational interface is reused.
- Providing benchmark data analysis or comprehensive visualizations. While
  benchmark data will be used to assist with making configuration
  recommendations to a user, neither _NeuralNav_ nor _Config Explorer_ is meant to
  be a primary interface for exploring or analyzing benchmark results.

## Proposal

The unified project will live in a new `llm-d/llm-d-planner` repository. Because
Config Explorer already lives in llm-d-benchmark, a core llm-d repository, and
the planner directly serves the llm-d deployment workflow, the new repository
belongs in the `llm-d` organization rather than `llm-d-incubation`.

### Complementary Capabilities

The two projects cover mostly distinct and largely non-overlapping parts of the
configuration search and deployment lifecycle. The table below summarizes the
capabilities that each project owns.

| Capability | Config Explorer | NeuralNav |
|---|:---:|:---:|
| Architecture-aware memory estimation (attention, MoE vs. dense vs. multimodal, quantization sizing, parallelism strategy evaluation) | ✔ | |
| Roofline-based throughput/latency profiling | ✔ | |
| Performance- and cost-optimized GPU recommendation | ✔ | |
| Empirically validated against real vLLM profiling data | ✔ | |
| Conversational requirements gathering (natural language to SLOs) | | ✔ |
| Business use cases to traffic profile mapping | | ✔ |
| Model accuracy and quality scoring | | ✔ |
| Multi-criteria ranking (Accuracy, Cost, Performance, etc.) | | ✔ |
| Kubernetes manifest generation | | ✔ |
| One-click deployment to local or production clusters | | ✔ |
| Live inference testing and deployment monitoring | | ✔ |
| Benchmark data persistence | | ✔ |

Where each tool is strong and absent:

- _Config Explorer_ knows how a model maps to hardware but not why the user needs
  it or what to do once the configuration is chosen.
- _NeuralNav_ knows what the user wants and how to deploy it, but its performance
  estimates are coarse. It also requires real hardware for unexplored
  territories, which is costly if the user just wants to get a simple
  understanding of performance expectations.

An integrated system inherits both strengths: _NeuralNav_ s conversational
frontend and deployment automation serves as the user-friendly frontend layer,
while _Config Explorer_'s memory models and roofline analysis become the
recommendation engine underneath. Neither project should need to rewrite the
capabilities the other already provides.

### Integration Architecture

The integration connects _Config Explorer_'s estimation backend with NeuralNav's
user-facing orchestration layer. Data flows bidirectionally between systems
while preserving modular independence.

| Layer | Component | Source | Function |
|---|---|---|---|
| Presentation | Conversational UI | _NeuralNav_ | Requirements gathering + better user experience |
| Orchestration | Specification service | _NeuralNav_ | Intent to SLO and traffic profile mapping |
| Recommendation | Config Explorer API | _NeuralNav_ and _Config Explorer_ | _NeuralNav_ for existing benchmarks; _Config Explorer_ for un-benchmarked configurations |
| Knowledge | Benchmark Store | _NeuralNav_  llm-d-benchmark, llm-d Results Store & Analysis | Provide performance truth based on a community-driven database of llm-d benchmarks |
| Deployment | Kubernetes | _NeuralNav_ | Manifest generation, cluster orchestration |
| Monitoring | Kubernetes | _NeuralNav_  llm-d-observability | Live monitoring of llm-d stack health |

### User Stories

#### User Story 1: model deployment into development environment

A platform engineer needs to deploy a code-generation LLM for their development
team. They describe their use case in natural language, and _llm-d-planner_
extracts SLO targets, evaluates model-hardware combinations using _Config
Explorer_'s roofline analysis, and presents ranked recommendations labeled as
"Estimated" or "Benchmarked". They select a configuration and deploy it with one
click.

#### User Story 2: deployment re-configuration post re-evaluation

A team is running an llm-d deployment and traffic patterns have shifted. The
monitoring layer detects the shift and triggers re-evaluation. _llm-d-planner_
suggests an updated configuration with a different P/D split ratio, and the team
can review and apply the change.

## Design Details

### Short-term: unified recommendation engine

The integration is not a simple swap. _NeuralNav_ already has a working
recommendation path. The goal is to have _Config Explorer_'s backend power the
pieces _NeuralNav_ currently lacks: architecture-aware memory estimation,
quantization-aware sizing, parallelism strategy evaluation and roofline-based
throughput/latency modeling.

| Milestone | Description | Deliverable |
|---|---|---|
| Extract _Config Explorer_ into standalone repo | Separate from llm-d-benchmark monorepo into standalone package with versioned releases | llm-d/llm-d-planner repo with _Config Explorer_ as standalone package |
| Move _NeuralNav_ into _llm-d-planner_ | Integrate _NeuralNav_ alongside _Config Explorer_ as a separate component | `llm-d/llm-d-planner` repo with _NeuralNav_ integrated |
| UI and API integration | Bridge a single interface from business intent extraction to llm-d deployment | Unified frontend and API server backend |
| Converge on common benchmark data format | Adopt llm-d-benchmark v2 benchmark report schema in _NeuralNav_ | Agreement on API interfaces |
| Hybrid recommendation | Augment _NeuralNav_'s benchmark-based recommendations with _Config Explorer_'s roofline + memory estimation for un-benchmarked configurations | Recommendation view shows "Benchmarked" or "Estimated" labels per config |
| Inference estimation engine integration (phase 1) | Integrate an inference performance estimation engine to enable configuration sweeps without running real benchmarks. Open-source tools like [BLIS](https://github.com/inference-sim/inference-sim) already exist for this purpose. | Pluggable interface for inference estimation engines |
| P/D disaggregation knobs search | End-to-end configuration for P/D deployments: TP, DP arguments, P and D replicas, and KV-cache transfer strategy | Data-backed P/D split configurations for llm-d |
| Kubernetes deployment generator | Generate deployable Kubernetes artifacts from a recommended configuration. Output format (e.g., Kustomize overlays or plain YAML) is a design decision to be determined in a future iteration. | Manifest generation engine for recommended configurations |
| Blog post on llm-d-planner | Document the llm-d-planner journey and capabilities | Public validation of approach and community feedback |

### Mid-term: expand knob-space search and real benchmarking

<u>Objective</u>: expand the recommendation surface from hardware selection to full
serving-stack tuning including vLLM knobs, inference-scheduler knobs, and P/D
disaggregation, backed by real vLLM or llm-d benchmark runs.

| Milestone | Description | Deliverable |
|---|---|---|
| Inference scheduler and scoring search (estimation engine phase 2) | Extend configuration search to inference scheduler and scoring weights | Present performance data (real or estimated) for inference scheduling-driven configuration comparison |
| Benchmark-backed validation | Run llm-d benchmark sweeps for each recommendation configuration. Stores results (local or publicly managed DB by llm-d) | Closes feedback loop. Estimations are compared to real throughput/latency |
| Multi-model and agentic workflow support | Support more complex use cases such as multi-model and agentic workflows | Recommendation engine handles multi-model topologies with per-model configuration and cross-model resource optimization |
| Blog posts | Planning and search across vLLM + inference scheduler knobs with real results. | Continued public validation of approach, community feedback loop, and impact |

### Long-term: simulation-driven dynamic tuning

| Milestone | Deliverable | Impact |
|---|---|---|
| Improve accuracy and quality scoring into recommendation engine | Incorporate _NeuralNav_'s scoring algorithm and enable data-driven discovery of optimal scoring strategies | Consumable scoring for llm-d-planner users |
| Dynamic tuning for workload adaptation | Estimation-engine-trained tuning algorithm adapts scheduler parameters to shifting workload patterns in real time | Deployments self-optimize as traffic changes |
| Dynamic tuning for PD adaptation | Extend dynamic tuning to PD, adapting on request shape | Handles mixed short/long context traffic without manual retuning |
| LoRA load balancing | LoRA adapter routing and balancing | Supports multi-tenant LoRA serving at scale |

### Expected Impact

**For llm-d ecosystem:**

- **_Config Explorer_ as a shared service**: extracting it to a standalone repo
  with a stable API makes capacity planning reusable across llm-d tooling, not
  just _NeuralNav_ but any component that needs to reason about model-hardware
  fit.
- **Pluggable estimation backends**: The provider interface for inference
  estimation engines invites external contributors to add new modeling
  approaches without forking the stack.
- **Closed feedback loop**: benchmark results flow back to the recommendation
  engine so estimate accuracy improves with every deployment rather than staying
  the same.

**For platform teams deploying LLMs and llm-d serving stacks:**

- **Fewer failed deployments** given architecture-aware memory estimation that
  catches OOM conditions and under-provisioned configs before anything is
  scheduled.
- **Lower GPU cost**: joint optimization across hardware selection, parallelism
  strategies, and serving-stack knobs surfaces configurations that meet SLOs at
  minimum cost rather than defaulting to the largest GPU.
- **Faster time-to-production**: a single workflow from natural language
  requirements to running deployment eliminates the manual handoff between
  business requirement mapping, capacity planning, and infrastructure
  provisioning.

### Risks and Mitigation Strategies

- **vLLM / serving-stack drift**: vLLM's configuration surface changes across
  releases; knob-space search results can go stale. Mitigation: Pin
  recommendations to tested vLLM versions. Add a version compatibility field to
  every stored benchmark result so stale data is never silently applied.
- **Community adoption friction**: Two projects with different installation
  paths and UIs may deter contributors. Mitigation: Ship a single API and
  developer environment that stand up both projects together. Maintain a unified
  getting-started guide.

## Alternatives

### Status quo: manual trial and error

Platform teams continue to select models, GPU types, parallelism strategies, and
serving-stack parameters through experimentation. Each iteration requires
provisioning real hardware, running benchmarks, and interpreting results before
trying the next combination. This approach works eventually but is expensive in
both GPU-hours and engineer time especially when the configuration space
includes vLLM knobs, inference-scheduler settings, and P/D disaggregation
options. It also means teams without large GPU budgets cannot explore the space
at all and default to over-provisioned, costly configurations.

This was ruled out because the whole point of the planner is to eliminate this
cost. Trial and error does not scale as the number of configuration dimensions
grows with each llm-d release.

### Keep Config Explorer and NeuralNav as separate tools

Teams could use _Config Explorer_ for hardware sizing and then manually transfer
its outputs (GPU type, count, parallelism strategy) into _NeuralNav_ for
deployment manifest generation. This preserves each project's independence and
avoids integration work.

This was ruled out because the manual handoff between tools is error-prone and
defeats the goal of a single workflow. Users must context-switch between
different interfaces, re-enter parameters, and reconcile assumptions that may
differ between the two tools (e.g. different GPU cost tables or model naming
conventions). The feedback loop also remains broken since neither tool sees the
other's results.

### Build estimation capabilities directly into NeuralNav

Instead of integrating _Config Explorer_, _NeuralNav_ could develop its own memory
estimation, roofline modeling, and parallelism evaluation from scratch. This
would keep the project self-contained with no external dependency.

This was ruled out because it duplicates work that _Config Explorer_ has already
done and validated against real vLLM profiling data. Building and maintaining
accurate memory models for diverse architectures (MoE, dense, multimodal) and
quantization schemes is a substantial ongoing effort. Leveraging Config
Explorer's existing, empirically validated models avoids this duplication and
lets both teams focus on their respective strengths.
