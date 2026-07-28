# Production Tuning: Deriving `maxConcurrency`

Because the Gateway Inference Extension supports a matrix of hardware, models, and topologies, there is no single "correct" value for `maxConcurrency`. If you change your hardware, model, or prompt structure, you must tune this value.

The goal of `maxConcurrency` is to maintain a **"healthy buffer"**—allowing exactly enough requests into the model server to meet your capacity targets and prevent GPU starvation, while holding all excess traffic at the gateway to enable Late-Binding, prevent KV-cache thrashing, and enforce fairness.

## Key Concepts

* **TTFT (Time-To-First-Token)**: The time from sending the request until the model generates the first token.
* **TPOT (Time-Per-Output-Token)**: The time to generate each subsequent token.
* **Active Batch Capacity ($N_{active}$)**: The maximum number of concurrent requests the GPU can process before breaching memory limits (KV-cache thrashing) or compute limits (TPOT degradation).
* **Lookahead Buffer ($B$)**: A small subset of requests held in the model server's local waiting queue to provide the continuous batcher with enough incoming work to form optimal execution chunks.

---

## The Throughput vs. Latency Trade-off (The 3-Layer Architecture)

LLM traffic does not arrive in a smooth stream; it arrives in sudden bursts. If you strictly cap your system at its average hardware limit, you clip the peaks and permanently degrade your throughput. Conversely, if you allow bursts to flood the GPU directly, latency SLAs will degrade.

To maintain high GPU utilization while strictly protecting SLAs, the Gateway Inference Extension uses a **3-Layer Architecture**:

1. **The Compute Limit (Engine Active Batch Limit = N)**
   * **What it is:** The strictly proven average concurrency the GPU can safely handle.
   * **Purpose:** Protects token speed (TPOT) and VRAM. No matter how much traffic spikes, the GPU never actively executes more than $N$ requests at a time (e.g., `--max-num-seqs` in vLLM).
2. **The Dispatch Gate (EPP `maxConcurrency` = N + B)**
   * **What it is:** The Compute Limit ($N$) plus a dynamically sized Lookahead Buffer ($B$).
   * **Purpose:** Keeps the engine fed. By allowing a small, mathematically sound subset of requests to sit in the model server's local waiting queue, the engine has enough continuous work to maximize throughput.
3. **The Burst Queue (EPP Flow Control `maxRequests` = 200+)**
   * **What it is:** The centralized ingress queue limit.
   * **Purpose:** Absorbs sudden traffic bursts. By holding the burst centrally in the Gateway instead of pushing it to the engine, the EPP can enforce multi-tenant Fairness, sort by Priority, and dynamically route requests to other replicas if they free up (Late-Binding).

---

## Bottleneck Analysis

Every LLM deployment is constrained by two independent physical ceilings. Your true hardware limit is always the active bottleneck: `min(Compute_Bound, Memory_Bound)`.

1. **Compute Bound (FLOPs)**: The point where token generation slows down and latency SLAs fail.
2. **Memory Bound (VRAM)**: The point where the KV-cache runs out and the engine begins thrashing (swapping or preempting requests).

To calculate these limits effortlessly, we use the `tuning_wizard.py` script.

### Step 1: Gather System Telemetry (For Compute Bound)

To find the physical execution limit of the GPU, you must observe it under stress.

1. **Configure for Unbounded Testing**: Start a single model server directly (bypassing the gateway) and set its active batch limit to an artificially high number (e.g., `2048` for `--max-num-seqs` in vLLM).
2. **Run a Representative Load Test**: Run a step-load test. Use a dataset that accurately reflects your production prompt lengths.
3. **Identify Saturation**: Find the highest stable load stage immediately preceding your defined SLO breach (e.g., P99 TPOT spiking above 100ms).
4. **Extract Metrics**: Record the **Mean Throughput (RPS)** and **Mean Latency (in seconds)**.

> [!NOTE]
> If the engine begins KV-cache thrashing before TPOT degrades, your workload is strictly Memory-Bound. Record the concurrency just before the thrashing occurred—you will use the Analytical Memory Bound as your primary limit.

### Step 2: Gather Workload Statistics (For Memory Bound)

To find the physical VRAM limit, you must know the shape of your actual production traffic.

1. Extract the mean and standard deviation of your Input Sequence Lengths (ISL) and Output Sequence Lengths (OSL) from your dataset or benchmark report.
2. Check your model server startup logs or metrics to find the total number of available KV cache blocks.

> [!TIP]
> **Curling for Parameters:** Instead of parsing logs, you can easily discover parameters like total KV blocks by querying the inference engine's metrics endpoint (usually at `:8000/metrics`). For example, in vLLM, look for the `vllm:cache_config_info` metric.

### Step 3: Run the Tuning Wizard

Pass all gathered parameters to the tuning wizard. The script applies queueing theory and statistical variance to output the exact limits you should configure.

```bash
python3 guides/flow-control/scripts/tuning_wizard.py \
  --throughput 12.5 \
  --latency-sec 3.45 \
  --gpu-blocks 21875 \
  --block-size 16 \
  --isl-mean 4000 \
  --isl-std 1500 \
  --osl-mean 800 \
  --osl-std 300 \
  --shared-prefix 3000 \
  --enable-prefix-caching \
  --z-score 2.0
```

