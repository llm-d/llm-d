# Centralized Image Components

This directory contains Kustomize Components that define the **default container images** for each accelerator/engine combination. Guides include the relevant component instead of hardcoding image versions inline, so a version bump requires editing one file.

## Available Components

```console
├── amd-sglang
│   ├── rocm720-mi30x
│   ├── rocm720-mi35x
│   ├── rocm724-mi30x
│   └── rocm724-mi35x
├── amd-vllm
│   ├── llm-d
│   ├── nightly
│   └── release
├── amd-vllm-omni
│   └── release
├── coordinator
│   ├── nightly
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
│   │   └── release
│   ├── nightly
│   └── release
├── gpu-vllm-omni
│   └── release
├── biren-vllm
│   └── release
├── metax-vllm
│   └── release
├── mthreads-sglang
│   └── release
├── mthreads-vllm
│   └── release
├── npu-vllm
│   └── release
├── routing-sidecar
│   ├── nightly
│   └── release
├── torch-tpu-vllm
│   ├── nightly
│   └── release
├── tpu-sglang
│   └── release
├── tpu-vllm
│   ├── nightly
│   └── release
├── xpu-sglang
│   ├── nightly
│   └── release
└── xpu-vllm
    ├── llm-d
    ├── nightly
    └── release
```

**NOTE**: This overlay view was generated with `tree -I 'kustomization.yaml' -I 'README.md'`.

### Why are there both `llm-d` and `vllm` images?

llm-d is moving towards using upstream images for both `sglang` and `vLLM`. As llm-d originally supported only vLLM, `llm-d` image variants were produced to account for any feature gaps in development of vLLM. Some of these feature gaps still exist today. As a stop-gap measure, the `llm-d` community will continue to host its own images as applicable, until they can be deprecated and safely migrate to upstream images.

## Usage

Include a component in your overlay's `kustomization.yaml`:

```yaml
components:
  - ../../../../../recipes/modelserver/components/images/gpu-vllm/release
```

The component replaces the `REPLACE_MODEL_SERVER_IMAGE` placeholder (`REPLACE_ROUTING_SIDECAR_IMAGE` for the sidecar, `REPLACE_COORDINATOR_IMAGE` for the coordinator) with the default image.

### Updating AWS EFA images

When bumping `gpu-vllm/release` or `gpu-sglang/release`, also check the corresponding AWS EFA pin:

| Shared release | AWS EFA pin | Available DLC tags |
| --- | --- | --- |
| [`gpu-vllm/release`](gpu-vllm/release/kustomization.yaml) | [`gpu-vllm/aws-efa/release`](gpu-vllm/aws-efa/release/kustomization.yaml) | [vLLM ECR gallery](https://gallery.ecr.aws/deep-learning-containers/vllm) |
| [`gpu-sglang/release`](gpu-sglang/release/kustomization.yaml) | [P/D SGLang AWS overlay](../../../../pd-disaggregation/modelserver/gpu/sglang/aws/kustomization.yaml) | [SGLang ECR gallery](https://gallery.ecr.aws/deep-learning-containers/sglang) |

Run `python scripts/check-aws-image-versions.py` from the repository root. The Guides CI runs the same check whenever guides or the checker change and annotates differences as warnings. It compares the major/minor/patch release, ignoring an upstream `.postN` suffix and AWS's platform suffix; floating, prerelease, or digest pins produce a notice to check compatibility manually.

AWS DLC releases can lag upstream. Check that the desired EFA-enabled image exists and validate it on the AWS path before updating the pin; do not substitute an upstream image just to match version numbers. Until a compatible build is available, the warning keeps the drift visible without blocking unrelated guide changes. See [#1944](https://github.com/llm-d/llm-d/issues/1944).

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
