# Fast Model Actuation

## Overview

Fast Model Actuation (FMA) enables rapid model loading and switching for LLM inference on Kubernetes by exploiting vLLM sleep/wake and model swapping. In Kubernetes, GPUs are bound 1-to-1 to pods: a pod that requests a GPU holds it exclusively for its lifetime. FMA uses the following **dual pod** technique to circumvent this constraint:

- **Server-requesting Pods** reserve GPU resources via the Kubernetes scheduler but do not run inference themselves.
- **Launcher Pods** (server-providing) run vLLM without requesting GPUs. They gain access to GPUs via `CUDA_VISIBLE_DEVICES`, directed by the FMA controller to the specific GPU(s) reserved by the requesting pod.
- **FMA Controllers** manage the lifecycle: binding requesting pods to launchers, starting vLLM instances, and orchestrating sleep/wake.

Server-requesting pods are managed through standard Kubernetes controllers such as Deployments and autoscalers. The FMA controller watches these pods and translates scheduler decisions into actions on launcher pods and GPUs.

When a requesting pod is deleted, the controller puts the corresponding vLLM instance to sleep (model stays in GPU memory). Although the Kubernetes GPU allocation is released when the requesting pod exits, the launcher pod retains the CUDA context and keeps the model in GPU memory. The GPU remains dedicated to that launcher until it is explicitly unbound or the launcher pod is deleted. When a new requesting pod arrives and gets assigned to the same GPU, the controller wakes the sleeping instance, resuming in seconds instead of cold-starting from scratch.

FMA also supports instant model switching: if a new requesting pod references a different `InferenceServerConfig`, the FMA controller can direct the bound launcher to swap the loaded model in place, avoiding a full cold start.

> [!NOTE]
> Fast wake only occurs if the Kubernetes scheduler assigns the new requesting pod to the same node (and GPU) where the sleeping vLLM instance resides. In a cluster with a single GPU per node, if the scheduler picks the same node, the GPU is necessarily the same one. In a multi-node pool the scheduler may assign the pod to a different node.

## Configuration

| Parameter               | Value                                                         |
| ----------------------- | ------------------------------------------------------------- |
| Model                   | [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B)  |
| Requesting pod replicas | 2                                                             |
| Launcher count          | 1 (per matching node)                                         |
| GPUs per requesting pod | 1                                                             |
| Router                  | llm-d-router-standalone                                      |

## Prerequisites

This guide assumes you have a Kubernetes cluster with GPU nodes and the [llm-d router](../../guides/recipes/router/README.md) infrastructure available. If you are starting from an existing llm-d deployment, the Gateway API Inference Extension CRDs may already be installed and you can skip that step.

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- Checkout llm-d repo:

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

- Set the guide specific environment variables:

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=fast-model-actuation
export NAMESPACE=llm-d-fast-model-actuation
export FMA_VERSION=0.6.2
export FMA_CHART_INSTANCE_NAME=fma
export MODEL=Qwen/Qwen3-0.6B
export CURL_TEST_IMAGE=cfmanteiga/alpine-bash-curl-jq:latest
export BENCHMARK_REF=main
export HARNESS=nop
export WORKLOAD=nop.yaml
export GATEWAY_CLASS=epponly # options: epponly, gke, agentgateway, istio
```
<!-- guide:env.static end -->

- Source the common guide environment variables (`GAIE_VERSION`, `ROUTER_CHART_VERSION`, `ROUTER_STANDALONE_CHART`, …):

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

> [!NOTE]
> Some environment variables are common amongst guides. Inspect the file sourced above so the rest of the guide makes sense.

- Install the Gateway API Inference Extension CRDs:

<!-- guide:prerequisites.gaie start -->
```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

- Create a target namespace for the installation:

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

## Installation Instructions

At minimum, the user running these commands needs rights to create and manage CRDs, ClusterRoles, ClusterRoleBindings, and Helm releases across namespaces.

### 1. Apply FMA CRDs

<!-- guide:deploy.fma_crds start -->
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
<!-- guide:deploy.fma_crds end -->