*(Note: If run without arguments, the script enters a guided interactive mode).*

### Sourcing the Inputs

| Parameter | Type | Default | Description | Sourcing |
| :--- | :--- | :--- | :--- | :--- |
| `--throughput` | `float` | N/A | Mean throughput (requests per sec). | Benchmark report. |
| `--latency-sec` | `float` | N/A | Mean end-to-end latency (**in seconds**). | Benchmark report. |
| `--gpu-blocks` | `int` | N/A | Total available KV cache blocks. | Engine logs or metrics. |
| `--block-size` | `int` | `16` | Number of tokens per KV block. | Engine config or metrics. |
| `--isl-mean` / `--isl-std` | `float` | N/A | Mean / StdDev of Input Sequence Length. | Dataset statistics. |
| `--osl-mean` / `--osl-std` | `float` | N/A | Mean / StdDev of Output Sequence Length. | Dataset statistics. |
| `--shared-prefix` | `int` | `0` | Length of static system prompt. | Operator knowledge. |
| `--enable-prefix-caching` | `flag` | `False` | MUST be passed if engine prefix caching is ON. | Operator knowledge. |
| `--z-score` | `float` | `2.0` | Statistical safety margin (e.g., 2.0 = ~95% confidence). | Operator knowledge. |

> [!WARNING]
> **The Prefix Caching Trap:** If your workload uses a massive shared prefix but you forget to enable prefix caching on the model server, the engine will store a duplicate copy of the prefix for every single active request, drastically reducing VRAM. Pass the `--enable-prefix-caching` flag *only* if it is actively running on the engine.

### Step 4: Apply Configuration

Based on the wizard's output, configure your environment:

**Model Server (e.g., vLLM)**
Set the active batch limit to the calculated Compute Limit ($N_{active}$). If you use `headroom` (see below), set this to the peak burst limit $\ge N_{peak} = N_{active} \times (1 + \text{headroom})$:

```bash
vllm serve ... --max-num-seqs <N_active_or_N_peak>
```

**Gateway (EPP)**
Configure the `EndpointPickerConfig` with the correct schema. You must enable the `flowControl` feature gate and reference your saturation detector plugin:

```yaml
apiVersion: llm-d.ai/v1alpha1
kind: EndpointPickerConfig

featureGates:
- flowControl

plugins:
- type: concurrency-detector
  parameters:
    maxConcurrency: <N_active + B> # From wizard output
    headroom: 0.0

saturationDetector:
  pluginRef: concurrency-detector

flowControl:
  maxRequests: 200 # Set a healthy burst queue limit
  maxBytes: "10Gi" # Adjust to protect host memory
```

> [!NOTE]
> The `maxRequests` and `maxBytes` parameters in the `flowControl` section are static bounds intended to manage host memory pressure and protect the Gateway proxy from running out of memory during extreme traffic bursts. Set them based on your available resources.

### Tuning `headroom` (Affinity Bursting)

The `headroom` parameter allows the scheduler to temporarily bypass the strict concurrency limit $N$ for a specific model server, provided the incoming request has high prefix cache affinity with that server.

* **Default (0.0):** Leave this at `0.0` for most workloads.
* **Advanced Tuning (0.1 to 0.2):** If your workload relies heavily on prefix caching, you can set this between `0.1` and `0.2` (10% - 20%). Because cache hits skip the compute-heavy prefill phase, a node can safely process a slightly higher concurrency of cached requests. *Note: Ensure your hardware has enough VRAM to support the generated decode tokens for this extended capacity, otherwise the engine will resort to KV-cache thrashing.*

---

### Choosing the Saturation Detector: Open-Loop vs. Closed-Loop

The EPP provides two saturation detectors, each optimized for different traffic profiles.

* **`utilization-detector` (Default / Closed-Loop):** Reacts to the true physical state of the hardware (KV cache utilization, engine queue depth). Because it measures actual memory footprint rather than just request counts, it is highly accurate and the best choice for heterogeneous hardware pools or workloads with highly variable context lengths. It relies on a periodic (50ms) telemetry scrape interval and is optimized for sustained, organic traffic.
* **`concurrency-detector` (Open-Loop):** Evaluates capacity using zero-latency optimistic accounting of in-flight requests. Because it does not wait for engine telemetry, its instantaneous reaction time makes it a great choice for workloads that experience sudden, massive traffic bursts.

## ⚠️ Operational Notes

1. **Re-tune on Workload Shifts**: The derived limits are tied to the prompt lengths you benchmarked. If your live traffic shifts to significantly longer prompts, you will need to re-run the wizard, as longer contexts consume more compute and memory.
2. **Handling High Variance**: If your prompt lengths vary wildly (e.g., mixing 100-token chat with 80,000-token document analysis), the wizard will calculate a higher statistical variance. You can safely trade a small amount of maximum throughput for a larger safety buffer by increasing the `--z-score` during the wizard run, protecting the engine from KV-cache thrashing.
3. **Gateway Memory Limits**: Protecting the GPU by queueing at the Gateway shifts the memory burden to the Gateway proxy. Ensure your EPP `maxBytes` and `maxRequests` limits are configured to safely handle the expected payload sizes during massive traffic spikes.
