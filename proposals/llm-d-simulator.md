# Cluster-Level Inference Performance Estimator for llm-d

**Authors**: Jing Chen, Carlos Costa, Dipanwita Guhathakurta, Michael Kalantar, Nick Masluk, Vishakha Ramani, Asser Tantawi, Mert Toslali, Fabio Oliveria, and Srinivasan Parthasarathy

## Summary

llm-d is intentionally engine- and hardware-agnostic, yet no tool exists to evaluate its distributed-systems design space: how routing, flow control, autoscaling, placement, and disaggregation policies interact across multiple inference engines and hardware platforms, at a speed that makes configuration search, algorithm development, and research tractable. This document proposes a core llm-d Simulator: a simulation framework that enables rapid, reproducible performance estimation across the llm-d ecosystem. The goal of the llm-d Simulator is to predict latency and throughput by advancing a simulation clock event-to-event without running real workloads on hardware, achieving approximately 200× speedup over real-time execution.

## Motivation

The llm-d Simulator serves three primary user communities. Platform engineers can use it to perform capacity planning, evaluate deployment configurations, and understand latency and throughput tradeoffs before provisioning expensive hardware. llm-d developers can rapidly prototype, test, and validate new scheduling, routing, admission control, and serving algorithms in a deterministic environment before deploying to real clusters. Researchers gain a common, reproducible platform for studying distributed inference systems, benchmarking new ideas, and sharing results that can be independently validated by the community. All three use cases depend on evaluating large numbers of candidates quickly. This is only feasible through discrete-event simulation, which decouples simulated time from wall-clock time.

This proposal builds on the success of BLIS, which has already demonstrated the value of simulation by enabling the design and evolution of new algorithms for llm-d significantly faster than hardware-based experimentation. By elevating BLIS into an official llm-d project, we establish simulation as the standard development, benchmarking, and research platform for the ecosystem, lowering the cost of experimentation while accelerating innovation for distributed inference research.

### Goals

- Contribute the `inference-sim/inference-sim` repository to `llm-d/llm-d-simulator`.
- Establish regular pinned releases of `llm-d-simulator` along with the llm-d release cycle.
- TBD: establish a regular community meeting for `llm-d-simulator`.

### Non-Goals

- Architectural changes or new features as part of the migration.
- Rewriting existing code beyond what the migration itself requires.

## Proposal

BLIS was originally developed with the goal of modeling llm-d from Day 0. BLIS has demonstrated success by initial integration with llm-d-planner to support fast configuration search for finding Pareto-optimal setups for capacity planning, and was also used to enable a new admission control algorithm discovery that was contributed to llm-d-router. These experiences provide strong evidence that adopting a performance estimator like BLIS advances the llm-d ecosystem.

### 1. Capacity Planning for Platform Engineers

Deploying an LLM serving system requires navigating a large configuration space across GPU types, replica counts, routing policies, batching parameters, and workload characteristics. Evaluating these choices directly on hardware is expensive and slow. BLIS demonstrated that platform engineers can instead sweep hundreds of candidate deployments in minutes, compare latency-throughput tradeoffs, and identify Pareto-optimal configurations before provisioning hardware. This is effectively the goal of [llm-d-planner](llm-d-planner.md), and it shifts capacity planning from manual trial-and-error to data-driven decision making while substantially reducing infrastructure cost.

### 2. Rapid Development of llm-d Algorithms

Developing new algorithms for distributed inference traditionally requires repeated GPU cluster deployments, making iteration slow and expensive. BLIS demonstrated a dramatically faster loop by accurately modeling admission control, routing, scheduling, batching, KV-cache behavior, and prefill/decode placement without executing real inference. BLIS exposes the same policy interfaces as llm-d's control and data planes, so new algorithms can be plugged in directly. Running approximately 200× faster than real cluster experiments, developers can rapidly prototype and compare policies locally before validating only the most promising candidates on hardware. This workflow has already produced results: BLIS enabled the discovery of a probabilistic admission control policy that reduced TTFT p99 by up to 97% and was subsequently contributed to [llm-d-router](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/requestcontrol/admitter/probabilisticadmitter). A soft-reflective flow control algorithm is en route for contribution as well.

