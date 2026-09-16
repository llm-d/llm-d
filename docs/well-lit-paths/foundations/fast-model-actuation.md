# Fast Model Actuation

In Kubernetes, once a Pod has been allocated some GPUs it keeps that exclusive allocation for the rest of the Pod's lifetime. Scaling a model server up or down therefore means creating or destroying whole pods, and each new pod pays a full **cold start** — pull the image, initialize the runtime, load gigabytes of weights, and compile CUDA graphs — before it can serve a single request. On a shared GPU pool, swapping between model variants repeats that cold start on every swap.

Fast Model Actuation (FMA) attacks vLLM startup time with two complementary techniques: **vLLM sleep/wake**, which parks a loaded model's tensors in main memory and restores them to the GPU in seconds — skipping model loading and CUDA-graph compilation entirely — and a **launcher** process that loads the vLLM Python modules once and spawns child vLLM instances on demand, skipping that module import. Together they turn model swap-in and replica scale-up from a minutes-scale cold start into a near-instant operation — without changing steady-state serving performance.

> [!NOTE]
> FMA's value is *actuation speed*, not inference throughput. Resident servers add only a small (~2.5%) CPU-memory overhead, and served performance matches a standard deployment. The [Workload Autoscaling](workload-autoscaling.md) capability decides *when* to scale; FMA addresses *how fast* the new capacity becomes ready.

## Deploy

See the [fast model actuation guide](../../../guides/fast-model-actuation) for manifests and step-by-step deployment.

For **autoscaled** FMA — see the [fast model actuation + KEDA autoscaling guide](../../../guides/fast-model-actuation-keda), where KEDA scales the requester pool on EPP flow-control saturation metrics and each scale-up drives an FMA hot-wake (resident sleeping instance) or warm-create (new instance on an existing launcher).

## Architecture

FMA circumvents that exclusive, lifetime-long GPU allocation with a **dual-pods** design coordinated by the FMA controllers:

- **Server-requesting pods** reserve GPU resources through the Kubernetes scheduler and kubelet — keeping cluster capacity accounting accurate — but do not run inference themselves.
- **Launcher pods** (server-providing) run vLLM while requesting **zero** GPUs. They can access all of their node's GPUs through provisions that don't count as scheduler usage, and the FMA controllers point vLLM at the specific GPU(s) reserved by the requesting pod via `CUDA_VISIBLE_DEVICES`.
- **FMA controllers** create and delete launchers, bind requesters to launchers and to the vLLM instances within them, create and delete those vLLM instances, and orchestrate sleep/wake so a fixed GPU pool can host many model variants.

### Actuation paths

When capacity is needed, the controller selects a launcher and takes the fastest available path:

- **Hot start** — wake an already-resident, sleeping instance by moving weights from CPU memory back to the GPU over PCIe (sub-second).
- **Warm start** — an existing launcher creates a fresh vLLM instance using preloaded modules (tens of seconds).
- **Cold start (with launcher)** — create a new launcher pod, then initialize the instance; still faster than a no-FMA cold start, which additionally pays pod scheduling and image pull.

To bound memory, the controller keeps at most a configurable number of instances sleeping per accelerator, evicting the least-recently-used instances when that budget is exceeded.
