# Fast Model Actuation

[![E2E (OCP GPU)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-fast-model-actuation-ibm-acc-gpu-vllm-x.yaml/badge.svg)](https://github.com/llm-d/llm-d/actions/workflows/consolidate-status-fast-model-actuation-ibm-acc-gpu-vllm-x.yaml)

## Overview

Fast Model Actuation (FMA) addresses vLLM startup time using two technologies. One is vLLM sleep and wake, which can entirely avoid model loading and CUDA graph compilation in applicable scenarios. The other is running multiple vLLM processes as children of a launcher process that has already done the Python module loading. FMA can manage multiple vLLM instances bound to one GPU, with one instance awake at a time, to accomplish fast switching of which model that GPU is used for; this generalizes to multiple GPUs of one node.

In Kubernetes, once a Pod has been allocated some GPUs it keeps that exclusive allocation for the rest of the Pod's lifetime. FMA uses the following **dual pod** technique to circumvent that constraint.

- **Server-requesting Pods** reserve GPU resources via the Kubernetes Pod scheduler and the kubelet but do not run inference themselves.
- **Launcher Pods** (server-providing) run vLLM without requesting GPUs. They (a) gain access to all GPUs of their Node via special provisions that do not count this access as usage and (b) using `CUDA_VISIBLE_DEVICES`, as directed by the FMA controllers, get vLLM to use the specific GPU(s) reserved for the requesting pod.
- **FMA Controllers** manage the lifecycle: creating/deleting launchers, binding/unbinding requesting pods to launchers and vLLM instances in them, creating/deleting those vLLM instances, and orchestrating sleep/wake.

Server-requesting pods are managed through standard Kubernetes set objects and controllers such as Deployments and autoscalers. An FMA controller watches the server-requesting pods and translates scheduler decisions into actions on launcher pods and GPUs.

When a requesting pod is deleted, the controller puts the corresponding vLLM instance to sleep (model tensors move from GPU to main memory). Although the Kubernetes GPU allocation is released when the requesting pod is deleted, the vLLM instance retains the CUDA context in a small amount of GPU memory---until an FMA controller directs that launcher to delete that vLLM instance (which may happen, to limit memory usage). If and when a new requesting pod arrives, serving the same model with the same command-line parameters and assigned to the same GPU, the controller wakes the sleeping instance---which resumes in seconds. This is "hot start".

When a new server-requesting Pod arrives and there is no sleeping vLLM instance to wake for it, but a launcher is available, an FMA controller will direct the launcher to create a corresponding new vLLM instance. This takes advantage of the Python module loading already done by the launcher. This is "warm start".

> [!NOTE]
> Hot start (vLLM `/wake_up`) only occurs if the Kubernetes Pod scheduler assigns the new requesting pod to the same node (and GPU) where the sleeping vLLM instance resides. In a cluster with a single GPU per node, if the scheduler picks the same node, the GPU is necessarily the same one. In a multi-node pool the scheduler may assign the pod to a different node.

For more details see [the FMA repo README](https://github.com/llm-d-incubation/llm-d-fast-model-actuation/blob/main/README.md) and [the FMA repo docs](https://github.com/llm-d-incubation/llm-d-fast-model-actuation/tree/main/docs).

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
export FMA_VERSION=0.6.4
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
export FMA_CRD_BASE="https://raw.githubusercontent.com/llm-d-incubation/llm-d-fast-model-actuation/v${FMA_VERSION}/config/crd"
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
# ClusterRole (cluster-scoped): the FMA controllers list nodes for the launcher-populator
kubectl apply -f ${REPO_ROOT}/guides/${GUIDE_NAME}/rbac/clusterrole.yaml

# Role (namespaced): launcher pods read their own pod spec
kubectl apply -n ${NAMESPACE} -f ${REPO_ROOT}/guides/${GUIDE_NAME}/rbac/role.yaml

# RoleBinding (namespaced): bind the Role to ${NAMESPACE}'s default ServiceAccount.
# Created imperatively (not from a static manifest) so ${NAMESPACE} drives both the
# binding's namespace and the subject ServiceAccount's namespace.
kubectl create rolebinding fma-launcher-pod-reader \
  --role=fma-launcher-pod-reader \
  --serviceaccount=${NAMESPACE}:default \
  -n ${NAMESPACE} \
  --dry-run=client -o yaml | kubectl apply -f -
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

### 5. Deploy the Model Server (Dual Pods)

Apply the FMA custom resources — `InferenceServerConfig`, `LauncherConfig`, and `LauncherPopulationPolicy` (which define the model to serve, the launcher pod template, and how many launchers to place) — **together with** the server-requesting pods that reserve GPUs and trigger model loading. The requester `Deployment` is included as a resource of `modelserver/kustomization.yaml` (via `../requester`), so a single apply stands up both halves of the dual-pods design. A `Deployment` is used for the requesters (rather than a bare `ReplicaSet`) so they integrate cleanly with autoscalers such as the Workload Variant Autoscaler (WVA):

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/

kubectl rollout status deployment/fma-requester -n ${NAMESPACE} --timeout=300s
```
<!-- guide:deploy.modelserver end -->

> [!NOTE]
> This guide uses [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) which is publicly accessible and does not require a HuggingFace token. For gated models, you would need to mount the token via a different mechanism (FMA's ISC does not support `secretKeyRef`).

> [!NOTE]
> `HF_HOME` points at `/tmp/hf_cache`, which is ephemeral node storage. This keeps the guide self-contained, but every fresh launcher re-downloads the model weights and re-runs `torch.compile`. For production — or any repeated benchmarking — back `HF_HOME` with a shared, persistent volume (a PVC, e.g. `ReadWriteMany`) so model weights and compiled graphs persist across launcher pods and are not recomputed on each start.

> [!NOTE]
> The launcher pod does **not** request GPU resources from the Kubernetes scheduler or device plugin. Instead, the FMA controller sets `CUDA_VISIBLE_DEVICES` to point to the GPU reserved by the corresponding requesting pod, giving the launcher direct access to that GPU via the CUDA runtime. The `runtimeClassName: nvidia` is required on platforms (e.g., OpenShift) where GPU driver libraries are injected via the runtime class rather than the device plugin resource request.

> [!NOTE]
> `launcherCount` is **per matching node**. Setting `launcherCount: 1` creates one launcher pod on each node that has `nvidia.com/gpu.present: "true"`. Only launchers that get bound to a requesting pod will actually start a vLLM instance.

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

kubectl rollout status deployment/fma-requester -n ${NAMESPACE} --timeout=120s
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

To remove all deployed components. **Order matters:** delete the model-server
CRs and the requester Deployment first, then wait for the requester and launcher
pods to drain while the dual-pods controller is still running to strip their
finalizers, and only then remove the controllers. Removing the controller before
the pods drain leaves finalizer-bound pods stuck in `Terminating` forever.

<!-- guide:cleanup start -->
```bash
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/ --ignore-not-found=true

kubectl wait --for=delete pod -l app=fma-requester -n ${NAMESPACE} --timeout=120s

kubectl wait --for=delete pod -l app.kubernetes.io/component=launcher -n ${NAMESPACE} --timeout=120s

helm uninstall ${FMA_CHART_INSTANCE_NAME} -n ${NAMESPACE}

helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -f ${REPO_ROOT}/guides/${GUIDE_NAME}/rbac/clusterrole.yaml --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}

kubectl delete crd inferenceserverconfigs.fma.llm-d.ai launcherconfigs.fma.llm-d.ai launcherpopulationpolicies.fma.llm-d.ai
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup end -->
