# Centralized Image Components

This directory contains Kustomize Components that define the **default container images** for each accelerator/engine combination. Guides include the relevant component instead of hardcoding image versions inline, so a version bump requires editing one file.

## Available Components

| Component | Image | Description |
|-----------|-------|-------------|
| `gpu-vllm` | `vllm/vllm-openai` | NVIDIA GPU with vLLM |
| `gpu-sglang` | `docker.io/lmsysorg/sglang` | NVIDIA GPU with SGLang |
| `amd-vllm` | `ghcr.io/llm-d/llm-d-rocm` | AMD GPU with vLLM |
| `amd-sglang` | `docker.io/lmsysorg/sglang` | AMD GPU with SGLang (ROCm variant) |
| `cpu-vllm` | `ghcr.io/llm-d/llm-d-cpu` | CPU with vLLM |
| `xpu-vllm` | `ghcr.io/llm-d/llm-d-xpu` | Intel XPU with vLLM |
| `xpu-vllm-omni` | `ghcr.io/llm-d/llm-d-xpu-omni` | Intel XPU with vLLM-Omni |
| `hpu-vllm` | `ghcr.io/llm-d/llm-d-hpu` | Intel Gaudi (HPU) with vLLM |
| `tpu-vllm` | `vllm/vllm-tpu` | Google TPU with vLLM |
| `routing-sidecar` | `ghcr.io/llm-d/llm-d-routing-sidecar` | Routing sidecar for PD disaggregation |

## Usage

Include a component in your overlay's `kustomization.yaml`:

```yaml
components:
  - ../../../../../recipes/modelserver/components/images/gpu-vllm
```

The component replaces the `REPLACE_MODEL_SERVER_IMAGE` placeholder (or `REPLACE_ROUTING_SIDECAR_IMAGE` for the sidecar) with the default image.

## Overriding

If a guide requires a different image (e.g. a nightly build, a vendor fork, or a platform-specific variant), add an `images:` section in the overlay that takes precedence over the component:

```yaml
components:
  - ../../../../../recipes/modelserver/components/images/gpu-vllm

# TODO(#<issue-number>): Remove override once <reason> is resolved.
images:
  - name: REPLACE_MODEL_SERVER_IMAGE
    newName: ghcr.io/example/custom-vllm
    newTag: nightly-20260601
```

> **Override policy:** Every inline `images:` override that diverges from the component default **must** include a `TODO` comment referencing a tracking issue for cleaning up the override. This ensures overrides are intentional, documented, and eventually removed.
