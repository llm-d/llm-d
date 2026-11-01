# Deploying llm-d in Disconnected and Air-Gapped Clusters

This guide covers operational best practices and procedures for deploying llm-d in air-gapped, sovereign, or strictly disconnected environments where Kubernetes clusters have no outbound internet access.

---

## Overview

In connected environments, the [well-lit path guides](../../guides/README.md) retrieve dependencies directly from public endpoints:
- **Custom Resource Definitions (CRDs)** from GitHub release assets (`kubernetes-sigs/gateway-api`, `kubernetes-sigs/gateway-api-inference-extension`, and `kubernetes-sigs/lws`).
- **Container images** from public registries (`ghcr.io`, `docker.io`).
- **Helm charts** served over OCI from GitHub Packages (`ghcr.io/llm-d/charts`).
- **Model weights and tokenizer configs** downloaded on demand from Hugging Face Hub.

In an air-gapped environment, outbound access to these registries is blocked by firewall policies or physical disconnection. To run llm-d successfully, you must pre-stage four key pillars:

| Pillar | Default Behavior (Connected) | Disconnected / Air-Gapped Solution |
| :--- | :--- | :--- |
| **CRDs** | Fetched from GitHub releases via URL | Pre-downloaded and applied from local files |
| **Container Images** | Pulled from `ghcr.io` and `docker.io` | Mirrored to internal registry + `imagePullSecrets` |
| **Helm Charts** | Pulled from `oci://ghcr.io/llm-d/charts` | Mirrored to internal OCI registry or local chart archive |
| **Model Weights** | Downloaded from Hugging Face Hub | Pre-staged to PVC/storage + `HF_HUB_OFFLINE=1` |

---

## 1. Local CRD Installation

Standard guides run [`guides/recipes/gateway/install-gateway-crds.sh`](../../guides/recipes/gateway/install-gateway-crds.sh) or apply release assets directly from GitHub URLs. In an air-gapped cluster, download the CRDs on an internet-connected workstation and stage them inside your internal Git repository or bastion storage.

### Required CRDs

1. **Gateway API Standard CRDs** (required for `Gateway`, `HTTPRoute`):
   ```bash
   export GATEWAY_API_VERSION="v1.6.2" # or your target version
   curl -fsSL -o crds/gateway-api-standard.yaml \
     "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"
   ```

2. **Gateway API Inference Extension (GAIE) CRDs** (required for `InferencePool` and `InferenceModel`):
   ```bash
   export GAIE_VERSION="v1.0.0" # or your target version
   curl -fsSL -o crds/gaie-v1-manifests.yaml \
     "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"
   ```

3. **LeaderWorkerSet (LWS) CRDs** (required if deploying multi-host models or `DisaggregatedSet` topologies):
   ```bash
   export LWS_VERSION="1.0.0"
   curl -fsSL -o crds/lws-manifests.yaml \
     "https://github.com/kubernetes-sigs/lws/releases/download/v${LWS_VERSION}/manifests.yaml"
   ```

### Applying CRDs Offline

From within your air-gapped environment:

```bash
kubectl apply -f crds/gateway-api-standard.yaml
kubectl apply -f crds/gaie-v1-manifests.yaml
kubectl apply -f crds/lws-manifests.yaml
```

Verify that the CRDs are registered:

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
kubectl api-resources --api-group=inference.networking.k8s.io
```

---

## 2. Container Image Mirroring

All images referenced by the llm-d Router, model server backends, and verification tools must be mirrored to an internal container registry (e.g., Harbor, Artifactory, AWS ECR, or a private Docker Registry).

### Inventory of Core Images

| Component | Default Public Image | Purpose |
| :--- | :--- | :--- |
| **Endpoint Picker (EPP)** | `ghcr.io/llm-d/llm-d-router-endpoint-picker:v1.0.0` | Intelligent prefix-cache & load-aware scheduler |
| **Router Proxy (Envoy)** | `docker.io/envoyproxy/envoy:v1.31.2` | Data-plane proxy in standalone or sidecar mode |
| **Coordinator** | `ghcr.io/llm-d/llm-d-router-coordinator:v1.0.0` | Async and broker front door (if used) |
| **Model Server (vLLM)** | `docker.io/vllm/vllm-openai:v0.10.0` | Inference engine runtime (see accelerator components) |
| **Model Server (SGLang)** | `docker.io/lmsysorg/sglang:v0.5.22` | Inference engine runtime (if using SGLang) |
| **Verification Utility** | `docker.io/cfmanteiga/alpine-bash-curl-jq:latest` | Used by test scripts and guide verification |

> [!TIP]
> Check [`guides/recipes/modelserver/components/images/`](../../guides/recipes/modelserver/components/images/README.md) to locate the exact image tag used by your hardware target (NVIDIA GPU, AMD ROCm, Intel XPU, or Google TPU).

### Mirroring with Skopeo

On an external machine with access to both the public internet and your private registry:

```bash
INTERNAL_REGISTRY="registry.internal.example.com/llm-d"

