# llm-d Accelerators

llm-d supports multiple accelerator vendors and we are expanding our coverage.

## Support

Maintainers for each accelerator type are listed below. See our well-lit path guides for details of deploying on each hardware type.

| Vendor | Models | Maintainers | Supported Well-lit Paths |
| --- | --- | --- | --- |
| AMD | ROCm | Kenny Roche (Kenny.Roche@amd.com) | Coming soon |
| Google | [TPU](../infra-providers/gke/README.md#llm-d-on-google-kubernetes-engine-gke) | Edwin Hernandez (@Edwinhr716), Cong Liu (@liu-cong, congliu.thu@gmail.com) | [Inference Scheduling](../../guides/inference-scheduling/README.md) |
| Intel | XPU | Yuan Wu (@yuanwu2017, yuan.wu@intel.com) | [Inference Scheduling](../../guides/inference-scheduling/README.md) |
| NVIDIA | GPU | Will Eaton (weaton@redhat.com), Greg (grpereir@redhat.com) | All |

## Requirements

We welcome contributions from accelerator vendors. To be referenced as a supported hardware vendor we require at minimum a publicly available container image that launches vLLM in the [recommended configuration](../../guides/prereq/infrastructure#optional-vllm-container-image).

For integration into the well-lit paths our standard for contribution is higher, **requiring**:

- A named maintainer responsible for keeping guide contents up to date
- Manual or automated verification of the guide deployment for each release

> [!NOTE]
> We aim to increase our requirements to have active CI coverage for all hardware guide variants in a future release.

> [!NOTE] 
> The community can assist but is not responsible for keeping hardware guide variants updated. We reserve the right to remove stale examples and documentation with regard to hardware support.
