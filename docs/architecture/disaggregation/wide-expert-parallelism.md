# Wide Expert Parallelism

Wide expert parallelism (wide-EP) serves very large Mixture-of-Experts (MoE) models across many nodes: the attention layers run data-parallel (DP) and the expert (MLP) layers run expert-parallel (EP), so every accelerator holds a slice of the experts and its own share of the KV cache. In llm-d, wide-EP is combined with [prefill/decode disaggregation](pd-disaggregation.md), multi-node pod groups ([LeaderWorkerSet](../workload-apis/leaderworkerset.md), optionally managed by a [DisaggregatedSet](../workload-apis/disaggregatedset.md)), and DP-aware routing in the llm-d Router.

This page explains the concepts. For a deployment, see the [Wide Expert Parallelism well-lit path](../../../guides/wide-ep/README.md); for benchmarked MoE recipes, see the Models guides, such as [DeepSeek-V4](../../../guides/models/deepseek-v4/README.md) and [GLM-5.2](../../../guides/models/glm-5-2/README.md).

## Why Wide Expert Parallelism

Very large MoE models like DeepSeek-R1 can consume 500 GB+ of memory just to hold the weights of the model, pressuring KV cache space for long context and high throughput serving. This problem is especially magnified for models with MLA attention, which replicates the KV cache when sharded with tensor parallelism.

To address these issues, model servers support DP/EP deployments, which deploy the attention layers with data parallelism and the MLP layers with expert parallelism. This deployment pattern scales the KV cache space, as it:

- **Scales to multiple nodes**: the collective operations (dispatch/combine) are sparse, since tokens are only sent to the expert rank after routing, so they consume much less bandwidth than the all-reduces of TP setups. This makes them suitable to run over slower interconnects (InfiniBand, RoCE rather than NVLink).
- **Avoids KV replication**: attention is data-parallel (TP=1 in every DP group), so there is only one copy of each token's KV.

## The DP/EP Forward Pass

The following visualizes the forward pass in a DP/EP deployment in vLLM:

<p align="center">
  <picture>
    <img src="../../assets/dp-ep-deployment.svg" alt="DP/EP deployment">
  </picture>
</p>

1. Each rank runs attention independently.
2. The MoE router selects the `topk` experts for each token. This is sparse: in the case of DeepSeek, 8 out of 256 experts are selected.
3. Tokens are "dispatched" (using the `topk_id`) to the proper expert rank (e.g. the green token on rank 1 is routed to E1 and E3).
4. Each expert runs independently.
5. Tokens are "combined" back to the original attention rank.

The dispatch/combine collectives run on an all-to-all backend such as DeepEP (NVIDIA) or MoRI-EP (AMD).

## Combining with P/D Disaggregation

Multi-node wide-EP deployments are typically combined with disaggregated serving because:

- Disaggregation avoids "bubbles" where rank N is computing a prefill and rank M is computing a decode.
- Specialized kernels for prefill and decode can be used (e.g. DeepEP high-throughput vs. DeepEP low-latency).

Prefill and decode therefore each run as their own DP/EP group, and the KV cache moves from the prefill rank to the decode rank over RDMA (InfiniBand, RoCE, EFA) with a KV connector such as NIXL or MoRI-IO. See [Disaggregated Serving](pd-disaggregation.md) for the P/D mechanism itself.

## Multi-Node Pod Groups

One DP/EP group spans several nodes, so it is deployed as a [`LeaderWorkerSet`](../workload-apis/leaderworkerset.md) (LWS) group: a leader pod and worker pods, one per node, created, scheduled and restarted together. Each P/D role (prefill, decode) is one such group, or several replicas of it.

A [`DisaggregatedSet`](../workload-apis/disaggregatedset.md) manages the prefill and decode `LeaderWorkerSet`s as one versioned unit: changing either role rolls both, one revision covers all roles, and `slices` replicates the whole prefill + decode topology into independent copies. During a rollout, the llm-d Router's `disaggregatedset-rollout-screener` pins each request to a single revision, so prefill and decode never pair across revisions. Two plain `LeaderWorkerSet`s work as well, without the coordinated rollout. See [Operating the DisaggregatedSet](../../operations/disaggregation/disaggregatedset.md).

## DP-Aware Routing

With data-parallel attention, every DP rank holds its own KV cache and request queue, so the router gets the most out of prefix-cache affinity and load balancing when it picks a rank, not just a pod. vLLM can run one API server per DP rank, each on its own port (for example `8000`-`8007` for eight ranks per pod); the `InferencePool` then lists every rank port in its target ports, and the llm-d Router treats each `podIP:port` as an endpoint to filter and score. On decode pods, the routing sidecar listens on the rank ports and forwards to the engine's per-rank ports.

When the model server exposes a single API port per pod instead (for example several API servers sharing one port through `SO_REUSEPORT`), the pool targets that port, the router picks a pod, and the model server balances requests across its ranks.

## Request Flow

<p align="center">
  <picture>
    <img src="../../assets/wide-ep.svg" alt="Multi-Node Wide Expert Parallelism">
  </picture>
</p>

1. A request arrives at the proxy, which forwards it to the router (EPP).
2. The router schedules the request with P/D disaggregation, using the pod labels to detect the prefill and decode roles, and picks specific DP ranks within the pod groups.
3. The request is routed to the decode pod's sidecar, which forwards it to the selected prefill rank.
4. The prefill rank processes the prompt, executing the forward pass with DP/EP; the all-to-all backend (DeepEP, MoRI) executes the cross-node dispatch/combine collectives. vLLM returns metadata about how to retrieve the KV blocks.
5. The decode rank pulls the KV cache over RDMA (InfiniBand, RoCE, EFA) with the KV connector (NIXL, MoRI-IO).
6. The decode rank generates the output tokens, executing the forward passes with DP/EP.

## Further Reading

- [LeaderWorkerSet (LWS)](../workload-apis/leaderworkerset.md) — pod group controller for multi-node accelerator workloads.
- [DisaggregatedSet](../workload-apis/disaggregatedset.md) — multi-role topology controller managing synchronized slices.
- [Wide Expert Parallelism well-lit path](../../../guides/wide-ep/README.md) — deploy wide-EP on NVIDIA GPU, AMD GPU or Intel XPU.
- [Disaggregated Serving](pd-disaggregation.md) — the P/D mechanism wide-EP builds on.
- vLLM docs: [DP deployment](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/), [EP deployment](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/), and [DeepEP and DeepGEMM](https://docs.vllm.ai/en/latest/design/fused_moe_modular_kernel/).
