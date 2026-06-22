# Replica Rebalancing (Experimental)

> **⚠️ Experimental Feature:** This feature is a proof of concept and is **not production-ready**. Enable it only in test or development environments after understanding the risks. The controller logs a warning at startup when this feature is enabled.

When multiple model deployments share a fixed single GPU quota within a namespace, individual HPAs scale independently and have no awareness of each other's GPU consumption. This can cause the namespace to be over-provisioned — one model's HPA raising its replica count while another is also scaling up, collectively exceeding the available GPU quota.

The **replica rebalancer** solves this by running a leader-elected control loop that reads a Kubernetes `ResourceQuota` from the target namespace and dynamically caps `spec.maxReplicas` on opted-in HPAs in that same namespace to keep total GPU usage within quota. The replica rebalancer operates entirely within a single namespace — it does not aggregate GPU usage or manage HPAs across namespace boundaries.

```
┌──────────────────────────────────────────────────────────────────┐
│                   replica rebalancer loop                        │
│                                                                  │
│  1. Read ResourceQuota → total GPU quota                         │
│  2. Sum GPUs-per-replica across all managed HPAs in a namespace  │
│  3. Redistribute maxReplicas to fit within quota                 │
│  4. Patch HPA spec.maxReplicas via Kubernetes API                │
└──────────────────────────────────────────────────────────────────┘
         ↕ patches          ↕ patches
   ┌──────────┐        ┌──────────┐
   │  HPA A   │        │  HPA B   │
   │ model-a  │        │ model-b  │
   └──────────┘        └──────────┘
```

The replica rebalancer only manages HPAs that have been explicitly opted in via an annotation. All other HPAs are left untouched.

## Prerequisites