### 3. Advancing Research, Education, and Adoption of llm-d Without Hardware Access

llm-d's complexity (tightly coupled routing, scheduling, KV-cache management, batching, and hardware-aware execution) creates a high barrier to entry for users without hardware access. BLIS lowers that barrier by providing a fully simulated environment that runs on CPUs alone, enabling both research (ideas can be tested and iterated in a controlled, reproducible environment before any hardware validation) and education (users can build intuition about how policies affect latency, throughput, and resource utilization through direct experimentation). This makes the simulator both a research substrate and an onboarding layer, broadening participation beyond accelerator-equipped environments.

## Design Details & Scope

The `llm-d-simulator` architecture is described in [BLIS: Evolving llm-d at Simulation Speed](https://llm-d.ai/blog/blis-evolving-llm-d-at-simulation-speed#what-is-blis), published on the llm-d website. This blog highlights the speed at which BLIS enables configuration search and policy development. Compared to traditional development which requires provisioning llm-d on a real cluster, BLIS gives performance numbers in a matter of seconds, and costs just CPUs.

Different simulation tools answer different questions and are complementary rather than competing. BLIS occupies the cluster layer, above any single inference engine.

| Layer | Question it answers | Best suited for |
|---|---|---|
| Execution / replay | Did this exact run reproduce, including frontend behavior and bugs? | Debugging, regression testing, trace preservation |
| Engine-native simulation | Did this engine's implementation behave correctly and efficiently? | Scheduler, KV-cache, kernel, and engine-internal evolution |
| Cluster-level / BLIS | Which cluster-level design wins across engines, hardware, and workloads? | Routing, admission, flow control, autoscaling, placement, policy search |

Engine-level simulation is necessary but not sufficient for llm-d. The behaviors that define llm-d's value (routing, admission control, autoscaling, placement across replicas, and P/D disaggregation) emerge at the cluster layer above what any single engine simulator captures, and a simulator embedded inside one engine inherits that engine's assumptions rather than modeling llm-d's policies across engines and hardware.

The table below shows what BLIS models, what it deliberately does not, and why each choice serves llm-d's use cases.

| Concern | Owned by | Modeled by BLIS | Why it matters |
|---|---|---|---|
| Request parsing, frontend / API quirks, CUDA kernels | Engine | No | These change frequently inside engines; not tracking them keeps the parity surface small and BLIS stable across engine updates |
| Attention variants, KV-cache eviction, quantization | Engine | Timing and capacity effects via calibrated terms | Performance consequences enter BLIS without reimplementing engine internals; recalibration against benchmark data is sufficient |
| Scheduler and batching semantics | Shared | Yes, via pluggable `InstanceScheduler` and `BatchFormation` interfaces | Shared contract enables direct plug-in testing of new scheduling algorithms without cluster deployments |
| Routing and scoring | llm-d | Yes, via router and scorer policy interfaces | Routing decisions directly affect tail latency and KV-cache hit rates across replicas; no engine simulator captures this |
| Admission and flow control | llm-d | Yes, via admission policy and gateway flow control interfaces | Queue depth and burst behavior are cluster-level phenomena that only emerge when router and engines are modeled together |
| Autoscaling and placement | llm-d | Yes, via collector/analyzer/actuator interfaces | Autoscaling correctness depends on cross-replica state invisible to any single-engine model |
| Distributed inference (prefill/decode disaggregation, LoRA scaling, multi-engine) | llm-d | Yes, full cluster discrete-event simulation | Engine-agnostic design lets vLLM, SGLang, and future engines be compared side by side under the same llm-d policies |

### A Simple Example

The following command demonstrates BLIS predicting cluster performance without running real inference on hardware:

```bash
./blis run --model qwen/qwen3-14b \
  --num-instances 4 --routing-policy weighted \
  --routing-scorers "precise-prefix-cache:2,queue-depth:1,kv-utilization:1" \
  --rate 100 --num-requests 500
```

> **Note:** `--num-instances` maps to the number of server engine replicas in the llm-d stack; `--routing-scorers` maps to llm-d-router native scorers used for request routing; `--rate` and `--num-requests` define the workload.

The output includes key inference performance metrics (E2E, TTFT, ITL, and throughput) and completes in **0.1 seconds**:

```json
{
  "instance_id": "cluster",
  "completed_requests": 500,
  "still_queued": 0,
  "still_running": 0,
  "injected_requests": 500,
  "total_input_tokens": 268297,
  "total_output_tokens": 257458,
  "vllm_estimated_duration_s": 24.190198,
  "responses_per_sec": 20.669529038166615,
  "tokens_per_sec": 10643.071214216601,
  "e2e_mean_ms": 9646.602298,
  "e2e_p90_ms": 15503.474400000001,
  "e2e_p95_ms": 17079.55565,
  "e2e_p99_ms": 19434.475519999996,
  "ttft_mean_ms": 44.700094,
  "ttft_p90_ms": 54.3286,
  "ttft_p95_ms": 56.7397,
  "ttft_p99_ms": 62.18690999999999,
  "itl_mean_ms": 18.682285050475173,
  "itl_p90_ms": 20.851,
  "itl_p95_ms": 20.959,
  "itl_p99_ms": 23.599,
  "scheduling_delay_p99_ms": 38.28315,
  "preemption_count": 0,
  "dropped_unservable": 0,
  "length_capped_requests": 0,
  "timed_out_requests": 0
}
```

BLIS runs 200× faster than real cluster experiments, estimating the performance of the end-to-end llm-d stack in seconds. The speed BLIS provides is a requirement, not a convenience, because the use cases in this proposal all depend on it:

- **Capacity planning** sweeps hundreds of candidate configurations.
- **Algorithm development** iterates over many policy variants against recorded traces.
- **AI-driven policy discovery** evolves candidates across large search spaces.

None of these are tractable at the wall-clock speed functional emulators run at. Discrete-event simulation is the only approach that makes them tractable, because it decouples simulated time from wall-clock time: it advances the simulation clock event-to-event instead of executing work in real time. Approaches that execute or replay the real engine (such as trace capture/replay or CPU forward passes) are inherently bounded by real time, making them well suited to fidelity but not to the config-search and policy-evolution use cases BLIS is built for.

### Maintaining Parity with llm-d Updates

Not every llm-d change requires a change to the simulator. Because BLIS models performance, not function, only three kinds of upstream change are parity-relevant: (1) a significant change to an architecture or interface, (2) a new algorithm that materially alters latency, or (3) a change to the request journey through the stack. The large class of changes that do not affect timing (request parsing, API-surface additions, response formatting, metrics bookkeeping, and other frontend behavior) require no simulator update at all. This keeps the parity surface far smaller than a functional emulator, which must track all of it.

Two architectural properties keep the cost of parity changes low:

- **Pluggable policy interfaces**: BLIS exposes the same control- and data-plane interfaces as llm-d (scoring, admission, flow control, routing, scheduling), so a new algorithm is added as a policy template behind an existing interface rather than as a rewrite.
- **A decomposable latency model**: BLIS's latency model is a sum of independent terms, so a change affecting one latency contributor can be recalibrated in isolation.

Today, parity is maintained through a workflow built around code-proofs, implemented as a suite of agent code skills that the simulator ships. Because AI agents can otherwise paraphrase or hallucinate behavior from memory, every parity change must be grounded in the actual reference source: a dedicated cross-repo feature issue template requires GitHub permalinks to the real llm-d-router/vLLM code, the behaviors to preserve, intentional deviations, and the target commit, and the agent must verify each permalink resolves before proceeding. An issue-review skill then checks evidence quality, scope, and coverage, and an implement-issue skill turns validated issues into PRs under test-driven development and the simulator's invariants, converging through automated self-review. Every parity claim is thus grounded in verifiable reference code pinned to a commit, not in an agent's recollection of how llm-d behaves.

We plan to augment this in the future with an automated, AI-driven parity-discovery protocol that watches llm-d (and other tracked engines) for changes, classifies each by whether it touches an interface, the request journey, or latency-relevant behavior, discards the performance-irrelevant majority, and files actionable, code-proof-backed issues into the existing issue-review and implement-issue pipeline. This turns parity from a manual chase after every upstream commit into a mechanized, auditable process focused on purely timing parity.

### Road Map

- Arxiv release of BLIS research paper
- Migrate `inference-sim/inference-sim` repository to `llm-d/llm-d-simulator`
- Enhanced P/D accuracy
- Improved network and communications modeling
- Investigating a CI workflow to periodically validate the accuracy of the simulator (this will require cluster resources like llm-d-benchmark nightly runs)
- Investigating an AI-driven workflow to identify llm-d updates that require changes to performance modeling in the simulator, to maintain parity with llm-d

## Alternatives

### Rely on real cluster-based evaluation or build a new simulator

One alternative is to rely exclusively on real cluster-based evaluation for llm-d development and capacity planning, or to build a new simulator from scratch tightly coupled to current system needs. The former approach is prohibitively slow, expensive, and non-deterministic for large-scale experimentation, while the latter would duplicate a year of work validated in BLIS, delaying availability of a usable system and discarding proven simulation workflows. Given that BLIS has already demonstrated success across capacity planning, algorithm development, and AI-driven discovery of improved inference policies, evolving it into a core llm-d simulator provides the most direct path to a unified, production-relevant simulation platform.

### Adopt an external simulator from another ecosystem

Other serving ecosystems are already investing in simulation as a first-class capability. DynoSim, for example, is a discrete-event simulator for the Dynamo serving stack that models the full inference pipeline and runs approximately 1,500× faster than real time. Its existence signals that simulation is becoming a standard feature of how production inference platforms are built and evaluated. The llm-d community could choose to rely on tools like it, but they are built around different serving stacks and do not model llm-d-router scoring, llm-d's admission and flow control semantics, or the specific interactions between llm-d components. Adopting them would mean accepting a model of a different system. The right response to other ecosystems moving ahead with simulation is for llm-d to do the same, on its own terms.

### llm-d-inference-sim as a substitute

A closer alternative within the llm-d ecosystem itself is [llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim), a functional, lightweight Go server that impersonates a vLLM inference endpoint over HTTP and gRPC, returning synthetic responses with realistic per-request latency. It is a valuable and complementary tool, well-suited for integration testing and CI environments where the goal is to decouple infrastructure development from the cost of real GPU inference. However, it runs at wall-clock time, models a single engine instance, and does not predict cluster-level components such as llm-d-router, admission control, or autoscaling — the layer where capacity planning and policy discovery actually happen. The two projects are best understood as serving fundamentally different use cases rather than as alternatives to each other.

## Further Reading

- [Why Simulate Before You Scale](https://inference-sim.github.io/inference-sim/latest/blog/2026/03/05/why-simulate-before-you-scale/)
- [The Physics of High-Fidelity Distributed Inference Platform Simulation](https://inference-sim.github.io/inference-sim/latest/blog/2026/04/09/the-physics-of-high-fidelity-distributed-inference-platform-simulation/)
- [From Simulation to Production: How an AI-Native Pipeline Discovered a Better Admission Controller for llm-d](https://ai-native-systems-research.github.io/ai-native-systems-research/blog/2026/05/13/from-simulation-to-production-how-an-ai-native-pipeline-discovered-a-better-admission-controller-for-llm-d/)
- Proposal for a new soft-reflective flow control algorithm discovered through BLIS (link TBD)
- [BLIS: Evolving llm-d at Simulation Speed](https://llm-d.ai/blog/blis-evolving-llm-d-at-simulation-speed#what-is-blis)