### 2. Grant RBAC Permissions

The FMA controllers need cluster-level access to list nodes (for the launcher-populator) and namespace-level access for launcher pods to read their own pod spec. This applies the `fma-node-viewer` ClusterRole and the namespace-scoped `fma-launcher-pod-reader` Role/RoleBinding:

<!-- guide:deploy.rbac start -->
```bash
kubectl apply -k ${REPO_ROOT}/guides/${GUIDE_NAME}/rbac/
```
<!-- guide:deploy.rbac end -->

> [!NOTE]
> Only the `fma-node-viewer` **ClusterRole** is created here. The matching **ClusterRoleBinding** is created by the FMA Helm chart in the next step, via `--set global.nodeViewClusterRole=fma-node-viewer`.

### 3. Deploy FMA Controllers via Helm

<!-- guide:deploy.fma_controllers start -->
```bash
helm upgrade --install ${FMA_CHART_INSTANCE_NAME} \
  oci://ghcr.io/llm-d-incubation/llm-d-fast-model-actuation/charts/fma-controllers \
  --version ${FMA_VERSION} \
  --set global.nodeViewClusterRole=fma-node-viewer \
  -n ${NAMESPACE}

kubectl wait --for=condition=available --timeout=180s \
  deployment "${FMA_CHART_INSTANCE_NAME}-dual-pods-controller" -n ${NAMESPACE}
kubectl wait --for=condition=available --timeout=120s \
  deployment "${FMA_CHART_INSTANCE_NAME}-launcher-populator" -n ${NAMESPACE}
```
<!-- guide:deploy.fma_controllers end -->

### 4. Deploy the llm-d Router

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

### 5. Create FMA Resources

Apply the `InferenceServerConfig`, `LauncherConfig`, and `LauncherPopulationPolicy` that define the model to serve, the launcher pod template, and how many launchers to place:

<!-- guide:deploy.fma_resources start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/
```
<!-- guide:deploy.fma_resources end -->

> [!NOTE]
> This guide uses [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) which is publicly accessible and does not require a HuggingFace token. For gated models, you would need to mount the token via a different mechanism (FMA's ISC does not support `secretKeyRef`).

> [!NOTE]
> `HF_HOME` points at `/tmp/hf_cache`, which is ephemeral node storage. This keeps the guide self-contained, but every fresh launcher re-downloads the model weights and re-runs `torch.compile`. For production — or any repeated benchmarking — back `HF_HOME` with a shared, persistent volume (a PVC, e.g. `ReadWriteMany`) so model weights and compiled graphs persist across launcher pods and are not recomputed on each start.

> [!NOTE]
> The launcher pod does **not** request GPU resources from the Kubernetes scheduler or device plugin. Instead, the FMA controller sets `CUDA_VISIBLE_DEVICES` to point to the GPU reserved by the corresponding requesting pod, giving the launcher direct access to that GPU via the CUDA runtime. The `runtimeClassName: nvidia` is required on platforms (e.g., OpenShift) where GPU driver libraries are injected via the runtime class rather than the device plugin resource request.

> [!NOTE]
> `launcherCount` is **per matching node**. Setting `launcherCount: 1` creates one launcher pod on each node that has `nvidia.com/gpu.present: "true"`. Only launchers that get bound to a requesting pod will actually start a vLLM instance.

### 6. Create Requesting Pods

Create the server-requesting pods that reserve GPUs and trigger model loading. A `Deployment` is used here (rather than a bare `ReplicaSet`) so the requesting pods integrate cleanly with autoscalers such as the Workload Variant Autoscaler (WVA):

<!-- guide:deploy.requester start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/requester/

kubectl wait --for=condition=ready pod -l app=fma-requester -n ${NAMESPACE} --timeout=300s
```
<!-- guide:deploy.requester end -->

You should see:
- 2 requesting pods (`fma-requester-*`) in `Ready` state
- Launcher pods in `Running` state (one per GPU node in your cluster)
- FMA controller pods
- Router/EPP pods

## Verification

### 1. Get the IP of the Router

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

### 2. Send a Test Request

