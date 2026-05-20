# Platform-Native Time-Slicing

In reinforcement learning, there is a fundamental tension between algorithmic stability and hardware utilization. To ensure mathematical convergence, engineering teams heavily rely on synchronous or asynchronous on-policy loops. While these architectures safeguard the integrity of the experiment, they incur a severe capacity tax.

Because the RL loop experiences natural blocking phases—such as waiting for CPU-bound reward evaluations, generation stragglers, or synchronization steps—highly expensive accelerator hardware sits completely idle for 45% to 66% of its lifecycle. Across large-scale fleet deployments, this pattern consistently yields a low median hardware efficiency. While asynchronous on-policy setups mitigate some of this idle time compared to strictly synchronous loops, the efficiency gains leave significant room for improvement before reaching optimal hardware saturation.

Historically, the only remedy for this stranded compute was to force researchers to rewrite their math into highly complex, off-policy architectures. This trades infrastructure inefficiency for massive mathematical convergence risk and vastly increased integration time.

## Decoupling Architecture from Application Logic

A systems-layer infrastructure bottleneck should not require an application-layer mathematical rewrite. We can deliver the high utilization profile of a highly tuned asynchronous system directly from the platform layer, without requiring you to alter your core math.

Instead of demanding complex structural changes to the RL loop, we shift the optimization burden entirely to the infrastructure via **Job Interleaving**. By dynamically time-slicing accelerator hardware at the orchestration level, the platform can hot-swap resources to serve multiple concurrent jobs. The logical workflow remains mathematically safe for any single job, while the underlying physical hardware operates asynchronously, jumping between independent workloads to mask idle gaps.

## How It Works: Collaborative Time-Slicing

![Architecture](https://github.com/aishukamal/rl-time-slicing/blob/main/time-slicing-proposal/image5.png?raw=true)

To execute this dynamically, we provide a lightweight infrastructure integration that time-slices hardware by swapping workloads during their natural blocking phases. Standard driver-level checkpointing mechanisms (like vanilla cuda-checkpoint or CRIU) are notoriously slow, taking anywhere from 20 to 90 seconds to move tens of gigabytes of VRAM. To achieve the sub-second latencies required for RL, we utilize application-aware, collaborative swapping.

You instrument the RL flow using simple decorators (e.g., `@slicer.run_on_gpu`) provided by our client library. Under the hood, these decorators communicate with a local daemon orchestrator. The orchestrator manages the GPU queue and coordinates the context switches.

Because the system works collaboratively with the application, we use distinct, highly-optimized swapping mechanisms for Trainers and Samplers:

*   **The Sampler Pool:** When a sampler finishes its rollout phase, the orchestrator triggers a native inference API. Rather than moving the state back and forth, the active context is discarded entirely which is safe, since the trainer re-pushes fresh weights at the start of the next step regardless. This allows an idle sampler to yield a massive GPU footprint in under 250 milliseconds.
*   **The Trainer Pool:** When the job yields, the platform rapidly moves this critical state into host memory. This cooperative approach enables a ~50GB training state to swap out and in reliably in less than a second.

## Structural Efficiency at Scale

By enabling multiple discrete RL jobs to safely time-share the same physical hardware footprint, we convert "ghost capacity" into active throughput. Early-stage production proofs have demonstrated dramatic efficiency gains.

| Metric | Value |
| :--- | :--- |
| **Relative Duty Cycle Increase** | +30% |
| **Warm Context Swap Time** | < 1.0s |
| **Impact to Step Throughput** | None |

In benchmark testing, interleaving two independent sampler workloads on a single node elevated the actual hardware duty cycle from a baseline of 41% to 71%, with a theoretical peak of ~95% under idealized phase alignments. Critically, because the active job has exclusive access to the GPU during its compute window, there is zero degradation to token generation or training step throughput.

**Value Delivered**

*   **Preserve Developer Velocity:** Researchers retain their predictable, on-policy pipelines, ensuring high experiment success rates and straightforward debugging.
*   **Reclaim Quota and Capital:** Reclaim the stranded capacity of traditional RL loops. A static cluster footprint can support drastically more concurrent jobs.
*   **Frictionless Adoption:** By providing simple integration hooks and leveraging standard open-source tools, bespoke framework integration drops from weeks to hours.

## Proof of Concept

Setup:

| Parameter | Specification |
| :--- | :--- |
| **Framework & Algo** | veRL / GRPO |
| **Trainer / Sampler** | PyTorch FSDP / vLLM |
| **Model & Dataset** | Qwen2.5-0.5B-Instruct / GSM8K |
| **Accelerators** | 2x NVIDIA H100 80GB (a3-highgpu-2g) |
| **PoC source code** | [github.com link](https://github.com/aishukamal/rl-time-slicing/tree/main/verl) |

Results summary:

*   43% (~49% to ~92%) increase in peak duty cycles
*   33% decrease in total execution time for 2 independent RL jobs
*   3-6% increase in phase duration (inline with noise observed in phase duration within the baseline runs)

|     Baseline          |  Timeslice   |
:-------------------------:|:-------------------------:
|![Duty cycles baseline](https://github.com/aishukamal/rl-time-slicing/blob/main/time-slicing-proposal/image3.png?raw=true) | ![Duty cycles timeslice](https://github.com/aishukamal/rl-time-slicing/blob/main/time-slicing-proposal/image2.png?raw=true) |
| ![Total execution time](https://github.com/aishukamal/rl-time-slicing/blob/main/time-slicing-proposal/image4.png?raw=true) | ![Phase execution time](https://github.com/aishukamal/rl-time-slicing/blob/main/time-slicing-proposal/image1.png?raw=true) |
