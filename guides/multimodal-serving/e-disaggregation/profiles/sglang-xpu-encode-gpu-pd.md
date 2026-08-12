# SGLang XPU Encode + GPU PD Profile

This profile extends the [common E-disaggregation guide](../README.md) with an SGLang E/PD deployment that runs Encode workers on Intel XPUs and the combined Prefill/Decode worker on a GPU.

## Overview

The reference deployment serves `moonshotai/Kimi-VL-A3B-Instruct` with four Intel XPU Encode workers and one NVIDIA GPU PD worker. SGLang owns encoder dispatch and embedding transfer, while the llm-d Router selects the PD endpoint.

This differs from the vLLM implementation:

* The llm-d Router selects only PD workers.
* The SGLang PD worker assigns media items through static `--encoder-urls`.
* Encode workers return embeddings through SGLang `zmq_to_scheduler`.
* The E-to-PD path does not use the llm-d disaggregation sidecar, vLLM EC Connector, or NIXL.

Text-only requests skip the Encode workers.

## Reference Configuration

| Setting | Value |
| --- | --- |
| Model | `moonshotai/Kimi-VL-A3B-Instruct` |
| Topology | 4E1PD |
| Encode replicas | 4 |
| Encode accelerator | 1 Intel XPU per replica, allocated with DRA |
| Encode attention backend | `xpu_attn` |
| PD replicas | 1 |
| PD accelerator | 1 NVIDIA GPU |
| Validated hardware | 4 Intel B60 XPUs + 1 NVIDIA H200 GPU |
| Encoder discovery | Static StatefulSet DNS names in `--encoder-urls` |
| Embedding transfer | SGLang `zmq_to_scheduler` |
| llm-d Router ownership | PD selection only |

> [!NOTE]
> The reference configuration was evaluated with SGLang's random multimodal dataset. Because this workload does not intentionally reuse prefixes, the PD worker uses `--disable-radix-cache` to avoid cache effects in the comparison. Remove this option for workloads that benefit from prefix reuse.

The Encode StatefulSet and headless Service provide these stable endpoints:

```text
encode-0.encode:8000
encode-1.encode:8000
encode-2.encode:8000
encode-3.encode:8000
```

The portable manifests do not select particular hosts, GPU UUIDs, PCI addresses, or card indices. Kubernetes selects the devices from available cluster resources.

## Architecture

```text
Client -> Proxy -> llm-d Router -> GPU PD worker
                                      |
                                      +-- HTTP encode work --> Intel XPU Encode workers
                                      |                         |
                                      +<---- ZMQ embeddings -----+
                                      |
                                      +-- prefill + decode --> Response
```

1. The llm-d Router selects a ready PD endpoint.
2. The SGLang language-only PD worker parses the multimodal request and divides media items across its static `--encoder-urls`.
3. The XPU workers process assigned items with `xpu_attn`.
4. Each XPU worker sends embeddings to the PD scheduler through `zmq_to_scheduler`.
5. The GPU PD worker performs language-model prefill and decode.

## Manifest Composition

The profile separates reusable encoder resources from topology-specific PD configuration:

```text
modelserver/hetero/sglang/
|-- common/
|   |-- kustomization.yaml
|   |-- encode-service.yaml
|   `-- xpu-encoder/
|       |-- kustomization.yaml
|       |-- encode-statefulset.yaml
|       `-- resource-claim-template.yaml
`-- e-pd/
    `-- xpu-encode-gpu-pd/
        |-- kustomization.yaml
        `-- patch-pd.yaml
