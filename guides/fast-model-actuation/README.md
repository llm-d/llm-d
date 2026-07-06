# Fast Model Actuation

## Overview

Fast Model Actuation (FMA) enables rapid model loading and switching for LLM inference on Kubernetes by exploiting vLLM sleep/wake and model swapping. In Kubernetes, GPUs are bound 1-to-1 to pods: a pod that requests a GPU holds it exclusively for its lifetime. FMA uses the following **dual pod** technique to circumvent this constraint:

- **Server-requesting Pods** reserve GPU resources via the Kubernetes scheduler but do not run inference themselves.
- **Launcher Pods** (server-providing) run vLLM without requesting GPUs. They gain access to GPUs via `CUDA_VISIBLE_DEVICES`, directed by the FMA controller to the specific GPU(s) reserved by the requesting pod.
- **FMA Controllers** manage the lifecycle: binding requesting pods to launchers, starting vLLM instances, and orchestrating sleep/wake.

Server-requesting pods are managed through standard Kubernetes controllers such as ReplicaSets and autoscalers. The FMA controller watches these pods and translates scheduler decisions into actions on launcher pods and GPUs.

When a requesting pod is deleted, the controller puts the corresponding vLLM instance to sleep (model stays in GPU memory). Although the Kubernetes GPU allocation is released when the requesting pod exits, the launcher pod retains the CUDA context and keeps the model in GPU memory. The GPU remains dedicated to that launcher until it is explicitly unbound or the launcher pod is deleted. When a new requesting pod arrives and gets assigned to the same GPU, the controller wakes the sleeping instance, resuming in seconds instead of cold-starting from scratch.

FMA also supports instant model switching: if a new requesting pod references a different `InferenceServerConfig`, the FMA controller can direct the bound launcher to swap the loaded model in place, avoiding a full cold start.

> [!NOTE]
> Fast wake only occurs if the Kubernetes scheduler assigns the new requesting pod to the same node (and GPU) where the sleeping vLLM instance resides. In a cluster with a single GPU per node, if the scheduler picks the same node, the GPU is necessarily the same one. In a multi-node pool the scheduler may assign the pod to a different node.

## Default Configuration

| Parameter               | Value                                                         |
| ----------------------- | ------------------------------------------------------------- |
| Model                   | [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B)  |
| Requesting pod replicas | 2                                                             |
| Launcher count          | 1 (per matching node)                                         |
| GPUs per requesting pod | 1                                                             |
| Router                  | llm-d-router-standalone-dev                                   |

## Prerequisites

This guide assumes you have a Kubernetes cluster with GPU nodes and the [llm-d router](../../guides/recipes/router/README.md) infrastructure available. If you are starting from an existing llm-d deployment, the Gateway API Inference Extension CRDs may already be installed and you can skip that step.

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.
- Checkout llm-d repo:

  ```bash
    export branch="main" # branch, tag, or commit hash
    git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${branch}
  ```

- Set the following environment variables:

  ```bash
    export GAIE_VERSION=v1.5.0
    export ROUTER_CHART_VERSION=v0
    export GUIDE_NAME="fast-model-actuation"
    export NAMESPACE=llm-d-fast-model-actuation
    export FMA_VERSION="0.6.0-alpha.13"
    export FMA_CHART_INSTANCE_NAME="fma"
    export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
  ```

- Install the Gateway API Inference Extension CRDs:

  ```bash
    kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
  ```

- Create a target namespace for the installation:

  ```bash
    kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  ```

## Installation Instructions

At minimum, the user running these commands needs rights to create and manage CRDs, ClusterRoles, ClusterRoleBindings, and Helm releases across namespaces.

### 1. Apply FMA CRDs

```bash
FMA_CRD_BASE="https://raw.githubusercontent.com/llm-d-incubation/llm-d-fast-model-actuation/v${FMA_VERSION}/config/crd"
kubectl apply --server-side \
    -f ${FMA_CRD_BASE}/fma.llm-d.ai_inferenceserverconfigs.yaml \
    -f ${FMA_CRD_BASE}/fma.llm-d.ai_launcherconfigs.yaml \
    -f ${FMA_CRD_BASE}/fma.llm-d.ai_launcherpopulationpolicies.yaml
kubectl wait --for=condition=Established crd/inferenceserverconfigs.fma.llm-d.ai --timeout=120s
kubectl wait --for=condition=Established crd/launcherconfigs.fma.llm-d.ai --timeout=120s
kubectl wait --for=condition=Established crd/launcherpopulationpolicies.fma.llm-d.ai --timeout=120s
```

### 2. Deploy FMA Controllers via Helm