# EPP
skopeo copy --all \
  docker://ghcr.io/llm-d/llm-d-router-endpoint-picker:v1.0.0 \
  docker://${INTERNAL_REGISTRY}/endpoint-picker:v1.0.0

# Envoy Sidecar
skopeo copy --all \
  docker://docker.io/envoyproxy/envoy:v1.31.2 \
  docker://${INTERNAL_REGISTRY}/envoy:v1.31.2

# vLLM Model Server
skopeo copy --all \
  docker://docker.io/vllm/vllm-openai:v0.10.0 \
  docker://${INTERNAL_REGISTRY}/vllm-openai:v0.10.0

# Verification utility
skopeo copy --all \
  docker://docker.io/cfmanteiga/alpine-bash-curl-jq:latest \
  docker://${INTERNAL_REGISTRY}/alpine-bash-curl-jq:latest
```

### Registry Authentication

If the private registry requires credentials, create a Kubernetes `docker-registry` Secret in the workload namespace:

```bash
kubectl create secret docker-registry internal-registry-secret \
  --docker-server=registry.internal.example.com \
  --docker-username="${REGISTRY_USER}" \
  --docker-password="${REGISTRY_PASSWORD}" \
  --namespace="${NAMESPACE}"
```

---

## 3. Internal OCI Helm Charts

The llm-d Router charts (`llm-d-router-standalone` and `llm-d-router-gateway`) are distributed as OCI packages.

### Mirroring the Helm Chart

1. **Pull the chart on a connected workstation:**
   ```bash
   export ROUTER_CHART_VERSION="v0.11.0"
   helm pull oci://ghcr.io/llm-d/charts/llm-d-router-standalone --version ${ROUTER_CHART_VERSION}
   ```

2. **Push the chart archive to your internal registry:**
   ```bash
   helm push llm-d-router-standalone-${ROUTER_CHART_VERSION}.tgz oci://registry.internal.example.com/charts
   ```

3. **In the air-gapped environment, point to the internal chart location:**
   ```bash
   export ROUTER_STANDALONE_CHART="oci://registry.internal.example.com/charts/llm-d-router-standalone"
   export ROUTER_CHART_VERSION="v0.11.0"
   ```

Alternatively, unpack the `.tgz` archive and point `ROUTER_STANDALONE_CHART` directly to the local directory path:
```bash
tar -xzf llm-d-router-standalone-${ROUTER_CHART_VERSION}.tgz -C ./charts/
export ROUTER_STANDALONE_CHART="./charts/llm-d-router-standalone"
```

---

## 4. Pre-staging Model Weights

In standard setups, model servers dynamically download checkpoints from the Hugging Face Hub during pod initialization. In an air-gapped cluster, this causes pods to fail with connection timeouts.

### Step 1: Pre-download Checkpoints Outside the Cluster

Download model repository files (tokenizer, config, and `safetensors` weights) using `huggingface-cli`:

```bash
export HF_HUB_ENABLE_HF_TRANSFER=1
huggingface-cli download \
  --repo-type model \
  --local-dir /staging/models/Qwen3-32B \
  Qwen/Qwen3-32B
```

Transfer the `/staging/models/Qwen3-32B` directory into your environment's storage solution (such as an NFS share, CephFS, or local storage volume).

### Step 2: Mount Weights via Persistent Volume Claim (PVC)

Use llm-d's [model-cache component](../../guides/recipes/modelserver/components/model-cache/kustomization.yaml) to attach a ReadWriteMany (RWX) volume to your model server deployment.

Create a PVC pointing to your pre-staged storage:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: model-pvc
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 500Gi
  storageClassName: rwx-nfs # your internal storage class
```

### Step 3: Configure Offline Engine Flags

Configure the model server container to run strictly offline by mounting the PVC and setting the offline environment variables:

