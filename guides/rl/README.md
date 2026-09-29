# Rollout/Inference optimization for RL

Modern LLM post-training - RLHF and RL algorithms like GRPO and PPO - is
dominated by *rollout*: the generation step where the policy model produces
samples that are then scored and used to compute the training signal. In a
typical GRPO or PPO run, rollout generation is the single largest consumer
of wall-clock time, often accounting for the majority of each training step.
The trainer is idle while it waits for rollouts to come back, so any
improvement in rollout throughput and latency translates almost directly
into faster, cheaper training.

Rollout is also an *inference* problem, and it is a demanding one. Large
sample groups share a common prompt prefix, requests arrive in bursts, and
the same set of engines serves every step for the lifetime of the job. Yet
most RL frameworks route rollout requests with only naive load balancing,
which leaves performance on the table under these conditions. It also makes
the run vulnerable to long-tail latency and stragglers: a training step
cannot advance until its slowest rollout returns, so a handful of requests
sent to an already-busy or cold-cache replica can stall the entire step. This
is the problem llm-d and its Endpoint Picker (EPP) were built to solve for
production serving, and the same machinery applies directly to RL rollout.

The following guides describe two ways to improve inference & rollout in an RL
training loop.

## [Leverage llm-d and EPP directly in your RL framework](./llm-d-verl-integration.md)

llm-d's [no-Kubernetes mode](../no-kubernetes-deployment/README.md) uses
the routing stack - EPP, Envoy, and the model servers - as plain processes
rather than Kubernetes resources, so it drops cleanly into a Ray or Slurm
cluster without requiring a control plane. This lets an RL framework replace
its naive rollout routing with EPP through configuration alone, with no
changes to the framework's source. Using llm-d's EPP brings the benefits of
advanced routing - KV-cache-aware routing, session stickiness, burst-aware
routing, and more. Likewise, using llm-d as the serving backend brings the
benefits of the full inference stack - P/D (prefill/decode) disaggregation,
KV-cache offloading, and more - a meaningful throughput win for the large,
prefix-sharing sample groups that GRPO and PPO produce.

Two integration modes are supported:

- **llm-d EPP** - the framework calls the EPP directly to pick a specific model
  server, then dispatches the rollout request itself.
- **llm-d Router** - the framework sends generation to an llm-d Router that
  consults EPP for request routing.

See the [llm-d verl integration guide](./llm-d-verl-integration.md) for an
overview and setup.

## [The verl integration via a dedicated Python inference scheduler](./py-scheduler-verl-integration.md)

For [verl][verl] specifically, the [verl integration guide](./py-scheduler-verl-integration.md)
covers a deeper integration that replaces verl's routing layer with the
[py-inference-scheduler][py-inference-scheduler] - a Python implementation of
an inference request scheduler. It overrides verl's `AgentLoopManager` with a
custom `PyInferenceAgentLoopManager` that delegates every routing decision to
the scheduler, and adds an `InflightStore` that tracks active requests per
worker in real time to give the scheduler an accurate, up-to-the-moment view
of cluster load - supplementing the slower Prometheus-based metrics. It brings
llm-d's scoring, filtering, and flow-control capabilities to rollout
generation and supports P/D disaggregation.

This path targets verl v0.7.1 and relies on internal verl APIs introduced in
that release, so it is the right choice when you are on verl and want the
tightest integration; the deployment steps and configuration live in the
[py-inference-scheduler][py-inference-scheduler] repository. See the
[verl integration guide](./py-scheduler-verl-integration.md) for the
architecture and deployment overview.

[verl]: https://github.com/volcengine/verl
[py-inference-scheduler]: https://github.com/llm-d-incubation/py-inference-scheduler