```

Guide, model, and engine labels are shared across both worker types. Accelerator and role labels remain workload-specific:

| Workload | Accelerator variant | Accelerator vendor | Engine | Role |
| --- | --- | --- | --- | --- |
| Encode | `xpu` | `intel` | `sglang` | `encode` |
| PD | `gpu` | `nvidia` | `sglang` | `decode` |

The E/PD profile composes the shared XPU encoder with the existing default single-host model-server recipe. A future SGLang E/P/D profile can reuse the same encoder resources and compose them with the existing SGLang P/D recipe; only the Prefill worker needs the encoder URLs and transfer configuration.

## Select This Profile

Complete the repository checkout and common environment setup in [Prerequisites](../README.md#prerequisites), then set:

```bash
export RELEASE_NAME="sglang-xpu-encode-gpu-pd"
export NAMESPACE="llm-d-sglang-xpu-gpu-e-pd"
export MODEL_NAME="moonshotai/Kimi-VL-A3B-Instruct"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_PATH}/router/sglang/e-pd-disaggregation.values.yaml"
export MODEL_SERVER_PATH="${REPO_ROOT}/guides/${GUIDE_PATH}/modelserver/hetero/sglang/e-pd/xpu-encode-gpu-pd"
export MONITORING_COMPONENT="monitoring"
export ROUTER_INFERENCE_POOL_CREATE="false"
```

After setting these variables, finish the [common prerequisites](../README.md#complete-common-prerequisites), then use the common [installation instructions](../README.md#installation-instructions), [verification workflow](../README.md#verification), and [cleanup command](../README.md#cleanup). In standalone mode, EPP discovers PD pods directly through `router.modelServers.matchLabels`, so this profile does not create an `InferencePool`. Gateway mode still requires an `InferencePool` and the corresponding Kubernetes permissions.

## Cluster Requirements

The cluster must provide:

* at least one schedulable NVIDIA GPU exposed as `nvidia.com/gpu`;
* at least four schedulable Intel XPUs;
* Kubernetes Dynamic Resource Allocation using the `resource.k8s.io/v1` API;
* the [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes) and a `gpu.intel.com` DeviceClass;
* pod-to-pod connectivity from the PD worker to Encode port 8000 and from the Encode workers to dynamic ZMQ receive ports on the PD pod;
* outbound access to Hugging Face Hub from the model-server pods unless the model is available from a local cache; and
* outbound access from Encode workers to remote media URLs used in requests, unless clients use data URLs or cluster-local media URLs.

Verify the accelerator resources before installation:

```bash
if kubectl auth can-i get deviceclasses.resource.k8s.io >/dev/null 2>&1; then
  kubectl get deviceclass gpu.intel.com
else
  echo "Skipping DeviceClass inspection: the current identity cannot read cluster-scoped DeviceClass resources."
fi

if kubectl auth can-i list nodes >/dev/null 2>&1; then
  kubectl get nodes \
    -o custom-columns='NAME:.metadata.name,NVIDIA_GPUS:.status.allocatable.nvidia\.com/gpu'
else
  echo "Skipping node inspection: the current identity cannot list cluster-scoped Node resources."
fi
```

These checks are informational. Namespace-scoped users can deploy the profile without permission to read cluster-scoped `DeviceClass` or Node objects; successful DRA allocation is confirmed through the namespaced `ResourceClaim` objects after deployment.

## Image Provenance

The profile pins both images by digest:

| Role | Image |
| --- | --- |
| GPU PD | `docker.io/lmsysorg/sglang:v0.5.15.post1-cu130@sha256:00c53fe4c31bf22d7b37537f28bbdfd924c02de13cdfb4bff7378c9c34d75ab2` |
| Intel XPU Encode | `ghcr.io/xiaojun-zhang/llm-d-xpu-sglang:sglang-heterogeneous-e-pd@sha256:56ed840fe2890671a894fda14da1ead719ce18e5a94d3095a58ba5b54c41e55d` |

The GPU image identifies SGLang source revision `0b3bb0cbe31873994c9f989fddfe2f87ca839fdd`. The XPU image is built from SGLang source revision `1af01674938f68266d5f8a0e5635ea1434af7801`, including the Kimi-VL 2-D image-grid fix, and `sgl-kernel-xpu` revision `a246742797279015f51d135063ed00f879496896`.

> [!IMPORTANT]
> The fork-owned XPU package is private at the time this profile was authored. The image must be made public or rebuilt and published under `ghcr.io/llm-d` before publication. The source build is defined in `.github/workflows/build-image.yaml` with the `sglang-xpu` platform.
>
> The SGLang E/PD runtime path was validated end to end with four Intel B60 Encode workers and one NVIDIA H200 PD worker. The site-local overlay supplied placement, device-allocation, model-cache, and registry-access settings; the SGLang E/PD arguments and pinned images matched the portable profile.

## Profile Verification

After following the common installation workflow, wait for the workers:

```bash
kubectl rollout status "deployment/${RELEASE_NAME}-epp" \
  -n "${NAMESPACE}" --timeout=10m
kubectl rollout status statefulset/encode \
  -n "${NAMESPACE}" --timeout=45m
kubectl rollout status deployment/decode \
  -n "${NAMESPACE}" --timeout=45m