```yaml
containers:
  - name: modelserver
    command: ["vllm", "serve"]
    args:
      # Point to the local directory path mounted from the PVC
      - "/model-weights/Qwen3-32B"
      - "--served-model-name=Qwen/Qwen3-32B"
      - "--host=0.0.0.0"
      - "--port=8000"
    env:
      # Enforce strict offline execution (prevents any outbound Hub queries)
      - name: HF_HUB_OFFLINE
        value: "1"
      - name: TRANSFORMERS_OFFLINE
        value: "1"
      - name: HF_HOME
        value: "/model-weights"
    volumeMounts:
      - name: model-cache
        mountPath: /model-weights
        readOnly: true
volumes:
  - name: model-cache
    persistentVolumeClaim:
      claimName: model-pvc
```

---

## 5. Deploying the Stack Offline

Here is a complete, working example deploying [`optimized-baseline`](../../guides/optimized-baseline/README.md) in an air-gapped cluster.

### Step 1: Configure Environment Variables

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh

# Override external image and chart coordinates
export ROUTER_STANDALONE_CHART="oci://registry.internal.example.com/charts/llm-d-router-standalone"
export ROUTER_CHART_VERSION="v0.11.0"
export CURL_TEST_IMAGE="registry.internal.example.com/llm-d/alpine-bash-curl-jq:latest"
export NAMESPACE="llm-d-offline"

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
```

### Step 2: Router Values Override (`airgapped-values.yaml`)

Create an override values file that directs EPP and Envoy to the internal registry and configures image pull secrets:

```yaml
# airgapped-values.yaml
router:
  imagePullSecrets:
    - name: internal-registry-secret

  epp:
    image:
      repository: registry.internal.example.com/llm-d/endpoint-picker
      tag: v1.0.0

  proxy:
    image:
      repository: registry.internal.example.com/llm-d/envoy
      tag: v1.31.2
```

Install the router using the internal chart and overrides:

```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f airgapped-values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

### Step 3: Model Server Kustomize Overlay

In your model server overlay `kustomization.yaml`, override the container image to point to your mirrored image:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ../base

images:
  - name: REPLACE_MODEL_SERVER_IMAGE
    newName: registry.internal.example.com/llm-d/vllm-openai
    newTag: v0.10.0

patches:
  - target:
      kind: Deployment
      name: decode
    patch: |-
      - op: add
        path: /spec/template/spec/imagePullSecrets
        value:
          - name: internal-registry-secret
```

Apply the model server overlay:

```bash
kubectl apply -k ${MODEL_SERVER_PATH} -n ${NAMESPACE}
```

---

## 6. Verification and Troubleshooting

### Verifying Zero Egress

1. **Verify Pod Status:**
   ```bash
   kubectl get pods -n ${NAMESPACE}
   ```
   All pods (`decode-*`, `epp-*`) should reach `Running` and `Ready` states without encountering `ImagePullBackOff` or `ErrImagePull`.

2. **Verify Offline Model Loading:**
   Inspect the model server startup logs:
   ```bash
   kubectl logs -n ${NAMESPACE} -l app=decode -c modelserver --tail=50
   ```
   Confirm that weights were read directly from disk and no HTTP connection attempts to `huggingface.co` were logged.

3. **Run an In-Cluster Smoke Test:**
   Send a test completion request using the local test image:
   ```bash
   kubectl run curl-test -n ${NAMESPACE} --rm -i --restart=Never \
     --image="${CURL_TEST_IMAGE}" -- \
     curl -s http://${GUIDE_NAME}-epp:80/v1/models
   ```
   You should receive a valid JSON response containing the served model identifier.

### Common Offline Failure Modes

| Error / Symptom | Root Cause | Resolution |
| :--- | :--- | :--- |
| `ErrImagePull` or `ImagePullBackOff` | Pod cannot authenticate to the private registry or secret is missing. | Verify secret exists in `${NAMESPACE}` and matches `imagePullSecrets` in Helm/kustomize manifests. |
| `urllib.error.URLError: <urlopen error ... Temporary failure in name resolution>` | Model server trying to reach `huggingface.co`. | Ensure `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are exported in the container environment, and point `--model-path` to the local directory. |
| `the server could not find the requested resource (get inferencepools.inference.networking.k8s.io)` | CRDs were not installed prior to deploying manifests. | Apply `gateway-api-standard.yaml` and `gaie-v1-manifests.yaml` locally. |
| `503 Service Unavailable` on request | Router cannot connect to model server or backend probe failed. | Check model server readiness probe (`/health`); verify model finished loading weights from local storage. |
