# P/D Disaggregation

Prefill/decode (P/D) disaggregation splits inference into separate **prefill** and **decode** pools. Prefill pods process the prompt and produce its KV cache; decode pods pull that KV cache over a KV transfer connector (NIXL by default) and generate the output tokens. The two pools scale independently, so heavy prompts never stall token generation.

For why P/D disaggregation helps, how requests flow between the two pools, and tuning guidance, see [Disaggregated Serving](../../docs/architecture/disaggregation/pd-disaggregation.md).

## Choose a guide

llm-d drives the two phases of a request in one of two ways. Each has its own guide:

| | [Routing sidecar](sidecar/README.md) | [Coordinator](coord/README.md) (experimental) |
| --- | --- | --- |
| Who drives the phases | A routing sidecar in front of each decode engine: the request lands on the decode pod, whose sidecar sends the prompt to the prefill pod and then decodes locally | A standalone Coordinator service runs a `prefill → decode` pipeline per request and calls the router once per phase |
| When the decode pod is picked | Together with the prefill pod, in one scheduling cycle, before prefill starts | After prefill has finished, based on the pool's state at that time |
| Router | One EPP with the `disagg-profile-handler`; Standalone or Gateway Mode | One EPP with the `header-profile-handler`; Gateway Mode only |
| Accelerators and model servers | NVIDIA, AMD, Intel XPU, Google TPU and community accelerators; vLLM and SGLang | NVIDIA GPU with vLLM |
| Maturity | Nightly E2E on several platforms | Community; not covered by nightly E2E |

Both guides deploy the prefill and decode roles as one [`DisaggregatedSet`](https://lws.sigs.k8s.io/docs/concepts/disaggregatedset/) and use the same prefill and decode scheduling profiles.

**Start with the [routing sidecar](sidecar/README.md)** unless you have a reason to pick the Coordinator. It is the established path, it covers every accelerator, and under uniform or steady load, or when prefill is the bottleneck, the two architectures perform the same.

**Consider the [Coordinator](coord/README.md)** when decode is the contended stage, per-request load is heterogeneous (long-tailed outputs, widely varying prompt or image sizes), and requests arrive in bursts. Its later decode pick then keeps the decode pods balanced: in the [Coordinator Disaggregation benchmarks](../coord-disaggregation/README.md#benchmark) it delivered 4–10% higher throughput and up to 15% lower p99 TTFT in that regime, at the cost of a few milliseconds of TTFT per request for the extra Gateway round trip per phase.

## Layout

```
guides/pd-disaggregation/
  sidecar/        guide.yaml, README.md, router/, modelserver/ (every accelerator)
  coord/          guide.yaml, README.md, router/, coordinator/, modelserver/ (NVIDIA GPU, vLLM)
```
