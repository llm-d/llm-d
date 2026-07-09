# Official llm-d Simulator

**Authors**: Jing Chen, Carlos Costa, Dipanwita Guhathakurta, Michael Kalantar, Nick Masluk, Vishakha Ramani, Asser Tantawi, Mert Toslali, Fabio Oliveria, and Srinivasan Parthasarathy

## Summary

As llm-d grows into a platform for intelligent distributed inference, simulation should become a core capability rather than a standalone research tool. This proposal promotes [BLIS](https://github.com/inference-sim/inference-sim), a discrete-event simulator for distributed LLM inference already proven across capacity planning, algorithm development, and AI-driven policy discovery, into the official **llm-d Simulator**: a supported simulation framework that enables rapid, reproducible experimentation across the llm-d ecosystem.

The migration transfers the `inference-sim/inference-sim` repository to `llm-d/llm-d-simulator` with no architectural changes or feature additions beyond what the migration itself requires, and establishes pinned releases aligned with the llm-d release cycle.

## Motivation

BLIS was originally developed as a research platform for studying distributed LLM inference systems. Over time it has evolved beyond a prototype and demonstrated sustained value across three primary user communities.

**Platform engineers** face a large configuration space when deploying an LLM serving system, including GPU types, replica counts, routing policies, batching parameters, and workload characteristics. Evaluating these choices directly on hardware is expensive and time-consuming. BLIS has demonstrated that these deployment decisions can instead be explored through calibrated discrete-event simulation. Platform engineers can evaluate hundreds of candidate deployments in minutes, compare latency–throughput tradeoffs, and identify Pareto-optimal configurations before provisioning a single GPU, shifting capacity planning from manual trial-and-error to data-driven decision making.

**llm-d developers** traditionally need repeated deployments to GPU clusters to experiment with new algorithms, making iteration slow, expensive, and difficult to reproduce. BLIS provides a dramatically faster development loop by accurately modeling admission control, routing, scheduling, batching, KV-cache behavior, and prefill/decode placement without executing real inference on GPUs. Running approximately 200× faster than equivalent cluster experiments while maintaining low latency prediction error, developers can prototype, evaluate, and compare new policies locally before validating only the most promising candidates on real hardware.

This workflow has already produced tangible results. Using BLIS as the experimentation platform, an AI-native optimization pipeline automatically discovered a probabilistic admission control policy that significantly reduced tail latency. The policy was subsequently validated on a real llm-d deployment and contributed to [llm-d-router](https://github.com/llm-d/llm-d-router/tree/main/pkg/epp/framework/plugins/requestcontrol/admitter/probabilisticadmitter). A soft-reflective flow control algorithm is also in progress for contribution to llm-d-router.

**Researchers and learners** benefit from a fully simulated environment where distributed inference ideas can be tested, compared, and iterated on in a controlled, reproducible environment, requiring only CPUs. This lowers the barrier to entry for users without access to GPU infrastructure, enabling both research into distributed inference systems and onboarding of new contributors who can build intuition about system behavior through direct experimentation.

### Goals

- Transfer the `inference-sim/inference-sim` repository to `llm-d/llm-d-simulator`.
- Establish pinned releases of `llm-d-simulator` aligned with the llm-d release cycle.
- Establish a regular community meeting for `llm-d-simulator` (TBD).

### Non-Goals

- Architectural changes or new features as part of the migration.
- Rewriting existing code beyond what the migration itself requires.

## Proposal

BLIS has active contributors and has already served as the experimental foundation for several research blogs and publications. These experiences provide strong evidence that simulation is not simply a research convenience but a foundational capability for the llm-d ecosystem. Elevating BLIS into an official llm-d project establishes simulation as the standard development, benchmarking, and research platform, lowering the cost of experimentation while accelerating innovation across production deployments, open-source development, and distributed inference research.

### User Stories

#### Story 1: capacity planning without hardware

A platform engineer needs to right-size a new llm-d deployment before any GPU budget is committed. They describe their workload (model, traffic profile, latency SLOs) and use `llm-d-simulator` to sweep hundreds of candidate configurations (replica counts, routing policies, batching parameters) in minutes on a laptop. The simulator returns a Pareto-optimal frontier of cost-vs-latency tradeoffs, and the engineer provisions only the configuration that best meets their SLOs. This is the use case that drives the integration between `llm-d-simulator` and [llm-d-planner](llm-d-planner.md).

#### Story 2: rapid algorithm development

An llm-d developer proposes a new admission control heuristic. Rather than deploying it to a GPU cluster, they plug the policy into `llm-d-simulator` using the same interface as the real control plane, run it against a recorded production trace, and receive tail-latency percentiles within minutes. After a few local iterations the developer identifies the best candidate and submits only that version for hardware validation.

#### Story 3: research and education without GPU access

A graduate student researching distributed inference wants to understand how prefill/decode disaggregation affects request latency under different traffic shapes. Using `llm-d-simulator`, they run controlled experiments, vary the P/D split ratio, and produce reproducible results, all on a standard laptop with no GPU access required.

## Design Details

The `llm-d-simulator` architecture is described in detail in [BLIS: Evolving llm-d at Simulation Speed](https://llm-d.ai/blog/blis-evolving-llm-d-at-simulation-speed), published on the llm-d website.

### Milestones Achieved

- BLIS was used to discover a new probabilistic admission control algorithm that reduced TTFT p99 by up to 97%. This algorithm was contributed to llm-d-router.
- Simulated benchmark data from BLIS is used as part of `llm-d-planner` to augment cluster experiments for discovering Pareto-optimal configurations for platform engineers.
- Multiple blog articles published:
  - [Why Simulate Before You Scale](https://medium.com/modeling-distributed-inference/why-simulate-before-you-scale-e59e0f0b1732)
  - [The Physics of High-Fidelity Distributed Inference Platform Simulation](https://medium.com/modeling-distributed-inference/the-physics-of-high-fidelity-distributed-inference-platform-simulation-28fe27b59da2)
  - [From Simulation to Production: How an AI-Native Pipeline Discovered a Better Admission Controller for llm-d](https://ai-native-systems-research.github.io/ai-native-systems-research/blog/2026/05/13/from-simulation-to-production-how-an-ai-native-pipeline-discovered-a-better-admission-controller-for-llm-d/)
- Research paper submitted to a top-tier systems conference covering the design, accuracy, and use cases of BLIS.

### Road Map

| Milestone | Description |
|---|---|
| Arxiv release of BLIS research paper | Public preprint of the systems paper covering design, accuracy, and use cases |
| Repository migration | Transfer `inference-sim/inference-sim` to `llm-d/llm-d-simulator` |
| Release alignment | Establish pinned `llm-d-simulator` releases alongside the llm-d release cycle |
| Enhanced P/D accuracy | Improved modeling of prefill/decode disaggregation behavior |
| Network and communications modeling | Higher-fidelity modeling of inter-node communication |

## Alternatives

One alternative is to keep BLIS as an independent research project and allow llm-d users to adopt it opportunistically for simulation and experimentation. While this preserves flexibility and avoids immediate integration work, it would leave simulation outside the core llm-d development workflow. Over time, this risks fragmentation: different teams may build ad-hoc simulation or testing setups, and there would be no shared, canonical environment for capacity planning, algorithm development, or benchmarking. This would also limit BLIS’s impact, as it would remain a research tool rather than a foundational part of the llm-d engineering environment.
Another alternative is to rely exclusively on real cluster-based evaluation for llm-d development and capacity planning, or to build a new simulator from scratch tightly coupled to current system needs. The former approach is prohibitively slow, expensive, and non-deterministic for large-scale experimentation, while the latter would duplicate several years of work already validated in BLIS, delaying availability of a usable system and discarding proven simulation workflows. Given that BLIS has already demonstrated success across capacity planning, algorithm development, and AI-driven discovery of improved inference policies, evolving it into the official llm-d Simulator provides the most direct path to a unified, production-relevant simulation platform.
