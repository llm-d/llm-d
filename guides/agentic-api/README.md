# Deploying vLLM Agentic API (`vllm/agentic-api:v0.8.0`) with llm-d

This guide walks through deploying [vLLM Agentic API](https://github.com/vllm-project/agentic-api/blob/32f32c8d77182cf23d8251be7a75d87cc47dcfec/docs/deploying/README.md) (`vllm/agentic-api:v0.8.0`) with **llm-d** and PostgreSQL-backed response state persistence, supporting both **Standalone Router Mode** and **GKE Gateway Mode**.

---

## Prerequisites

1. **Deploy the `zai-org/GLM-5.3-Flash` Aggregated Wide-EP Model Server & Router:**
   Follow the [Aggregated Wide-EP Deployment Guide (`guides/wide-ep/README.aggregated.md`)](../wide-ep/README.aggregated.md) to deploy `zai-org/GLM-5.3-Flash` (`DP=16, EP=16, TP=1`) and the `wide-ep` router in the `llm-d-wide-ep` namespace.
   > [!IMPORTANT]
   > Ensure `vllm serve` is started with `--reasoning-parser glm47`, `--tool-call-parser glm47`, and `--enable-auto-tool-choice` (already configured in [`guides/wide-ep/modelserver/gpu/vllm-glm-5.3-flash-aggregated/base/lws.yaml`](../wide-ep/modelserver/gpu/vllm-glm-5.3-flash-aggregated/base/lws.yaml)) so `agentic-api` can orchestrate tool calls and reasoning outputs.

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
    V0["GLM-5.3-Flash Leader\n(DP 0-7, EP 0-7)"]
    V1["GLM-5.3-Flash Worker\n(DP 8-15, EP 8-15)"]

    C -->|"/v1/responses"| A
    A <-->|"Rehydrate & Persist"| PG
    A -->|"--llm-api-base\nhttp://wide-ep-epp:80"| R
    R -->|"Ports 8000-8007"| V0
    R -->|"Ports 8000-8007"| V1
```

### Mode 2: GKE Gateway Mode (`llm-d-router-gateway` + `gke-l7-regional-external-managed`)

In **Gateway Mode**, both `agentic-api` (`Service/agentic-api:9000`) and the `llm-d` `InferencePool` (`InferencePool/wide-ep` backed by `wide-ep-epp:9002`) sit behind the **same GKE Gateway (`llm-d-inference-gateway`)**.
- **External client requests** to `/v1/responses`, `/v1/conversations`, `/v1/messages`, and `/v1/models` hit `http://<GATEWAY_IP>` (`HTTPRoute/wide-ep-agentic-gateway-route`) and route to **`Service/agentic-api:9000`** first (allowing `/v1/models?client_version=...` to return the Codex Model Catalog).
- **Loop avoidance via the `Host` header (`HTTPRoute` `spec.hostnames`):**
  - `agentic-api` maps `epp.gateway.internal` to the GKE Gateway IP (`${GATEWAY_IP}`) via Kubernetes `hostAliases` and sets `--llm-api-base http://epp.gateway.internal`.
  - Every upstream request from `agentic-api` to the GKE Gateway automatically carries the HTTP header **`Host: epp.gateway.internal`**.
  - A dedicated internal route (`HTTPRoute/wide-ep-internal-inference-route` with `hostnames: ["epp.gateway.internal"]`) matches `Host: epp.gateway.internal` at highest Gateway API precedence and routes directly to **`InferencePool/wide-ep` (`wide-ep-epp`)**, keeping the `Authorization` header completely free for end-user OIDC/Bearer tokens.

```mermaid
flowchart TB
    C["Client / verify.py"]
    GW["GKE Gateway (llm-d-inference-gateway)\nhttp://<GATEWAY_IP>:80"]
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

1. Ensure the `llm-d` router (`wide-ep-epp`) is deployed in standalone mode (see [`guides/wide-ep/README.aggregated.md`](../wide-ep/README.aggregated.md#step-2-deploy-the-standalone-llm-d-router)):
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

## Step 2B: Deploy in GKE Gateway Mode

1. **Upgrade `wide-ep` Router to Gateway Mode (`llm-d-router-gateway`):**
   ```bash
   kubectl delete deployment wide-ep-epp -n ${NAMESPACE} --ignore-not-found
   helm upgrade wide-ep oci://ghcr.io/llm-d/charts/llm-d-router-gateway \
     -f guides/wide-ep/router/wide-ep-aggregated.values.yaml \
     --set provider.name=none \
     --set httpRoute.create=false \
     --set router.epp.resources.requests.cpu=250m \
     --set router.epp.resources.requests.memory=1Gi \
     -n ${NAMESPACE} --version v0
   ```

2. **Deploy the GKE Gateway (`llm-d-inference-gateway`), Policies, and `HTTPRoute`s:**
   Apply [`manifests/gateway/gateway-routes.yaml`](manifests/gateway/gateway-routes.yaml):
   ```bash
   kubectl apply -n ${NAMESPACE} -f guides/agentic-api/manifests/gateway/gateway-routes.yaml
   ```
   Wait for the GKE Gateway to report `PROGRAMMED=True` and retrieve its external IP:
   ```bash
   kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -w
   export GATEWAY_IP=$(kubectl get gateway llm-d-inference-gateway -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
   echo "Gateway IP: ${GATEWAY_IP}"
   ```

3. **Substitute `${GATEWAY_IP}` into `agentic-api-gateway.yaml` and Apply:**
   Substitute `${GATEWAY_IP}` into [`manifests/gateway/agentic-api-gateway.yaml`](manifests/gateway/agentic-api-gateway.yaml) (which maps `epp.gateway.internal` to `${GATEWAY_IP}` via `hostAliases` and sets `--llm-api-base http://epp.gateway.internal`) and apply:
   ```bash
   envsubst '${GATEWAY_IP}' < guides/agentic-api/manifests/gateway/agentic-api-gateway.yaml | kubectl apply -n ${NAMESPACE} -f -
   kubectl rollout status -n ${NAMESPACE} deployment/agentic-api --timeout=120s
   ```

4. **Verify Gateway Mode** using [`verify.py`](verify.py) directly against `http://${GATEWAY_IP}`:
   ```bash
   python3 guides/agentic-api/verify.py --base-url http://${GATEWAY_IP} --skip-health
   ```

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
