# Multimodal Serving in llm-d

Multimodal models process text together with images, video, or audio. Serving
these models introduces a distinct encoding stage that can consume substantial
compute before language-model prefill and decode begin.

llm-d supports two serving patterns:

1. **Aggregated serving** runs encode, prefill, and decode in each model-server
   replica.
2. **Encode-disaggregated serving** moves multimodal encoding to a dedicated
   worker pool.

## Guide Index

* [Aggregated Serving Guide](./aggregation/README.md): deploy unified
  model-server replicas with multimodal prefix-cache-aware and load-aware
  routing.
* [Encode-Disaggregated Serving Guide](./e-disaggregation/README.md): deploy
  E/PD or E/P/D, including heterogeneous SGLang E/PD with Intel XPU Encode
  workers and an NVIDIA GPU PD worker.

## Aggregated Serving

Each model-server replica executes the full request:

1. **Encode** converts media into feature embeddings.
2. **Prefill** processes the embeddings and prompt tokens and builds KV cache.
3. **Decode** generates output tokens.

The llm-d Router can combine multimodal prefix-cache and load signals when
selecting a replica. Keeping all stages together avoids cross-pod embedding
transfer, but encoder and language-model capacity cannot be scaled or placed
independently.

## Encode-Disaggregated Serving

Dedicated Encode workers process media, while downstream PD or P/D workers
consume the resulting embeddings. This permits independent scaling and
hardware specialization, but introduces network transfer and coordination
overhead.

The control and data paths depend on the inference backend.

### vLLM

For the vLLM configurations, the llm-d Router chooses Encode and downstream
workers. Routing metadata passes through the llm-d disaggregation sidecar, and
the vLLM EC Connector transfers embeddings with a NIXL data plane and ZMQ
control plane.

### SGLang

For the heterogeneous SGLang configuration, the llm-d Router chooses only a PD
worker. That PD worker owns encoder dispatch using SGLang `--encoder-urls`.
SGLang transfers embeddings directly from the Encode workers to the PD
scheduler with the `zmq_to_scheduler` backend. This path does not use the
llm-d disaggregation sidecar, vLLM EC Connector, or NIXL for E-to-PD embedding
transfer.

## Supported Topologies

* **E/PD**: dedicated Encode workers and combined Prefill/Decode workers.
* **E/P/D**: dedicated Encode, Prefill, and Decode workers.

## Comparison

| Dimension | Aggregated | Encode-disaggregated |
| --- | --- | --- |
| Worker roles | Every replica runs Encode + Prefill + Decode | Encode is a dedicated tier; Prefill and Decode may be combined or separate |
| Encoder scaling | Coupled to language-model replicas | Independent |
| Hardware placement | One accelerator type per replica | Encode and language stages can use different hardware |
| Embedding transfer | Local to the model server | Cross-pod; mechanism depends on the backend |
| Multi-item parallelism | Limited to one replica's local execution | Items can be distributed across Encode workers |
| Operational complexity | Lower | Higher |

## Choosing a Pattern

Use aggregated serving when media inputs are light, the encoder is not a
bottleneck, or minimizing deployment and network complexity matters most.

Evaluate encode disaggregation when requests frequently contain multiple or
high-resolution media items, encoder work dominates time to first token, or
separate accelerator pools improve utilization or cost. Performance gains are
not universal: model architecture, media shape, arrival rate, output length,
network behavior, and the Encode-to-PD ratio all affect the result. Compare
both patterns with a representative workload before production use.
