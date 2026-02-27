#!/usr/bin/env bash
set -Eeuo pipefail

if ! command -v yq >/dev/null 2>&1; then
  echo "ERROR: yq v4 is required on PATH" >&2
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 path/to/values.yaml" >&2
  exit 1
fi
FILE="$1"

OLD_MODEL_ORG="deepseek-ai"
OLD_MODEL_NAME="DeepSeek-R1-0528"
NEW_MODEL_ORG="deepseek-ai"
NEW_MODEL_NAME="DeepSeek-V2-Lite"

OLD_MODEL="${OLD_MODEL_ORG}/${OLD_MODEL_NAME}"
OLD_MODEL_SED_ESCAPED="${OLD_MODEL_ORG}\/${OLD_MODEL_NAME}"

NEW_MODEL="${NEW_MODEL_ORG}/${NEW_MODEL_NAME}"
NEW_MODEL_SED_ESCAPED="${NEW_MODEL_ORG}\/${NEW_MODEL_NAME}"

patch() {
    # Transform ms-wide-ep/value{s.yaml to a scaled-down deployment
    # Need a small MoE model for this example: Qwen1.5-MoE-A2.7B-Chat.
    # This example will be a 2D - 1P (DP of 2), 1 GPU for prefill (because g6 series doesnt support nvlink or PCIe for p2p), and 1 GPU each for both Decodes

    yq e '.modelArtifacts.uri = "hf://'${NEW_MODEL}'"' -i ${FILE}
    yq e '.modelArtifacts.size = "30Gi"' -i ${FILE}
    yq e '.modelArtifacts.name = "'${NEW_MODEL}'"' -i ${FILE}
    yq e '.routing.modelName = "'${NEW_MODEL}'"' -i ${FILE}

    ## Decode specific changes
    ### Unbound the accelerator type from H200
    yq e 'del(.decode.acceleratorTypes)' -i ${FILE}

    ### Swap the model name in custom startup script
    decode_args=$(yq '.decode.containers[0].args[0]' ${FILE})
    decode_args_updated=$(echo "${decode_args}" | sed 's/'${OLD_MODEL_SED_ESCAPED}'/'${NEW_MODEL_SED_ESCAPED}'/g') # THIS NEEDS TO USE ARGS ABOVE
    decode_args_updated=$(echo "${decode_args_updated}" | sed '/--port/a\  --max-model-len 4096 \\')
    decode_args_updated=$(echo "${decode_args_updated}" | sed '/--port/a\  --enforce-eager \\')

    ### L40s Don't support DeepEP kernels, use allgather_reducescatter
    decode_args_updated=$(echo "${decode_args_updated}" | sed 's/--all2all-backend deepep_low_latency/--all2all-backend allgather_reducescatter/')

    export decode_args_updated

    yq e '.decode.containers[0].args[0] = strenv(decode_args_updated)' -i ${FILE}

    ### DP_SIZE 2, DP_SIZE_LOCAL 1
    yq e '.decode.parallelism.data = 2' -i ${FILE}
    yq e '(.decode.containers[0].env[] | select(.name == "DP_SIZE_LOCAL")).value = "1"' -i ${FILE}

    ### The example is set to work out of the box on the coreweave cluster loading model from node storage. Were going to use HF download instead
    yq 'del(.decode.containers[0].env[] | select(.name == "HF_HUB_CACHE"))' -i ${FILE}
    yq 'del(.decode.containers[0].env[] | select(.name == "HF_HUB_DISABLE_XET"))' -i ${FILE}

    ### Remove deep gemm
    yq e 'del(.decode.containers[0].env[] | select(.name == "VLLM_USE_DEEP_GEMM"))' -i ${FILE}

    ### Remove security context
    yq e 'del(.decode.containers[0].securityContext)' -i ${FILE}

    ### See above, example is a 2 by 2
    yq e '
    (.decode.containers[0].resources = {}) |
    (.decode.containers[0].resources.limits = {"nvidia.com/gpu": 1}) |
    (.decode.containers[0].resources.requests = {"nvidia.com/gpu": 1})
    ' -i ${FILE}

    ### Using model from HF rather than host storage, already discussed
    yq e ' (.decode.containers[0].mountModelVolume = true)' -i ${FILE}

    ### The example is set to work out of the box on the coreweave cluster loading model from node storage. Were going to use HF download instead.
    ### Thus we can remove the HF Cache volume mounts and let HF take care of a default cache path.
    yq e '
    .decode.containers[0].volumeMounts
        |= map(select(.name != "hf-cache"))
    |
    .decode.volumes
        |= map(select(.name != "hf-cache"))
    ' -i ${FILE}

    ## Prefill specific changes
    ### Unbound the accelerator type from H200
    yq e 'del(.prefill.acceleratorTypes)' -i ${FILE}

    ### Swap the model name in custom startup script
    prefill_args=$(yq '.prefill.containers[0].args[0]' ${FILE})

    prefill_args_updated=$(echo "${prefill_args}" | sed 's/'${OLD_MODEL_SED_ESCAPED}'/'${NEW_MODEL_SED_ESCAPED}'/g') # THIS NEEDS TO USE ARGS ABOVE
    prefill_args_updated=$(echo "${prefill_args_updated}" | sed '/--port/a\  --max-model-len 4096 \\')
    prefill_args_updated=$(echo "${prefill_args_updated}" | sed '/--port/a\  --enforce-eager \\')

    ### L40s Don't support DeepEP kernels, use allgather_reducescatter
    prefill_args_updated=$(echo "${prefill_args_updated}" | sed 's/--all2all-backend deepep_high_throughput/--all2all-backend allgather_reducescatter/')

    export prefill_args_updated

    yq e '.prefill.containers[0].args[0] = strenv(prefill_args_updated)' -i ${FILE}

    ### DP_SIZE 2, DP_SIZE_LOCAL 1
    yq e '.prefill.parallelism.data = 2' -i ${FILE}
    yq e '(.prefill.containers[0].env[] | select(.name == "DP_SIZE_LOCAL")).value = "1"' -i ${FILE}

    ### The example is set to work out of the box on the coreweave cluster loading model from node storage. Were going to use HF download instead
    yq 'del(.prefill.containers[0].env[] | select(.name == "HF_HUB_CACHE"))' -i ${FILE}
    yq 'del(.prefill.containers[0].env[] | select(.name == "HF_HUB_DISABLE_XET"))' -i ${FILE}

    ### Remove deep gemm
    yq e 'del(.prefill.containers[0].env[] | select(.name == "VLLM_USE_DEEP_GEMM"))' -i ${FILE}

    ### Remove security context
    yq e 'del(.prefill.containers[0].securityContext)' -i ${FILE}

    ### See above, example is a 2 by 2
    yq e '
    (.prefill.containers[0].resources = {}) |
    (.prefill.containers[0].resources.limits = {"nvidia.com/gpu": 1}) |
    (.prefill.containers[0].resources.requests = {"nvidia.com/gpu": 1})
    ' -i ${FILE}

    ### Using model from HF rather than host storage, already discussed
    yq e ' (.prefill.containers[0].mountModelVolume = true)' -i ${FILE}

    ### The example is set to work out of the box on the coreweave cluster loading model from node storage. Were going to use HF download instead.
    ### Thus we can remove the HF Cache volume mounts and let HF take care of a default cache path.
    yq e '
    .prefill.containers[0].volumeMounts
        |= map(select(.name != "hf-cache"))
    |
    .prefill.volumes
        |= map(select(.name != "hf-cache"))
    ' -i ${FILE}
}

patch