kubectl get pods,resourceclaims -n "${NAMESPACE}"
```

Confirm that EPP discovered the PD pod:

```bash
kubectl logs "deployment/${RELEASE_NAME}-epp" \
  -n "${NAMESPACE}" -c epp \
  | grep -E '"msg":"Pod added".*"name":"decode-'
```

The Router discovers only the PD pod. The PD pod waits for all four Encode endpoints before starting SGLang.

From the `curl-debug` shell opened by the common verification workflow, send a request containing four embedded color images:

> [!NOTE]
> This request uses data URLs and does not require outbound connectivity from the Encode workers. For remote URL inputs, the SGLang Encode workers fetch the media and must be able to reach those URLs.

```bash
RED='iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAS0lEQVR42u3PQQkAAAgAsetfWiP4FgYrsKZeS0BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEDgsqnc8OJg6Ln3AAAAAElFTkSuQmCC'
GREEN='iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAS0lEQVR42u3PQQkAAAgAsetfWiP4FgYrsJp+ExAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBC4LLjs8OJxKlMxAAAAAElFTkSuQmCC'
BLUE='iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAS0lEQVR42u3PQQkAAAgAsetfWiP4FgYrsGqeExAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBA4LMf88OL0EKXAAAAAAElFTkSuQmCC'
YELLOW='iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAATElEQVR42u3PMQkAAAwDsPo33UnoPQjEQNLmtQgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgILAcyl+HSEp61MQAAAABJRU5ErkJggg=='

cat >/tmp/epd-request.json <<EOF
{
  "model": "${MODEL_NAME}",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,${RED}"
          }
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,${GREEN}"
          }
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,${BLUE}"
          }
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,${YELLOW}"
          }
        },
        {
          "type": "text",
          "text": "Identify the color of each image in order."
        }
      ]
    }
  ],
  "max_tokens": 128,
  "temperature": 0
}
EOF

curl -sS -f -X POST "http://${IP}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/epd-request.json | jq .
```

After leaving the debug shell, confirm that the PD worker dispatched all four items:

```bash
kubectl logs deployment/decode -n "${NAMESPACE}" -c modelserver \
  --since=10m \
  | grep 'Dispatching 4 mm items to 4 encoder'
```

Confirm that all four Encode workers processed one image and returned HTTP 200:

```bash
for ordinal in 0 1 2 3; do
  echo "encode-${ordinal}"
  kubectl logs "encode-${ordinal}" -n "${NAMESPACE}" -c modelserver \
    --since=10m \
    | grep -E 'Dispatching batch of 1 IMAGE requests|POST /encode HTTP/1.1" 200 OK'
done
```

## When to Use This Profile

This profile is intended for workloads where:

* requests contain several or high-resolution images;
* multimodal encoding materially contributes to time to first token;
* independent Encode scaling can remove a bottleneck; or
* Intel XPUs provide a useful cost or capacity tier for vision encoding.

Performance gains are model- and workload-dependent. Compare this deployment with an aggregated baseline using a representative workload before production use.

## Limitations

* This is a fixed 4E1PD example for `moonshotai/Kimi-VL-A3B-Instruct`; it does not establish support or a performance benefit for other models or workloads.
* Encoder membership is static. Changing the StatefulSet replica count also requires updating the PD worker's `--encoder-urls`.
* The PD worker waits for all Encode workers at startup, but the llm-d Router does not track Encode health or load.
* Remote media URLs are fetched by Encode workers. In the validated SGLang revision, an Encode-side download failure caused the PD process to restart; use reachable URLs or data URLs and inspect `kubectl logs deployment/decode -n "${NAMESPACE}" -c modelserver --previous` after media-fetch failures.
* The E-to-PD path uses TCP-based HTTP and ZMQ communication. It does not configure RDMA or NIXL.
* The Encode workers expose metrics, but this profile does not include an Encode-specific PodMonitor.
* Hardware sizing, network policy, persistent model caching, autoscaling, and production availability policy remain deployment-specific.

## References

* [llm-d Router architecture](https://github.com/llm-d/llm-d-router/blob/main/docs/architecture.md)
* [SGLang EPD disaggregation](https://github.com/sgl-project/sglang/blob/main/docs/advanced_features/epd_disaggregation.md)
* [Intel Resource Drivers for Kubernetes](https://github.com/intel/intel-resource-drivers-for-kubernetes)
