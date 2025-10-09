# Hardware Backends

Some of our examples support additional specialized hardware backends. This document should track hardware support throughout the various guides, points of contact for each hardware vendor, and document any workarounds to use specialty hardware.

## Hardware Backend Extensibility Requirements

In the llm-d community we welcome contributions from hardware providers, with the bare minimum requirement of having publicly available container images, which is enough to be referenced in hardware and platform support documentation. 

For integration into guides in our main repo our standard for contribution is higher, **requiring**:
- A point of contact responsible for maintaining that hardware flavor of the guide
- Verbal or written confirmation the guide variant works

Coming soon: as we progress as a community we aim to increase our requirements to have active CI coverage for all hardware guide variants, but this is outside the scope of `llm-d`'s `v0.3.0` release.

**Note:** The community can assist but is not resposible for keeping hardware guide variants updated. The community reserves the right to remove stale examples and documentation with regard to hardware support.

## TPU

### TPU LLM-D Points of Contact

Points of contact:
- Edwin Hernandez (@Edwinhr716)
- Cong Liu (@liu-cong, congliu.thu@gmail.com)
- Clayton Coleman (@smarterclayton)

### TPU Guide Support

We support TPU backend in the [`inference-scheduling` guide](../../guides/inference-scheduling/) via the [TPU modelservice values file](../../guides/inference-scheduling/ms-inference-scheduling/values_tpu.yaml).

> For more information on TPU, see the [GKE infrastructure provider docs](https://github.com/llm-d/llm-d/tree/main/docs/infra-providers/gke#prerequisites)

## XPU

### XPU LLM-D Points of Contact

Points of contact:
- Yuan Wu (@yuanwu2017, yuan.wu@intel.com)

### XPU Guide Support

We support XPU backend in the [`inference-scheduling` guide](../../guides/inference-scheduling/) via the [XPU modelservice values file](../../guides/inference-scheduling/ms-inference-scheduling/values_xpu.yaml).

## AMD ROCm 

Coming soon, hopefully in the `v0.3.1` release.
