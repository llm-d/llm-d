# GLM-5.2-FP8 on H200

Deploys [GLM-5.2-FP8](https://huggingface.co/zai-org/GLM-5.2-FP8) (753B MoE) on NVIDIA H200 GPUs
using LeaderWorkerSets, in two topologies: **P/D disaggregated** (wide expert-parallel with NIXL)
and **aggregate** (single-node TP=8 for high-interactivity workloads).

These manifests were tested on CoreWeave (CKS) with InfiniBand networking.

This recipe reuses the [wide-ep-lws guide](../../../README.md) for the router/gateway and
shared prerequisites (namespace, HF token secret, LeaderWorkerSet controller). The notes
below cover only what is specific to this deployment.

## Default Configuration

| Parameter                | Value                  |
| ------------------------ | ---------------------- |
| Model                    | zai-org/GLM-5.2-FP8   |
| KV Cache Dtype           | fp8                    |

## Prerequisites

In addition to the [wide-ep-lws prerequisites](../../../README.md#prerequisites):

* Set environment variables for this deployment:

  ```bash
  export KUBECONFIG=~/.kube/config
  export NAMESPACE=ecrncevi-dev
  export MODEL=zai-org/GLM-5.2-FP8
  ```

## Deploy the Model Server

### P/D Disaggregated

| Deployment | Prefill                    | Decode                        | Nodes / GPUs |
| ---------- | -------------------------- | ----------------------------- | ------------ |
| `p1d1`     | 1 node, DEP8               | 1 node, DEP8                  | 2 / 16       |
| `p1d2`     | 1 node, DEP8               | 2 nodes, DEP16 (wide)         | 3 / 24       |
| `p2d1`     | 2 nodes, DEP8 (replicated) | 1 node, DEP8                  | 3 / 24       |
| `p2d2`     | 2 nodes, DEP8 (replicated) | 2 nodes, DEP16 (wide)         | 4 / 32       |

```bash
kubectl apply -n ${NAMESPACE} -k deployments/<deployment>
```

### Aggregate

Single-node TP=8 deployment with no disaggregation — suited for high-interactivity workloads.

| Topology             | Nodes / GPUs |
| -------------------- | ------------ |
| 1 node, TP=8         | 1 / 8        |

```bash
kubectl apply -n ${NAMESPACE} -f base/aggregate.yaml
```

## Verification

Follow the [Verification steps in the wide-ep-lws guide](../../../README.md#verification),
using model `zai-org/GLM-5.2-FP8` in the request body:

```bash
curl -X POST http://${IP}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "zai-org/GLM-5.2-FP8",
        "prompt": "How are you today?"
    }' | jq
```

## Grafana Dashboards

| Topology       | Dashboard                                                        |
| -------------- | ---------------------------------------------------------------- |
| P/D            | [`base/grafana-wideep-overview.json`](base/grafana-wideep-overview.json) |
| Aggregate      | [`base/grafana-aggregate.json`](base/grafana-aggregate.json)     |

Load a dashboard as a ConfigMap (Grafana's sidecar picks it up automatically):

```bash
DASHBOARD=base/grafana-aggregate.json  # or: base/grafana-wideep-overview.json
kubectl create configmap $(basename $DASHBOARD .json) \
    --from-file=$(basename $DASHBOARD)=${DASHBOARD} \
    -n ${NAMESPACE} --dry-run=client -o yaml | \
  kubectl label -f - grafana_dashboard=1 --local --dry-run=client -o yaml | \
  kubectl apply -f -
```

## Cleanup

**P/D:**

```bash
kubectl delete -n ${NAMESPACE} -k deployments/<deployment>
```

**Aggregate:**

```bash
kubectl delete -n ${NAMESPACE} -f base/aggregate.yaml
```
