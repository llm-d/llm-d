# Centralized Image Components

This directory contains Kustomize Components that define the **default container images** for each accelerator/engine combination. Guides include the relevant component instead of hardcoding image versions inline, so a version bump requires editing one file.

## Available Components

```console
├── amd-sglang
│   ├── rocm700-mi30x
│   ├── rocm700-mi35x
│   ├── rocm720-mi30x
│   └── rocm720-mi35x
├── amd-vllm
│   ├── llm-d
│   ├── nightly
│   └── release
├── amd-vllm-omni
│   └── release
├── cpu-vllm
│   ├── llm-d
│   ├── nightly
│   └── release
├── gpu-sglang
│   ├── nightly
│   └── release
├── gpu-trtllm
│   └── release
├── gpu-vllm
│   ├── aws-efa
│   │   ├── llm-d
│   │   └── release
│   ├── ec-connector
│   ├── llm-d
│   │   └── release
│   ├── nightly
│   └── release
├── gpu-vllm-omni
│   └── release
├── routing-sidecar
│   ├── nightly
│   └── release
├── tpu-vllm
│   ├── nightly
│   └── release
└── xpu-vllm
    ├── llm-d
    ├── nightly
    └── release
```

**NOTE**: This overlay view was generated with `tree -I 'kustomization.yaml' -I 'README.md'`.

### Why are there both `llm-d` and `vllm` images?

llm-d is moving towards using upstream images for both `sglang` and `vLLM`. As llm-d originally supported only vLLM, `llm-d` image variants were produced to account for any feature gaps in development of vLLM. Some of these feature gaps still exist today, for example the `ec-connector` work is still open in vLLM at the time of documenting this. As a stop-gap measure, the `llm-d` community will continue to host its own images as applicable, until they can be deprecated and safely migrate to upstream images.

## Usage

Include a component in your overlay's `kustomization.yaml`:

```yaml
components:
  - ../../../../../recipes/modelserver/components/images/gpu-vllm/release
```

The component replaces the `REPLACE_MODEL_SERVER_IMAGE` placeholder (or `REPLACE_ROUTING_SIDECAR_IMAGE` for the sidecar) with the default image.

## Overriding

If a guide requires a different image (e.g. a nightly build, a vendor fork, or a platform-specific variant), add an `images:` section in the same overlay. The override's `name:` must match the image **as baked by the component** (registry-qualified, e.g. `docker.io/vllm/vllm-openai`), not the `REPLACE_*` placeholder — an inline override that names the placeholder is silently ignored when a component also replaces it:

```yaml
components:
  - ../../../../../recipes/modelserver/components/images/gpu-vllm/release

# TODO(#<issue-number>): Remove override once <reason> is resolved.
images:
  - name: docker.io/vllm/vllm-openai
    newName: ghcr.io/example/custom-vllm
    newTag: nightly-20260601
```

> **Override policy:** Every inline `images:` override that diverges from the component default **must** include a `TODO` comment referencing a tracking issue for cleaning up the override. This ensures overrides are intentional, documented, and eventually removed.
