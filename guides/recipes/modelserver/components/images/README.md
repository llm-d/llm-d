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
│   └── llm-d
├── amd-vllm-omni
├── cpu-vllm
├── gpu-sglang
├── gpu-trtllm
├── gpu-vllm
│   ├── aws-efa
│   │   └── llm-d
│   └── multimodal-disaggregation
├── gpu-vllm-omni
├── routing-sidecar
├── tpu-vllm
└── xpu-vllm
    └── llm-d
```

**NOTE**: This overlay view was generated with `tree -I 'kustomization.yaml' -I 'README.md'`.

### Why are there both `llm-d` and `vllm` images?

llm-d is moving towards using upstream images for both `sglang` and `vLLM`. As llm-d was orrigionally only supported vllm, `llm-d` image variants were produced to account for any feature gaps in development of vLLM. Some of these feature gaps still exist today, for example the `ec-connector` work is still open in vLLM at the time of documeting this. As a stop-gap measure, the `llm-d` community will continue to host its own images as applicable, until they can be deprecated and safely migrate to upstream images.


#### Known gap - NVSHMEM on RoCE networking

Upstream vLLM currently [pins NVSHMEM to `v3.4.5`](https://github.com/vllm-project/vllm/blob/ac70ce96e0b9f69dd834bd1b0cd2d2b4c4a9db46/requirements/test/cuda.txt#L648). This version of NVSHMEM requires [a patch](../../../../../patches/nvshmem_zero_ibv_ah_attr_v3.4.5-0.patch) guarding aginst where "static_rate must be a known value and is passed directly to the device". Any image runnign on ROCE should use `llm-d` image variants.

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
