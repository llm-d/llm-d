# verl Integration

[verl][verl] is a flexible, high-performance RLHF/GRPO training framework built on Ray. By default, verl routes rollout requests to its model-server actors with simple [least-requests or LRU logic][verl-lb]. This integration replaces that routing layer with the llm-d scheduler, bringing the same scoring, filtering, and flow-control capabilities used in production inference to RL training rollouts.

> [!IMPORTANT]
> This integration targets **[verl v0.7.1][verl-release]**. It relies on internal API signatures (`load_balancer_handle`, `_acquire_server`) introduced in that release and is **not backwards compatible** with earlier versions.

## Architecture

The integration overrides verl's `AgentLoopManager` with a custom `PyInferenceAgentLoopManager` that delegates all routing decisions to the llm-d scheduler engine. An `InflightStore` tracks active requests per worker in real time, augmenting the slower Prometheus-based metrics to give the scheduler an accurate view of cluster load during rollout generation.

```
  verl Training Loop
       │
       ▼
  ┌─────────────────────────────────┐
  │  PyInferenceAgentLoopManager    │
  │  (replaces default routing)     │
  │                                 │
  │  ┌───────────┐  ┌────────────┐  │
  │  │ Scheduler │◀─│ Inflight   │  │
  │  │ Engine    │  │ Store      │  │
  │  └─────┬─────┘  └────────────┘  │
  └────────┼────────────────────────┘
           │ scored routing
     ┌─────┴─────┬────────────┐
     ▼           ▼            ▼
  [Worker 0]  [Worker 1]  [Worker N]
  vLLM/SGLang actors on Ray
```

Key components:

- **`verl_hook.py`** — `InferenceSchedulerServerManager` and `PyInferenceAgentLoopManager`, injected into the verl training loop.
- **`InflightStore`** — Real-time per-worker request tracking to supplement Prometheus metrics.
- **`backends/verl/`** — Monkey-patches for vLLM and SGLang enabling metrics extraction and correct environment propagation.
- **`datalayer/metrics/verl/`** — Backend-specific HTTP scraping to fetch and parse worker metrics.

## Deploy

For prerequisites, cluster setup, scheduler configuration, and step-by-step job submission instructions, see the full [verl Integration Guide][verl-guide] in the `py-rl-scheduler` repository.

At a high level:

1. **Set up a Ray cluster** with shared metrics directory and (on K8s) a scheduler ConfigMap — see the [verl multinode docs][verl-multinode] or [KubeRay post-training guide][kuberay-guide].
2. **Configure the scheduler** via `scheduler.yaml` — use the bundled default or mount a custom config.
3. **Submit the training job** with the integration flags:

   ```bash
   ray job submit \
       --runtime-env integration/verl/examples/runtime-env.yaml \
       -- python3 your_training_script.py \
           actor_rollout_ref.rollout.disable_log_stats=False \
           +actor_rollout_ref.rollout.agent.agent_loop_manager_class=integration.verl.verl_hook.PyInferenceAgentLoopManager
   ```

4. **Verify** via the Ray Dashboard — look for scheduler routing decisions in the worker/driver logs and monitor `perf/throughput` and `timing_s/agent_loop/slowest/generate_sequences` metrics.

## Further Reading

- [Full verl Integration Guide][verl-guide] — prerequisites, configuration options, and example scripts
- [Scheduler Customization Guide][scheduler-customization] — designing custom scoring profiles, filters, and flow-control plugins
- [verl Documentation][verl-docs] — installation, quickstart, and dataset preparation

[verl]: https://github.com/volcengine/verl
[verl-lb]: https://github.com/verl-project/verl/blob/fa69bc0e2bd2493b6cd74c950413edc081dd10ef/verl/experimental/agent_loop/agent_loop.py#L58
[verl-release]: https://github.com/volcengine/verl/releases/tag/v0.7.1
[verl-guide]: https://github.com/llm-d-incubation/py-inference-scheduler/blob/main/integration/verl/README.md
[verl-multinode]: https://verl.readthedocs.io/en/latest/start/multinode.html
[kuberay-guide]: https://docs.ray.io/en/latest/cluster/kubernetes/examples/verl-post-training.html
[verl-docs]: https://verl.readthedocs.io/en/latest/
[scheduler-customization]:  https://github.com/llm-d-incubation/py-inference-scheduler/blob/main/docs/scheduler_customization.md