```bash
helm upgrade --install ${FMA_CHART_INSTANCE_NAME} \
    oci://ghcr.io/llm-d-incubation/llm-d-fast-model-actuation/charts/fma-controllers \
    --version ${FMA_VERSION} \
    -n ${NAMESPACE}
```

### 3. Grant RBAC Permissions

The FMA controllers need cluster-level access to list nodes (for the launcher-populator) and namespace-level access for launcher pods to read their own pod spec:

```bash
kubectl apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: fma-node-viewer
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: fma-node-viewer
subjects:
  - kind: ServiceAccount
    name: ${FMA_CHART_INSTANCE_NAME}-fma-controllers
    namespace: ${NAMESPACE}
roleRef:
  kind: ClusterRole
  name: fma-node-viewer
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: fma-launcher-pod-reader
  namespace: ${NAMESPACE}
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: fma-launcher-pod-reader
  namespace: ${NAMESPACE}
subjects:
  - kind: ServiceAccount
    name: default
    namespace: ${NAMESPACE}
roleRef:
  kind: Role
  name: fma-launcher-pod-reader
  apiGroup: rbac.authorization.k8s.io
EOF
```

### 4. Wait for Controllers to Be Ready

```bash
kubectl wait --for=condition=available --timeout=180s \
    deployment "${FMA_CHART_INSTANCE_NAME}-dual-pods-controller" -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=120s \
    deployment "${FMA_CHART_INSTANCE_NAME}-launcher-populator" -n ${NAMESPACE}
```

### 5. Deploy the llm-d Router

```bash
helm install ${GUIDE_NAME} \
    oci://ghcr.io/llm-d/charts/llm-d-router-standalone-dev \
    -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
    -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
    -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

### 6. Create FMA Resources

Apply the following manifests to create the FMA resource stack:

#### InferenceServerConfig

Defines the model to serve and its vLLM configuration:

```bash
kubectl apply -n ${NAMESPACE} -f - <<EOF
apiVersion: fma.llm-d.ai/v1alpha1
kind: InferenceServerConfig
metadata:
  name: fma-isc
spec:
  modelServerConfig:
    port: 8000
    options: "--model Qwen/Qwen3-0.6B --gpu-memory-utilization 0.9"
    env_vars:
      VLLM_LOGGING_LEVEL: "INFO"
      HF_HOME: "/tmp/hf_cache"
      TRANSFORMERS_CACHE: "/tmp/hf_cache"
    labels:
      llm-d.ai/guide: "fast-model-actuation"
      llm-d.ai/model: "qwen3-0.6b"
  launcherConfigName: "fma-launcher"
EOF
```

> [!NOTE]
> This guide uses [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) which is publicly accessible and does not require a HuggingFace token. For gated models, you would need to mount the token via a different mechanism (FMA's ISC does not support `secretKeyRef`).

#### LauncherConfig

Defines the template for providing pods that will run vLLM:

```bash
kubectl apply -n ${NAMESPACE} -f - <<EOF
apiVersion: fma.llm-d.ai/v1alpha1
kind: LauncherConfig
metadata:
  name: fma-launcher
spec:
  maxInstances: 2
  podTemplate:
    spec:
      runtimeClassName: nvidia
      containers:
        - name: inference-server
          image: ghcr.io/llm-d-incubation/llm-d-fast-model-actuation/launcher:v${FMA_VERSION}
          command: ["/bin/bash", "-c"]
          args:
            - |
              python3 launcher.py \
              --host 0.0.0.0 \
              --port 8001 \
              --log-level info
          env:
            - name: HOME
              value: "/tmp"
          ports:
            - containerPort: 8001
          resources:
            requests:
              cpu: "4"
              memory: "8Gi"
            limits:
              cpu: "8"
              memory: "16Gi"
EOF
```

> [!NOTE]
> The launcher pod does **not** request GPU resources from the Kubernetes scheduler or device plugin. Instead, the FMA controller sets `CUDA_VISIBLE_DEVICES` to point to the GPU reserved by the corresponding requesting pod, giving the launcher direct access to that GPU via the CUDA runtime. The `runtimeClassName: nvidia` is required on platforms (e.g., OpenShift) where GPU driver libraries are injected via the runtime class rather than the device plugin resource request.

#### LauncherPopulationPolicy

Defines how many launcher pods to create and where:

```bash
kubectl apply -n ${NAMESPACE} -f - <<EOF
apiVersion: fma.llm-d.ai/v1alpha1
kind: LauncherPopulationPolicy
metadata:
  name: fma-lpp
spec:
  enhancedNodeSelector:
    labelSelector:
      matchLabels:
        nvidia.com/gpu.present: "true"
  countForLauncher:
    - launcherConfigName: "fma-launcher"
      launcherCount: 1