Open a temporary interactive shell inside the cluster and send a completion request (model-aware; set `MODEL` to the name you want to query):

<!-- guide:verify.tests.request start -->
```bash
kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\"}"'
```
<!-- guide:verify.tests.request end -->

### 3. Demonstrate Sleep/Wake

This section demonstrates FMA's core value: fast model actuation via sleep/wake. Scaling the requesting pods to `0` triggers the FMA controller to unbind them from their launchers and tell vLLM to sleep (the model stays in GPU memory but stops serving). Scaling back up re-binds them and wakes the sleeping instances — resuming in seconds rather than cold-starting:

<!-- guide:verify.tests.sleep_wake start -->
```bash
kubectl scale deployment fma-requester -n ${NAMESPACE} --replicas=0

kubectl scale deployment fma-requester -n ${NAMESPACE} --replicas=2

kubectl wait --for=condition=ready pod -l app=fma-requester -n ${NAMESPACE} --timeout=120s
```
<!-- guide:verify.tests.sleep_wake end -->

Re-run the inference request from step 2 to confirm the model is serving again.

> [!NOTE]
> Wake latency depends on the Kubernetes scheduler assigning the new requesting pod to the same node and GPU where the sleeping vLLM instance resides. If a different GPU is assigned, a new vLLM instance starts from scratch (cold start). Sleep/wake is most valuable in multi-GPU-per-node configurations where multiple models share the same GPU pool and can be swapped in and out without cold-starting.

## Benchmarking

This guide uses [`llmdbenchmark`](https://github.com/llm-d/llm-d-benchmark) — the supported standard CLI for llm-d performance benchmarking. It defaults to the `nop` harness (which stands the stack up and validates it end-to-end without driving a synthetic load); the richer, FMA-specific experimentation workflow lives in [`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark) itself.

> [!IMPORTANT]
> The Benchmarking section below contains only the **fast-model-actuation-specific commands** needed to drive the stack you just deployed — for everything else (and especially when something goes wrong), start at [`helpers/benchmark.md`](../../helpers/benchmark.md).

### 1. Install the CLI

<!-- guide:benchmark.setup start -->
```bash
curl -sSL https://raw.githubusercontent.com/llm-d/llm-d-benchmark/${BENCHMARK_REF}/install.sh | bash
cd llm-d-benchmark
source .venv/bin/activate
llmdbenchmark --version
```
<!-- guide:benchmark.setup end -->

> [!NOTE]
> Subsequent `llmdbenchmark` commands assume you are inside the `llm-d-benchmark` repo directory with the `venv` activated. If you open a new shell, re-run the commands above.

### 2. Resolve the endpoint of the stack you just deployed

<!-- guide:benchmark.endpoint.standalone start -->
```bash
export ENDPOINT_URL="http://$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')"
```
<!-- guide:benchmark.endpoint.standalone end -->

### 3. Run the benchmark

<!-- guide:benchmark.execute start -->
```bash
llmdbenchmark \
  --spec           guides/${GUIDE_NAME} \
  run \
  --endpoint-url   "${ENDPOINT_URL}" \
  --gateway-class  "${GATEWAY_CLASS}" \
  --model          "${MODEL}" \
  --namespace      "${NAMESPACE}" \
  --harness        "${HARNESS}" \
  --workload       "${WORKLOAD}" \
  --analyze
```
<!-- guide:benchmark.execute end -->

## Cleanup

To remove all deployed components:

<!-- guide:cleanup start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/requester/ --ignore-not-found=true

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/ --ignore-not-found=true

helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

helm uninstall ${FMA_CHART_INSTANCE_NAME} -n ${NAMESPACE}

kubectl delete -k ${REPO_ROOT}/guides/${GUIDE_NAME}/rbac/ --ignore-not-found=true

kubectl delete namespace ${NAMESPACE}

kubectl delete crd inferenceserverconfigs.fma.llm-d.ai launcherconfigs.fma.llm-d.ai launcherpopulationpolicies.fma.llm-d.ai
```
<!-- guide:cleanup end -->
