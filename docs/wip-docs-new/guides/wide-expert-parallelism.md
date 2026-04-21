# Multi-Node Wide Expert Parallelism

Very large MoE models like DeepSeek-R1 can consume 500GB+ of RAM just to hold the weights of the model, pressuring KV cache space for long context and high throughput serving. This problem is especially magnified for models with MLA attention, which replicates the KV cache when sharded with tensor parallelism.

To address these issues, model servers support DP/EP deployments, which deploys the attention layers with data parallelism and the MLP layers with expert parallelism. This deployment pattern enables scaling the KV cache space, as the pattern:
* **Scales to multiple nodes** - since the collective operations (dispatch/combine) are sparse - tokens are only sent to the expert rank after routing - they consume much less bandwidth than the all-reduces used in TP setups, making them suitable to run over slower interconnects (IB, RoCE rather than NVLink)
* **Avoids KV replication** - since attention is data-parallel (TP=1 in every DP group), there is only one copy of each tokens's KV

The following visualizes the forward pass in a DP/EP deployment in vLLM:

![DP/EP deployment](../../assets/dp-ep-deployment.svg)

The following steps occurs:
* Each rank runs attention independently
* MoE router selects the `topk` experts for each token (this is sparse - in the case of DeepSeek, 8 out of 256 experts are selected)
* Tokens are "dispatched" (using the `topk_id`) to the proper expert rank (e.g, the green token on rank 1 is routed to E1 and E3)
* Each expert runs independently
* Tokens are "combined" backed to the original attention rank

> [!IMPORTANT]
> Dispatch/combine uses the **DeepEP** backend over NVSHMEM with GPU-initiated RDMA (`ibgda` transport), requiring full-mesh InfiniBand/RoCE connectivity. 

## Deploy

See the [Wide Expert Parallelism guide](https://github.com/llm-d/llm-d/tree/main/guides/wide-ep-lws) for manifests and step-by-step deployment.

## Architecture

Multi-node "WideEP" deployments are typically combined with disaggregated serving because:
* Disaggregatation avoids "bubbles" where Rank N is computing a prefill and Rank M is computing a decode
* Specialized kernels for prefill and decode can be used (e.g. DeepEP HT vs DeepEP LL)

As a result, we leverage the following design for the deployment:
* Disaggregated prefill and decode via llm-d's EPP
* `LeaderWorkerSet` to manage multi-node pod group deployment of vLLM
* DP/EP deployment configuration in vLLM

![Multi-Node Wide Expert Parallelism](../../assets/wide-ep.svg)

The request flow works as follows:
- Request arrives at the proxy, which forwards the request to the EPP
- EPP schedules the request with P/D disaggregation, using the labels to detect the decode and prefill variants. The EPP schedules to specific pods within the LWS
- Request is routed to the sidecar, which forwards the request to the prefill pods
- Prefill instance processes the prompt, executing the forward pass with DP/EP. DeepEP executes the cross-node dispatch/combine collectives. vLLM returns metadata about how to retrieve the KV blocks
- Decode instance pulls the KVs over RDMA (IB, RoCE, EFA) with NIXL
- Decode instances processes the decodes, executing the forward passes with DP/EP. DeepEP executes the cross-node dispatch/combine collectives

## Further Reading

See:
* [PD Architecture](../architecture/advanced/disaggregation/README.md) for more details on disaggregation in llm-d
* [vLLM docs on DP deployment](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/)
* [vLLM docs on EP deployment](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/)
* [vLLM docs on DeepEP and DeepGEMM](https://docs.vllm.ai/en/latest/design/fused_moe_modular_kernel/)
