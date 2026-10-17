# Hyperconverged CephFS KV-Cache Offloading

## Overview

A hyperconverged setup runs compute and storage on the same nodes: the servers that host the GPUs also contribute their local drives to one shared storage pool, with no separate storage appliance.

This guide deploys a shared KV-cache tier on [CephFS](https://docs.ceph.com/en/squid/cephfs/) that runs on the same GPU nodes as the model servers. Ceph, managed by [Rook](https://rook.io), turns the local NVMe drives of every node into one ReadWriteMany filesystem. Each vLLM replica offloads KV-cache blocks to it and can read back blocks that any other replica wrote.

Choose this guide when:

- your model servers span **several GPU nodes**, and
- prompts share long prefixes (agentic, multi-turn, long-context workloads), and
- the working set of cached prefixes is larger than GPU memory plus CPU RAM.

Offloading to node-local NVMe gives each node its own cache: a prefix computed on one node is invisible to replicas on the others, so they recompute it. A shared filesystem removes that boundary. Running it hyperconverged, on the NVMe already in the GPU nodes, does so without an external storage appliance.

If your replicas all fit on one node, or the working set fits in CPU RAM, use the CPU RAM path of the [Tiered Prefix Cache guide](../tiered-prefix-cache/README.md) instead. That guide also covers the offloading concepts and other filesystem backends; this guide adds the storage layer and its automation.

### Architecture

```text
            ┌──────────────── llm-d Router (EPP) ────────────────┐
            │       prefix-cache affinity + token-load scoring    │
            └───────┬──────────────────┬──────────────────┬──────┘
                    │                  │                  │
        ┌───────────▼─────┐  ┌─────────▼───────┐  ┌───────▼─────────┐
        │   GPU node 1    │  │   GPU node 2    │  │   GPU node 3    │
        │ vLLM replicas   │  │ vLLM replicas   │  │ vLLM replicas   │
        │ GPU HBM  (hot)  │  │ GPU HBM  (hot)  │  │ GPU HBM  (hot)  │
        │ CPU RAM  (warm) │  │ CPU RAM  (warm) │  │ CPU RAM  (warm) │
        │ Ceph OSDs, NVMe │  │ Ceph OSDs, NVMe │  │ Ceph OSDs, NVMe │
        └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
                 └────────── CephFS (shared, RWX) ─────────┘
```

| Component | What this guide deploys |
| --------- | ----------------------- |
| Storage | The [Rook CephFS storage recipe](../recipes/storage/rook-cephfs/README.md): Rook operator, a Ceph cluster on the labelled GPU nodes, a `kvcache-fs` CephFS filesystem, and the `rook-cephfs-fast` StorageClass |
| Shared volume | One ReadWriteMany PVC, `llm-d-kv-cache-storage`, mounted by every replica at `/mnt/files-storage` |
| Model servers | vLLM with the native `OffloadingConnector` and `TieringOffloadingSpec`: GPU HBM → CPU RAM → CephFS |
| Router | llm-d Router with the same prefix- and load-aware plugins as the [optimized baseline](../optimized-baseline/README.md) |

## Default Configuration

| Parameter | Default |
| --------- | ------- |
| Model | [Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) |
| Replicas | 8 |
| GPUs per replica (TP) | 2 |
| Total GPUs | 16 |
| CPU RAM tier per replica | 256 GiB |
| Filesystem workers per replica | 64 read threads, 32 write threads |
| Shared tier | CephFS, 3 Ti PVC |
| Ceph data pool | 1 copy (`size=1`), `pg_num=128`, NVMe device class |
| CephFS metadata | 1 copy, 2 active MDS daemons with standby-replay |
| CephFS mount | `noatime`, `nodiratime`, `rsize` and `wsize` of 64 MiB |
| Ceph tuning | OSD, MDS, client and messenger settings in [`rook-cluster.values.yaml`](../recipes/storage/rook-cephfs/rook-cluster.values.yaml) |

The data and metadata pools keep a **single copy**. KV cache is derived data: losing an object costs one prefill, and losing the filesystem costs a refill of the cache, not user data, so the full raw NVMe capacity goes to the cache. To keep warm cache across a node failure or a rolling upgrade, raise `replicated.size` to 2 or 3 in [`rook-cluster.values.yaml`](../recipes/storage/rook-cephfs/rook-cluster.values.yaml).

## Prerequisites

- Have the [proper client tools installed on your local system](../../helpers/client-setup/README.md) to use this guide.

- At least three GPU nodes, each with unused NVMe devices for Ceph. Ceph takes every unused device that matches its device filter (default `^nvme[1-7]n1$`): check `lsblk` on each node first.

- A fast network between the nodes. The reference environment carries Ceph traffic over six 200 Gb/s Ethernet links per node with an MTU of 9000, separate from the management subnet.

- A values file with the settings that depend on your cluster: the subnets of the high-speed links and, if the default does not fit, the device filter. Write it as described in the recipe's [install instructions](../recipes/storage/rook-cephfs/README.md#1-write-your-site-specific-settings) and set `ROOK_CLUSTER_EXTRA_VALUES` to its path below. Set the subnets **before installing**: without them the OSDs advertise each node's management address, and switching a running cluster later breaks monitor quorum.

- Enough accelerators, memory and node disk for the [Configuration](#default-configuration) above (default: 16 GPUs, 400 GiB of RAM per replica, and about 70 GiB of free ephemeral storage per replica on the node for the model download). With less, adjust `replicas`, `--tensor-parallel-size` and `cpu_bytes_to_use` in the [model server patch](./modelserver/gpu/vllm/base/patch-vllm.yaml).

- Set the branch and clone the llm-d repo:

<!-- guide:prerequisites.clone start -->
<!-- llm-d-cicd:skip start -->
```bash
export BRANCH=main
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.clone end -->

- Set the guide specific environment variables:

<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=hyperconverged-cephfs
export NAMESPACE=llm-d-hyperconverged-cephfs
```
<!-- llm-d-cicd:skip start -->
```bash
export HF_TOKEN=HF_TOKEN_PLACEHOLDER
```
<!-- llm-d-cicd:skip end -->
```bash
export MONITORING_VALUES=
export PROVIDER_NAME=none # options: none, gke, agentgateway, istio
export MODEL=Qwen/Qwen3.6-35B-A3B
export ROOK_NAMESPACE=rook-ceph
export ROOK_CHART_REPO=https://charts.rook.io/release
export ROOK_CHART_VERSION=v1.20.2
export ROOK_CLUSTER_EXTRA_VALUES=
export STORAGE_NODE_LABEL=llm-d.ai/ceph-storage=true
export STORAGE_CLASS=rook-cephfs-fast
export KV_CACHE_PVC_SIZE=3Ti
export CURL_TEST_IMAGE=cfmanteiga/alpine-bash-curl-jq:latest
```
<!-- guide:env.static end -->

> [!NOTE]
> `HF_TOKEN` must be a [valid HuggingFace token](../../helpers/hf-token.md); replace
`HF_TOKEN_PLACEHOLDER` with your real token.

- Source the common guide environment variables:

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

- Install the Gateway API Inference Extension CRDs:

<!-- guide:prerequisites.gaie start -->
```bash
# GAIE_URL is automatically calculated from GAIE_VERSION at ${REPO_ROOT}/guides/env.sh
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
```
<!-- guide:prerequisites.gaie end -->

- Create a target namespace for the installation:

<!-- guide:prerequisites.namespace start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- guide:prerequisites.namespace end -->

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../../helpers/hf-token.md) to pull models:

<!-- guide:prerequisites.secrets start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.secrets end -->

- Label the GPU nodes that will also run Ceph. Use at least three, so that the Ceph monitors keep quorum:

<!-- guide:prerequisites.storage_nodes start -->
<!-- llm-d-cicd:skip start -->
```bash
# Replace with the GPU nodes that will also host the Ceph daemons and OSDs
export STORAGE_NODES="dgx-01 dgx-02 dgx-03"
kubectl label node ${STORAGE_NODES} ${STORAGE_NODE_LABEL} --overwrite
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.storage_nodes end -->

## Installation Instructions

### Automated Deployment

Every command on this page is rendered from this guide's [`guide.yaml`](./guide.yaml), and `scripts/guide.py emit` assembles the same steps into one script. After the prerequisites above (secret and node labels), this runs steps 1 to 4 below in order, in standalone mode, waiting for each one:

<!-- llm-d-cicd:skip start -->
```bash
cd ${REPO_ROOT}
scripts/guide.py emit guides/hyperconverged-cephfs --context ci \
  env prerequisites.gaie prerequisites.namespace \
  deploy.storage deploy.pvc deploy.router_values deploy.standalone deploy.modelserver | bash -e
```
<!-- llm-d-cicd:skip end -->

Use `deploy.gateway` in place of `deploy.standalone` for gateway mode, and `--var NAME=VALUE` to override any variable from the environment block above. Then continue with [Verification](#verification).

### 1. Deploy the Storage Layer

Install Rook, the Ceph cluster and the `kvcache-fs` filesystem with the [Rook CephFS storage recipe](../recipes/storage/rook-cephfs/README.md). The command returns once the cluster and the filesystem are ready, which takes several minutes while the OSDs are prepared:

<!-- guide:deploy.storage start -->
```bash
# Reads the ROOK_* and STORAGE_NODE_LABEL variables set above
${REPO_ROOT}/guides/recipes/storage/rook-cephfs/install-rook-cephfs.sh
```
<!-- guide:deploy.storage end -->

> [!NOTE]
> The recipe layers the file named by `ROOK_CLUSTER_EXTRA_VALUES` on top of its own values. If your cluster already offers a CephFS StorageClass, skip this step and set `STORAGE_CLASS` to its name.

### 2. Provision the Shared KV-Cache Volume

<!-- guide:deploy.pvc start -->
```bash
envsubst '${STORAGE_CLASS} ${KV_CACHE_PVC_SIZE}' \
  < ${REPO_ROOT}/guides/${GUIDE_NAME}/manifests/pvc.yaml | kubectl apply -n ${NAMESPACE} -f -
kubectl wait pvc/llm-d-kv-cache-storage -n ${NAMESPACE} \
  --for=jsonpath='{.status.phase}'=Bound --timeout=5m
```
<!-- guide:deploy.pvc end -->

The vLLM connector does not evict data from the shared tier. Size the PVC for your working set, and manage capacity with the reference PVC evictor from the [llm-d-kv-cache repository](https://github.com/llm-d/llm-d-kv-cache).

### 3. Deploy the llm-d Router

- Prepare the paths to the `helm` values files for the router:

<!-- guide:deploy.router_values start -->
```bash
# Paths to values files
export ROUTER_BASE_VALUES="${REPO_ROOT}/guides/recipes/router/base.values.yaml"
export ROUTER_VALUES="${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml"
```
<!-- guide:deploy.router_values end -->

- Optionally, enable Prometheus monitoring on the router. This requires the [monitoring stack](../../docs/operations/observability/setup.md) to be installed first:

<!-- guide:deploy.monitoring_values start -->
```bash
#
# Uncomment the below to enable Prometheus monitoring on the llm-d router
#
# Unlike the ROUTER_*_VALUES paths above, this variable carries its own
# -f flag: it is empty by default, so the helm commands expand it as-is.
#
# export MONITORING_VALUES="-f ${REPO_ROOT}/guides/recipes/router/features/monitoring.values.yaml"
```
<!-- guide:deploy.monitoring_values end -->

#### Standalone Mode

This deploys the llm-d Router in [Standalone Mode](../../docs/architecture/core/router/proxy.md) with an Envoy sidecar (default):

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.standalone end -->

<details>
<summary><b>Gateway Mode</b></summary>

To use a Kubernetes Gateway managed proxy instead, [deploy a Gateway](../../docs/infrastructure/gateway), set `PROVIDER_NAME` to its provider, and run this in place of the command above:

<!-- guide:deploy.gateway start -->
```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_GATEWAY_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  --set provider.name=${PROVIDER_NAME} \
  --set httpRoute.create=true \
  --set httpRoute.inferenceGatewayName=llm-d-inference-gateway \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
<!-- guide:deploy.gateway end -->

</details>

### 4. Deploy the Model Server

Each model server downloads the model to its node's local disk on first start. For model sources, caching, and startup optimization, see the [Model Loading and Startup Acceleration operations guide](../../docs/operations/model-loading-and-startup.md).

<!-- guide:deploy.modelserver start -->
```bash
kubectl apply -n ${NAMESPACE} \
  -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/base/
```
<!-- guide:deploy.modelserver end -->

### 5. (Optional) Enable monitoring

With the [monitoring stack](../../docs/operations/observability/setup.md) installed, deploy the monitoring resources for the model servers:

<!-- guide:deploy.monitoring start -->
```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring
```
<!-- guide:deploy.monitoring end -->

## Verification

### 1. Get the IP of the Proxy

**Standalone Mode**

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service ${GUIDE_NAME}-epp -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

<details>
<summary><b>Gateway Mode</b></summary>

<!-- guide:verify.endpoint.gateway start -->
```bash
export IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
<!-- guide:verify.endpoint.gateway end -->

</details>

### 2. Check Ceph, Send Test Requests, and Confirm the Tier Is Shared

<!-- guide:verify.tests start -->
```bash
kubectl exec -n ${ROOK_NAMESPACE} deploy/rook-ceph-tools -- ceph status
kubectl exec -n ${ROOK_NAMESPACE} deploy/rook-ceph-tools -- ceph fs status kvcache-fs
kubectl exec -n ${ROOK_NAMESPACE} deploy/rook-ceph-tools -- ceph osd pool get kvcache-fs-data0 all
# OSD addresses should be on the high-speed subnets, not the management one
kubectl exec -n ${ROOK_NAMESPACE} deploy/rook-ceph-tools -- ceph osd dump | grep '^osd\.'
kubectl get pvc llm-d-kv-cache-storage -n ${NAMESPACE}

kubectl run curl-test --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/sh -c 'curl -sS -X POST "http://${IP}/v1/completions" -H "Content-Type: application/json" -d "{\"model\": \"${MODEL}\", \"prompt\": \"How are you today?\"}"'

# Long prompt (~6K tokens) so KV blocks are offloaded
kubectl run curl-offload --rm -i --restart=Never \
  --image=${CURL_TEST_IMAGE} \
  --namespace="${NAMESPACE}" \
  --env="IP=${IP}" \
  --env="MODEL=${MODEL}" \
  -- /bin/bash -c '
    PROMPT=$(printf "Story: "; for i in $(seq 1 800); do printf "alice met bob and they walked together. "; done)
    jq -n --arg model "${MODEL}" --arg prompt "${PROMPT}" "{model:\$model, prompt:\$prompt, max_tokens:3, temperature:0}" \
      | curl -sS "http://${IP}/v1/completions" -H "Content-Type: application/json" -d @- | jq .usage'

# Every replica mounts the same CephFS volume, so each one reports the same usage
for POD in $(kubectl get pod -n ${NAMESPACE} -l llm-d.ai/guide=${GUIDE_NAME} -o jsonpath='{.items[*].metadata.name}'); do
  echo "${POD}: $(kubectl exec -n ${NAMESPACE} ${POD} -c modelserver -- du -sh /mnt/files-storage | cut -f1)"
done
# The effective mount options are authoritative: check that noatime and nodiratime are listed
kubectl exec -n ${NAMESPACE} ${POD} -c modelserver -- grep ' /mnt/files-storage ' /proc/mounts
```
<!-- guide:verify.tests end -->

Expected results:

- `ceph status` reports `HEALTH_OK` with every OSD `up` and `in`, `ceph fs status` shows two active MDS daemons, the data pool has `size: 1` and `pg_num: 128`, and the PVC is `Bound`.
- The OSD addresses are on your high-speed subnets.
- The completion request returns generated text.
- After the long prompt, **every** replica reports the same non-zero usage for `/mnt/files-storage`, although only one of them served the request. That is the shared tier: blocks written by one replica are visible to all.
- The mount line lists `noatime` and `nodiratime`. The kernel omits `rsize` and `wsize` from this line when they equal its maximum of 64 MiB, so their absence is expected.

The model server metrics show the same thing per replica: `vllm:kv_offload_tiering_write_bytes_total{tier="1:fs"}` counts the bytes written to CephFS, and `vllm:prompt_tokens_by_source_total{source="external_kv_transfer"}` counts the prompt tokens restored from an offload tier. When a second replica receives a prompt that another replica already served, the second counter rises on it and the request skips most of the prefill.

## Cleanup

Remove the llm-d stack and the shared volume:

<!-- guide:cleanup.stack start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/base/ --ignore-not-found=true

kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/guides/recipes/modelserver/components/monitoring --ignore-not-found=true

kubectl delete pvc llm-d-kv-cache-storage -n ${NAMESPACE} --ignore-not-found=true
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.stack end -->

Remove the storage layer. This destroys the Ceph cluster and everything stored in it, after asking for confirmation. The NVMe devices keep their Ceph labels afterwards; see the recipe's [Troubleshooting](../recipes/storage/rook-cephfs/README.md#troubleshooting) before reusing them:

<!-- guide:cleanup.storage start -->
<!-- llm-d-cicd:skip start -->
```bash
# DESTRUCTIVE: destroys the Ceph cluster and everything stored in it (asks for confirmation)
${REPO_ROOT}/guides/recipes/storage/rook-cephfs/install-rook-cephfs.sh --uninstall
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup.storage end -->

## Troubleshooting

For the storage layer (no OSDs created, the cluster or a volume never becoming ready, reusing drives from an earlier Ceph cluster), see the recipe's [Troubleshooting](../recipes/storage/rook-cephfs/README.md#troubleshooting).

- **Model servers cannot write to `/mnt/files-storage`.** OpenShift assigns the pod an `fsGroup` that owns the volume. On other distributions, set `securityContext.fsGroup` on the pod.
- **Replicas do not reuse each other's blocks.** Every replica must run the same model, vLLM version and `PYTHONHASHSEED`; otherwise block hashes differ and lookups miss.

## Tuning

- **Storage.** The Ceph pool, daemon, client and mount settings, and the manual steps that depend on your hardware (spreading OSDs over several NICs, calibrating the OSD scheduler per drive, choosing the replication), are in the recipe's [Tuning](../recipes/storage/rook-cephfs/README.md#tuning) section.
- **Filesystem workers.** `n_read_threads` and `n_write_threads` are vLLM settings. With too few, requests wait while the shared tier sits idle; when writes fall behind, the CPU tier fills, vLLM refuses to store more blocks, and prompts are recomputed.
- **Tier sizes.** `cpu_bytes_to_use` sets the CPU RAM tier. Keep the pod memory request and the `/dev/shm` size limit above it.
- **Routing.** The prefix-affinity filter's `peakPrefillThroughput` is model- and hardware-specific. Measure it with the [calibration recipe](../recipes/router/calibration/README.md).

## Benchmarking Reports

With 8 model servers on 16 H100 GPUs under an agentic coding workload, adding the shared CephFS tier reached 7.48 requests/s at 512 concurrent sessions: 81.5% above serving without offload and 43.4% above a CPU RAM tier alone, with a median TTFT of 11.2 s against 46.3 s. Below saturation the three configurations perform the same.

- [Qwen/Qwen3.6-35B-A3B on H100 and vLLM: no offload, CPU RAM, CephFS, and node-local NVMe](./benchmark-results/vllm-qwen3.6-35b-a3b-h100/README.md)