EOF
```

> [!NOTE]
> `launcherCount` is **per matching node**. Setting `launcherCount: 1` creates one launcher pod on each node that has `nvidia.com/gpu.present: "true"`. Only launchers that get bound to a requesting pod will actually start a vLLM instance.

#### Requesting Pods

Create the server-requesting pods that reserve GPUs and trigger model loading:

```bash
kubectl apply -n ${NAMESPACE} -f - <<EOF
apiVersion: apps/v1
kind: ReplicaSet
metadata:
  name: fma-requester
spec:
  replicas: 2
  selector:
    matchLabels:
      app: fma-requester
  template:
    metadata:
      labels:
        app: fma-requester
      annotations:
        dual-pods.llm-d.ai/inference-server-config: "fma-isc"
        dual-pods.llm-d.ai/admin-port: "8081"
    spec:
      containers:
        - name: requester
          image: ghcr.io/llm-d-incubation/llm-d-fast-model-actuation/requester:v${FMA_VERSION}
          ports:
            - containerPort: 8080
              name: probes
            - containerPort: 8081
              name: admin
          readinessProbe:
            httpGet:
              path: /ready
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 5
          resources:
            requests:
              nvidia.com/gpu: "1"
              cpu: "100m"
              memory: "128Mi"
            limits:
              nvidia.com/gpu: "1"
              cpu: "200m"
              memory: "256Mi"
EOF
```

### 7. Wait for Pods to Be Ready

Wait for the FMA controllers to bind requesting pods to launcher pods and start vLLM:

```bash
kubectl wait --for=condition=ready pod -l app=fma-requester -n ${NAMESPACE} --timeout=300s
```

Verify the full stack is running:

```bash
kubectl get pods -n ${NAMESPACE}
```

You should see:
- 2 requesting pods (`fma-requester-*`) in `Ready` state
- Launcher pods in `Running` state (one per GPU node in your cluster)
- FMA controller pods
- Router/EPP pods

## Verification

### 1. Get the IP of the Router

```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```

### 2. Send a Test Request

**Open a temporary interactive shell inside the cluster:**

```bash
kubectl run curl-debug --rm -it \
    --image=cfmanteiga/alpine-bash-curl-jq \
    --namespace="$NAMESPACE" \
    --env="IP=$IP" \
    -- /bin/bash
```

**Send a completion request:**

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "Qwen/Qwen3-0.6B",
        "prompt": "How are you today?"
    }' | jq
```

### 3. Demonstrate Sleep/Wake

This section demonstrates FMA's core value: fast model actuation via sleep/wake.

**Verify pods are awake and serving:**

```bash
kubectl get pods -n ${NAMESPACE} -l app=fma-requester
```

**Trigger sleep by scaling down the requesting pods:**

```bash
kubectl scale replicaset fma-requester -n ${NAMESPACE} --replicas=0
```

The FMA controller detects the deletion of requesting pods, unbinds them from their launcher pods, and tells vLLM to sleep. The model remains in GPU memory but stops serving.

**Trigger wake by scaling back up:**

```bash
kubectl scale replicaset fma-requester -n ${NAMESPACE} --replicas=2
```

The FMA controller binds the new requesting pods to launcher pods. If the scheduler assigns them to the same GPUs, the controller wakes the sleeping vLLM instances — resuming in seconds rather than minutes.

**Wait for ready and send another request:**

```bash
kubectl wait --for=condition=ready pod -l app=fma-requester -n ${NAMESPACE} --timeout=120s
```

Re-run the inference request from step 2 to confirm the model is serving again.

> [!NOTE]
> Wake latency depends on the Kubernetes scheduler assigning the new requesting pod to the same node and GPU where the sleeping vLLM instance resides. If a different GPU is assigned, a new vLLM instance starts from scratch (cold start). Sleep/wake is most valuable in multi-GPU-per-node configurations where multiple models share the same GPU pool and can be swapped in and out without cold-starting.

## Cleanup

To remove all deployed components:

```bash
kubectl delete replicaset fma-requester -n ${NAMESPACE}
kubectl delete inferenceserverconfig fma-isc -n ${NAMESPACE}
kubectl delete launcherconfig fma-launcher -n ${NAMESPACE}
kubectl delete launcherpopulationpolicy fma-lpp -n ${NAMESPACE}
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
helm uninstall ${FMA_CHART_INSTANCE_NAME} -n ${NAMESPACE}
kubectl delete clusterrolebinding fma-node-viewer
kubectl delete clusterrole fma-node-viewer
kubectl delete namespace ${NAMESPACE}
kubectl delete crd inferenceserverconfigs.fma.llm-d.ai launcherconfigs.fma.llm-d.ai launcherpopulationpolicies.fma.llm-d.ai
```
