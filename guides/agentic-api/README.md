# Deploying vLLM Agentic API (`vllm/agentic-api:v0.8.0`) with llm-d

This guide walks through deploying [vLLM Agentic API](https://github.com/vllm-project/agentic-api/blob/32f32c8d77182cf23d8251be7a75d87cc47dcfec/docs/deploying/README.md) (`vllm/agentic-api:v0.8.0`) with **llm-d** and PostgreSQL-backed response state persistence, supporting both **Standalone Router Mode** and **Gateway Mode** (validated on **Istio** and the **GKE managed Gateway**).

---

## Supported Gateway Providers

As in the aggregated well-lit path [`guides/optimized-baseline`](../optimized-baseline/README.md#prerequisites), Gateway Mode is selected with `PROVIDER_NAME` rather than hardcoded to GKE:

```bash
export PROVIDER_NAME=istio # options: none, gke, agentgateway, istio
```

| `PROVIDER_NAME` | What the llm-d router chart renders | Extra manifests this guide applies |
| --- | --- | --- |
| `istio` | A `DestinationRule` giving the Istio gateway TLS access to the EPP's ext_proc endpoint | none |
| `gke` | `GCPBackendPolicy` + `HealthCheckPolicy` for `InferencePool/wide-ep` | [`manifests/gateway/gke-policies.yaml`](manifests/gateway/gke-policies.yaml), for `Service/agentic-api` — the chart does not know about that backend |
| `none` | nothing | — |

This guide ships **no `Gateway` of its own**: deploy one named `llm-d-inference-gateway` by following [the gateway guides](../../docs/infrastructure/gateway), and every llm-d guide in that namespace shares it with its own `HTTPRoute`s. The two `HTTPRoute`s in [`manifests/gateway/routes.yaml`](manifests/gateway/routes.yaml) are provider-neutral.

**Standalone Router Mode needs no Gateway at all** and runs unchanged everywhere — start there.

---

## Prerequisites

1. **Deploy the `zai-org/GLM-5.3-Flash` Wide-EP Model Server & Router:**
   Follow the [GLM-5.3-Flash Wide-EP P/D Disaggregated Guide (`guides/wide-ep/modelserver/gpu/vllm-glm-5.3-flash/README.md`)](../wide-ep/modelserver/gpu/vllm-glm-5.3-flash/README.md) to deploy `zai-org/GLM-5.3-Flash` (`2 prefill + 2 decode` pods, `DP=16, EP=16, TP=1` per role) and the `wide-ep` router in the `llm-d-wide-ep` namespace.
   > [!IMPORTANT]
   > Ensure `vllm serve` is started with `--reasoning-parser glm47`, `--tool-call-parser glm47`, and `--enable-auto-tool-choice` (already configured in [`guides/wide-ep/modelserver/gpu/vllm-glm-5.3-flash/base/disaggregatedset.yaml`](../wide-ep/modelserver/gpu/vllm-glm-5.3-flash/base/disaggregatedset.yaml)) so `agentic-api` can orchestrate tool calls and reasoning outputs.

2. **Verify `zai-org/GLM-5.3-Flash` Pods are Ready:**
   ```bash
   export NAMESPACE=llm-d-wide-ep
   kubectl get pods -n ${NAMESPACE} -l llm-d.ai/model=GLM-5.3-Flash
   ```

---

## Architecture Topologies

### Mode 1: Standalone Router Mode (`llm-d-router-standalone`)

In **Standalone Mode**, `wide-ep-epp` runs with an embedded Envoy proxy sidecar (`Service/wide-ep-epp:80`). `agentic-api` sits as a Kubernetes `Service` (`Service/agentic-api:9000`) in front of `wide-ep-epp`, and configures `--llm-api-base http://wide-ep-epp.llm-d-wide-ep.svc.cluster.local:80`.

```mermaid
flowchart LR
    C["Client / verify.py\n(HTTP /v1/responses, Webhooks, WS)"]
    A["Service: agentic-api:9000\n(vllm/agentic-api:v0.8.0)"]
    PG[("PostgreSQL 17\n(agentic-api-postgres:5432)")]
    R["Service: wide-ep-epp:80\n(Standalone Envoy + EPP)"]
    V0["GLM-5.3-Flash Prefill (x2)\n(DP 16, EP 16)"]
    V1["GLM-5.3-Flash Decode (x2)\n(DP 16, EP 16)"]

    C -->|"/v1/responses"| A
    A <-->|"Rehydrate & Persist"| PG
    A -->|"--llm-api-base\nhttp://wide-ep-epp:80"| R
    R -->|"Ports 8000-8007"| V0
    R -->|"Ports 8000-8007"| V1
    V0 -.->|"NIXL RDMA KV Transfer"| V1
```

### Mode 2: Gateway Mode (`llm-d-router-gateway` + Istio or GKE)

In **Gateway Mode**, both `agentic-api` (`Service/agentic-api:9000`) and the `llm-d` `InferencePool` (`InferencePool/wide-ep` backed by `wide-ep-epp:9002`) sit behind the **same Gateway (`llm-d-inference-gateway`)**. The topology below is identical on Istio and GKE — only the `gatewayClassName` and GKE's backend policies differ.
- **External client requests** to `/v1/responses`, `/v1/conversations`, `/v1/messages`, and `/v1/models` hit `http://<GATEWAY_IP>` (`HTTPRoute/wide-ep-agentic-gateway-route`) and route to **`Service/agentic-api:9000`** first (allowing `/v1/models?client_version=...` to return the Codex Model Catalog).
- **Loop avoidance via the `Host` header (`HTTPRoute` `spec.hostnames`):**
  - `agentic-api` maps `epp.gateway.internal` to the Gateway address (`${GATEWAY_IP}`) via Kubernetes `hostAliases` and sets `--llm-api-base http://epp.gateway.internal`.
  - Every upstream request from `agentic-api` to the Gateway automatically carries the HTTP header **`Host: epp.gateway.internal`**.
  - A dedicated internal route (`HTTPRoute/wide-ep-internal-inference-route` with `hostnames: ["epp.gateway.internal"]`) matches `Host: epp.gateway.internal` at highest Gateway API precedence and routes directly to **`InferencePool/wide-ep` (`wide-ep-epp`)**, keeping the `Authorization` header completely free for end-user OIDC/Bearer tokens.

> [!NOTE]
> `hostAliases` requires a literal **IP**, and providers report their Gateway address differently: GKE publishes `status.addresses[0]` as `type: IPAddress` (an external LoadBalancer IP), while Istio publishes `type: Hostname` (the gateway `Service` FQDN, because the [Istio recipe](../recipes/gateway/istio/configmap.yaml) creates a `ClusterIP` Service). Reading `status.addresses[0].value` therefore yields an unusable value on Istio — Step 2B resolves `${GATEWAY_IP}` per provider. Either way the resulting IP is reachable from the `agentic-api` pod, which is all the `hostAliases` hop needs; reaching the Gateway from your laptop differs per provider, see [Step 2B](#step-2b-deploy-in-gateway-mode-istio-or-gke).

```mermaid
flowchart TB
    C["Client / verify.py"]
    GW["Gateway: llm-d-inference-gateway\n(istio | gke-l7-regional-external-managed)\nhttp://<GATEWAY_IP>:80"]
    A["Service: agentic-api:9000\n(--llm-api-base http://epp.gateway.internal)"]
    PG[("PostgreSQL 17")]
    POOL["InferencePool: wide-ep\n(EPP ext_proc :9002)"]
    V["GLM-5.3-Flash Workers\n(16 DP / EP Ranks)"]

    C -->|"1. POST http://<GATEWAY_IP>/v1/responses"| GW
    GW -->|"2. HTTPRoute (wide-ep-agentic-gateway-route)\n(/v1/responses -> agentic-api)"| A
    A <-->|"3. State Hydration"| PG
    A -->|"4. POST http://epp.gateway.internal/v1/responses\nHeader: Host: epp.gateway.internal"| GW
    GW -->|"5. HTTPRoute (wide-ep-internal-inference-route)\nhostnames: [epp.gateway.internal] -> InferencePool/wide-ep"| POOL
    POOL -->|"6. Prefix & Load-Aware Selection"| V
```

---

## Step 1: Deploy PostgreSQL for Response State Persistence

Create the `agentic-api-postgres` Secret and deploy PostgreSQL (`postgres:17-alpine` backed by a `PersistentVolumeClaim`):

```bash
export NAMESPACE=llm-d-wide-ep
PGPASS=$(openssl rand -hex 16)

kubectl create secret generic agentic-api-postgres -n ${NAMESPACE} \
  --from-literal=password="$PGPASS" \
  --from-literal=database-url="postgres://postgres:${PGPASS}@agentic-api-postgres.${NAMESPACE}.svc.cluster.local:5432/agentic_api" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -n ${NAMESPACE} -f guides/agentic-api/manifests/postgres.yaml
kubectl rollout status -n ${NAMESPACE} deployment/agentic-api-postgres --timeout=120s
```

---

## Step 2A: Deploy in Standalone Mode (Recommended First)

1. Ensure the `llm-d` router (`wide-ep-epp`) is deployed in standalone mode (see [`guides/wide-ep/modelserver/gpu/vllm-glm-5.3-flash/README.md`](../wide-ep/modelserver/gpu/vllm-glm-5.3-flash/README.md#1-deploy-the-llm-d-router-disaggregated-pd-mode)):
   ```bash
   kubectl get svc wide-ep-epp -n ${NAMESPACE}
   ```

2. Apply [`manifests/agentic-api-standalone.yaml`](manifests/agentic-api-standalone.yaml), which configures `vllm/agentic-api:v0.8.0` with `--llm-api-base http://wide-ep-epp.llm-d-wide-ep.svc.cluster.local:80`:
   ```bash
   kubectl apply -n ${NAMESPACE} -f guides/agentic-api/manifests/agentic-api-standalone.yaml
   kubectl rollout status -n ${NAMESPACE} deployment/agentic-api --timeout=120s
   ```

3. **Verify Standalone Mode** using [`verify.py`](verify.py):
   ```bash
   kubectl port-forward -n ${NAMESPACE} svc/agentic-api 9000:9000 &
   PF_PID=$!
   sleep 3

   python3 guides/agentic-api/verify.py --base-url http://127.0.0.1:9000

   kill $PF_PID
   ```

---

## Step 2B: Deploy in Gateway Mode (Istio or GKE)

1. **Deploy a Kubernetes Gateway named `llm-d-inference-gateway`** by following one of [the gateway guides](../../docs/infrastructure/gateway) — for Istio, [`istio.md`](../../docs/infrastructure/gateway/istio.md), which installs the control plane with `ENABLE_GATEWAY_API_INFERENCE_EXTENSION=true` and applies [`guides/recipes/gateway/istio`](../recipes/gateway/istio). The `Gateway` is namespaced, so each namespace needs its own even when the control plane is already up.

   Then set the provider and read the Gateway's address:
   ```bash
   export PROVIDER_NAME=istio # options: none, gke, agentgateway, istio
   kubectl wait --for=condition=Programmed gateway/llm-d-inference-gateway -n ${NAMESPACE} --timeout=300s

   # hostAliases needs an IP. GKE reports status.addresses[0] as type: IPAddress,
   # but Istio reports type: Hostname (the gateway Service FQDN), so read its ClusterIP.
   if [ "${PROVIDER_NAME}" = "istio" ]; then
     export GATEWAY_IP=$(kubectl get svc llm-d-inference-gateway-istio -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
   else
     export GATEWAY_IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
   fi
   echo "Gateway address: ${GATEWAY_IP}"
   ```

   > [!IMPORTANT]
   > `${GATEWAY_IP}` must be a bare IP. If it holds a hostname such as
   > `llm-d-inference-gateway-istio.<ns>.svc.cluster.local`, Step 4 fails with
   > `spec.template.spec.hostAliases[0].ip: Invalid value: ... must be a valid IP address`.
   > Check it with `echo "[${GATEWAY_IP}]"` before continuing.

2. **Upgrade the `wide-ep` Router to Gateway Mode (`llm-d-router-gateway`):**
   ```bash
   kubectl delete deployment wide-ep-epp -n ${NAMESPACE} --ignore-not-found
   helm upgrade --install wide-ep oci://ghcr.io/llm-d/charts/llm-d-router-gateway \
     -f guides/recipes/router/base.values.yaml \
     -f guides/wide-ep/router/wide-ep.values.yaml \
     --set provider.name=${PROVIDER_NAME} \
     --set httpRoute.create=false \
     -n ${NAMESPACE} --version v0
   ```
   > [!IMPORTANT]
   > Set `provider.name` to the provider whose Gateway you deployed in step 1. As [`guides/optimized-baseline`](../optimized-baseline/README.md) warns, the default `none` renders no provider-specific resources — on GKE that means no `HealthCheckPolicy` for the `InferencePool`, so the Gateway marks the backend unhealthy and inference requests fail with 503s. On Istio, `none` omits the `DestinationRule` the gateway needs to reach the EPP's ext_proc endpoint over TLS.

   `httpRoute.create=false` because this guide supplies its own two routes in the next step.

3. **Apply the `HTTPRoute`s (and, on GKE, the `agentic-api` backend policies):**
   ```bash
   kubectl apply -n ${NAMESPACE} -f guides/agentic-api/manifests/gateway/routes.yaml

   # GKE only: networking.gke.io CRDs, so this fails on Istio and other providers.
   # Covers Service/agentic-api; the InferencePool's policies come from the chart above.
   if [ "${PROVIDER_NAME}" = "gke" ]; then
     kubectl apply -n ${NAMESPACE} -f guides/agentic-api/manifests/gateway/gke-policies.yaml
   fi
   ```

4. **Substitute `${GATEWAY_IP}` into `agentic-api-gateway.yaml` and Apply:**
   Substitute `${GATEWAY_IP}` into [`manifests/gateway/agentic-api-gateway.yaml`](manifests/gateway/agentic-api-gateway.yaml) (which maps `epp.gateway.internal` to `${GATEWAY_IP}` via `hostAliases` and sets `--llm-api-base http://epp.gateway.internal`) and apply:
   ```bash
   envsubst '${GATEWAY_IP}' < guides/agentic-api/manifests/gateway/agentic-api-gateway.yaml | kubectl apply -n ${NAMESPACE} -f -
   kubectl rollout status -n ${NAMESPACE} deployment/agentic-api --timeout=120s
   ```

5. **Verify Gateway Mode** using [`verify.py`](verify.py).
   As in [`guides/optimized-baseline`](../optimized-baseline/README.md#verification), the endpoint is whatever the `Gateway` reports in `status.addresses[0].value` — but whether your laptop can reach it depends on the provider's `Service` type.

   **GKE** (`${GATEWAY_IP}` is an external LoadBalancer IP):
   ```bash
   python3 guides/agentic-api/verify.py --base-url http://${GATEWAY_IP} --skip-health
   ```

   **Istio** (the recipe's Gateway `Service` is `ClusterIP`, so port-forward it):
   ```bash
   kubectl port-forward -n ${NAMESPACE} svc/llm-d-inference-gateway-istio 8080:80 &
   PF_PID=$!
   sleep 3

   python3 guides/agentic-api/verify.py --base-url http://127.0.0.1:8080 --skip-health

   kill $PF_PID
   ```
   All four `verify.py` tests run entirely client-side (including the local webhook receiver in test `[3/4]`, which `verify.py` itself calls), so a port-forward is sufficient — nothing in the cluster needs to dial back to your machine.

   `--skip-health` is required in Gateway Mode for both providers: `/health` and `/ready` fall through the default route to the `InferencePool`, not to `agentic-api`.

---

## Verification Script (`verify.py`)

[`guides/agentic-api/verify.py`](verify.py) requires only the Python 3 standard library and performs four end-to-end tests:

1. **Health & Model Discovery (`[1/4]`):** Verifies `/health`, `/ready` (in standalone mode), and `GET /v1/models` (`zai-org/GLM-5.3-Flash`).
2. **Stateful HTTP `/v1/responses` (`[2/4]`):**
   - **Turn 1:** Stores a secret verification code (`COBALT-7492`) with `store: true`, returning `resp_...`.
   - **Turn 2:** Sends a follow-up request referencing **only** `previous_response_id` (no client-side message history) and verifies that `agentic-api` rehydrates the prior turn from PostgreSQL and returns `COBALT-7492`.
3. **Webhook Mode & Stateful Tool Loop (`[3/4]`):**
   - Starts a local HTTP webhook listener (`http://127.0.0.1:<port>/webhook/deployment-events`).
   - Sends a stateful `/v1/responses` request with a `function` tool (`emit_deployment_webhook`), receives the model's `function_call`, dispatches the webhook payload to the HTTP webhook server (`WH-9981`), and sends the `function_call_output` back via `previous_response_id` to complete the stateful response.
4. **WebSocket Mode (`[4/4]`):**
   - Upgrades an RFC 6455 WebSocket connection to `ws://<endpoint>/v1/responses`, sends a `response.create` frame (`store: true, stream: true`), and verifies streaming completion (`response.completed`).
