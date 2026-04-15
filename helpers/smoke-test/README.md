# llm-d Deployment Smoke Test

A lightweight smoke-test script that verifies an llm-d cluster is healthy after deployment.

Location:

- `guides/operations/healthcheck/healthcheck.sh`

## What It Checks

| Check | Endpoint | Purpose |
|-------|----------|---------|
| (Optional) Liveness | `GET /health` | If exposed by your gateway; can be required via `--require-health` |
| Readiness | `GET /v1/models` | Confirms OpenAI-compatible API is reachable; can auto-discover model when `jq` is available |
| Inference | `POST /v1/completions` and/or `POST /v1/chat/completions` | End-to-end inference works; measures latency (auto-fallback supported) |

### jq dependency

- `curl` is required.
- `jq` is **optional**:
  - With `jq`: enables model auto-discovery, response structure validation, and `-o json` output.
  - Without `jq`: still performs HTTP-level smoke tests, but you must provide `--model` (or `MODEL_ID=...`).

## Usage

```bash
# Basic — port-forwarded gateway on localhost:8000
./healthcheck.sh

# Explicit endpoint and model
./healthcheck.sh -e http://inference.example.com -m meta-llama/Llama-3-8B

# Force chat-completions
./healthcheck.sh -e http://gateway:80 --api chat -m meta-llama/Llama-3-8B

# Fail if inference latency exceeds 5 seconds
./healthcheck.sh -l 5000

# JSON output for CI pipelines (requires jq)
./healthcheck.sh -o json
```

## Options

| Flag | Env Var | Default | Description |
|------|---------|---------|-------------|
| `-e, --endpoint` | `ENDPOINT` | `http://localhost:8000` | Base URL of the inference endpoint |
| `-m, --model` | `MODEL_ID` | *(auto-discovered with jq)* | Model ID to query |
| `-t, --timeout` | `TIMEOUT` | `30` | HTTP request timeout in seconds |
| `-l, --max-latency` | `MAX_LATENCY` | `0` (disabled) | Fail if inference latency exceeds this (ms) |
| `-o, --output` | — | `text` | Output format: `text` or `json` (json requires jq) |
| `--api` | — | `auto` | `auto`, `completions`, or `chat` |
| `--require-health` | — | disabled | Treat `/health` non-200 as a failure (default: warn) |

## Kubernetes Usage

### One-off (ephemeral) pod

This uses `alpine` and installs dependencies at runtime (simple and reproducible):

```bash
kubectl run llm-d-healthcheck --rm -i --restart=Never \
  --namespace llm-d \
  --image=alpine:3.20 \
  -- sh -lc '
    apk add --no-cache bash curl jq >/dev/null &&
    bash -lc "$(cat guides/operations/healthcheck/healthcheck.sh)" -- -e http://llm-d-gateway:80 -o json
  '
```

If you prefer **no jq**, drop it and provide `-m` explicitly:

```bash
kubectl run llm-d-healthcheck --rm -i --restart=Never \
  --namespace llm-d \
  --image=alpine:3.20 \
  -- sh -lc '
    apk add --no-cache bash curl >/dev/null &&
    bash -lc "$(cat guides/operations/healthcheck/healthcheck.sh)" -- -e http://llm-d-gateway:80 -m meta-llama/Llama-3-8B
  '
```

### ConfigMap + Job (recommended)

```bash
kubectl create configmap llm-d-healthcheck-script \
  --from-file=healthcheck.sh=guides/operations/healthcheck/healthcheck.sh \
  --namespace llm-d
```

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: llm-d-healthcheck
  namespace: llm-d
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: healthcheck
        image: alpine:3.20
        command: ["sh", "-lc"]
        args:
          - |
            apk add --no-cache bash curl jq >/dev/null &&
            /scripts/healthcheck.sh -e http://llm-d-gateway:80 -o json
        volumeMounts:
        - name: script
          mountPath: /scripts
      volumes:
      - name: script
        configMap:
          name: llm-d-healthcheck-script
          defaultMode: 0755
```

## Example Output

### Text (default)

```
╔══════════════════════════════════════════╗
║         llm-d Health Check Report        ║
╠══════════════════════════════════════════╣
║  Status:    HEALTHY                      ║
║  Endpoint:  http://localhost:8000        ║
║  Model:     meta-llama/Llama-3-8B        ║
║  API:       auto                         ║
║  jq:        true                         ║
╠══════════════════════════════════════════╣
║  /health          HTTP 404               ║
║  /v1/models       HTTP 200   count=1     ║
║  /v1/completions  HTTP 200   1842  ms    ║
╠══════════════════════════════════════════╣
║  Checks: 2 passed, 0 failed, 1 warned    ║
╚══════════════════════════════════════════╝
```

### JSON (`-o json`, requires jq)

```json
{
  "status": "healthy",
  "timestamp": "2026-02-13T00:00:00Z",
  "endpoint": "http://localhost:8000",
  "model": "meta-llama/Llama-3-8B",
  "api_mode": "auto",
  "jq_present": true,
  "checks": { "passed": 3, "failed": 0, "warned": 0 },
  "results": {
    "health": { "http_status": "200" },
    "models": { "http_status": "200", "model_count": "1" },
    "inference": { "path": "/v1/completions", "http_status": "200", "latency_ms": "1842" }
  }
}
```