- Multiple inference pools deployed in the same namespace via the [Multi-Inference Pool Setup guide](./README.multi-inference-pool.md).
- `llm-d-workload-variant-autoscaler` deployed in your cluster.
- Prometheus adapter (or KEDA) configured per the [HPA + EPP Metrics guide](./README.hpa-epp.md#configuration-guide) — the replica rebalancer works alongside the existing HPA setup, not instead of it.
- A `ResourceQuota` defining the GPU quota for the namespace.

## Step 1: Define a GPU ResourceQuota

Create a quota that caps total GPU requests across all pods in the **same namespace** where your model HPAs live. The replica rebalancer reads this quota to calculate the available GPU quota for that namespace:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: gpu-quota
  namespace: <your-namespace>
spec:
  hard:
    requests.nvidia.com/gpu: "10"
```

> **Note:** Set `requests.nvidia.com/gpu` to the actual number of GPUs available in your namespace. The value `"10"` is from the Kind-based simulation sample — for real clusters, inspect your node capacity with `kubectl describe nodes | grep nvidia.com/gpu` and set the quota accordingly.

Apply it:

```bash
kubectl apply -f gpu-quota.yaml
```

## Step 2: Enable the Replica Rebalancer

The replica rebalancer is disabled by default. Enable it by setting the `EXPERIMENTAL_COORDINATOR_ENABLED` environment variable in the `llm-d-workload-variant-autoscaler` manager ConfigMap:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: manager-config
  namespace: <autoscaler-namespace>
data:
  EXPERIMENTAL_COORDINATOR_ENABLED: "true"
  COORDINATOR_INTERVAL: "15s"   # how often the rebalance loop runs
```

| Variable | Default | Description |
|---|---|---|
| `EXPERIMENTAL_COORDINATOR_ENABLED` | `"false"` | Set to `"true"` to activate the replica rebalancer loop. |
| `COORDINATOR_INTERVAL` | `"15s"` | Reconciliation interval for the rebalance loop. |

Apply the updated ConfigMap and restart the autoscaler manager pod to pick up the new values.

## Step 3: Opt HPAs Into Replica Rebalancer Management

The replica rebalancer only manages HPAs that carry the `llm-d.ai/epp-inference-pool` annotation. Add it to every HPA you want the replica rebalancer to govern:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: model-a-hpa
  namespace: <your-namespace>
  annotations:
    llm-d.ai/managed: "true"
    llm-d.ai/model-id: "model-a"
    llm-d.ai/epp-inference-pool: "model-a-inference-pool"  # opts this HPA into replica rebalancer management
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: model-a
  minReplicas: 1
  maxReplicas: 10   # set to the maximum replicas your GPU quota can physically fit given per-replica GPU cost
  metrics:
  - type: External
    external:
      metric:
        name: epp_queue_size   # use the metric name you defined in your Prometheus adapter rules (see HPA + EPP Metrics guide)
      target:
        type: Value
        value: "250"   # tune to your model and hardware; "1" is used only in simulation
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
      - type: Pods
        value: 4
        periodSeconds: 15
    scaleDown:
      stabilizationWindowSeconds: 120
      policies:
      - type: Percent
        value: 100
        periodSeconds: 30
```

> **Note:** The `maxReplicas` field in the HPA manifest acts as the **upper ceiling** — the replica rebalancer will only ever lower it to fit within quota. It will never raise `maxReplicas` above the value you set here. As a sanity check, `maxReplicas × GPUs-per-replica` should not exceed your total `ResourceQuota` value, otherwise the ceiling is set higher than the quota would ever allow anyway.

Each pod in the deployment must declare explicit GPU resource requests so the replica rebalancer can calculate the per-replica GPU cost:

```yaml
resources:
  requests:
    nvidia.com/gpu: "1"
  limits:
    nvidia.com/gpu: "1"
```

> **Note:** Set `nvidia.com/gpu` to the number of GPUs your model actually requires per replica — for example, `"1"` for a Llama 3 8B on a single A100, or `"4"` for a 70B model requiring tensor parallelism across 4 GPUs. The replica rebalancer uses this value to calculate how many replicas can fit within the namespace quota.

## Step 4: Verify Replica Rebalancer Behavior

Check that the replica rebalancer is running and patching HPAs as expected:

```bash
# Confirm the replica rebalancer is active (look for the startup warning)
kubectl logs -n <autoscaler-namespace> -l app=llm-d-workload-variant-autoscaler | grep -i coordinator

# Watch how maxReplicas changes under load
kubectl get hpa -n <your-namespace> -w
```

When the GPU quota is nearly exhausted, you should see the replica rebalancer lower `maxReplicas` on one or more HPAs to prevent over-provisioning. When load drops and replicas are freed, the replica rebalancer raises the ceiling back toward the value defined in each HPA's manifest.

## How It Interacts With the HPA

The replica rebalancer and HPA work at different layers:

| Layer | Actor | What it controls |
|---|---|---|
| Demand signal | Prometheus / EPP metrics | When to scale |
| Replica count | HPA | How many replicas to run |
| GPU quota cap | Replica rebalancer | Maximum replicas allowed within the namespace |

The HPA still drives scaling decisions based on queue size and request load. The replica rebalancer acts as a guardrail, dynamically adjusting each HPA's `maxReplicas` ceiling so that the sum of all replicas never exceeds the available GPU quota.

## Perceived Effects

| Condition | What you observe |
|---|---|
| Demand rises, quota nearly full | Replica rebalancer lowers `spec.maxReplicas` on one or more HPAs — the HPA cannot schedule new pods beyond the new ceiling |
| Demand drops, replicas scale down | Replica rebalancer raises `spec.maxReplicas` back toward the value defined in the HPA manifest |
| One model is idle | Its freed GPUs are reflected in the quota calculation, allowing other HPAs to receive a higher ceiling |

Changes to `spec.maxReplicas` are visible in real time:

```bash
kubectl get hpa -n <your-namespace> -w
```

## Sample Multi-Model Setup with Kustomize

The replica rebalancer has been validated on Kind using simulation images (`ghcr.io/llm-d/llm-d-inference-sim`). For real vLLM deployments, swap the simulator image in `model-a-decode.yaml` / `model-b-decode.yaml` for your actual vLLM image and model configuration — the replica rebalancer and HPA setup is model-agnostic and requires no other changes.

The replica rebalancer sample configuration is structured as a Kustomize project to separate base resources from the rebalancer overlay:

```
config/samples/hpa/co-ordinator/
├── base/
│   ├── kustomization.yaml
│   ├── gpu-quota.yaml          # ResourceQuota
│   ├── model-a-hpa.yaml        # HPA without replica rebalancer annotation
│   ├── model-b-hpa.yaml
│   ├── model-a-decode.yaml     # model Deployments
│   └── model-b-decode.yaml
└── overlays/rebalance/
    └── kustomization.yaml      # patches HPAs to add epp-inference-pool annotation
```

Apply the base configuration first, then apply the rebalancer overlay to opt HPAs in:

```bash
# Deploy base resources (HPAs without replica rebalancer management)
kubectl apply -k config/samples/hpa/co-ordinator/base/

# Apply the overlay to add the replica rebalancer annotation to HPAs
kubectl apply -k config/samples/hpa/co-ordinator/overlays/rebalance/
```

## Known Limitations

- **Experimental:** This feature is a proof of concept. The API (annotations, env vars, ConfigMap keys) may change in future releases without notice.
- **No preemption:** The replica rebalancer rebalances `maxReplicas` on a fixed interval (`COORDINATOR_INTERVAL`).
