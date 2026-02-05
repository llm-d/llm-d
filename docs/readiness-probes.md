# vLLM Model-Aware Readiness Probes

## Overview

Proper health checking for vLLM inference containers requires understanding three distinct lifecycle stages:

1. **Container Running** - Kubernetes container lifecycle
2. **API Server Ready** - vLLM OpenAI-compatible API server is accepting connections
3. **Model Loaded** - Model is loaded and ready to serve inference requests

This guide explains how to configure Kubernetes probes to ensure pods are only marked Ready when models are fully loaded and operational.

## Problem Statement

When deploying vLLM inference servers, there's a significant time gap between when the container starts and when the model is fully loaded. Using only basic health checks can lead to:

- Premature traffic routing to pods that aren't ready to serve requests
- Failed requests during model loading phase
- Need for arbitrary sleep times in deployment pipelines
- Unreliable E2E testing and CI/CD workflows

The vLLM `/health` endpoint only indicates that the server process is running, **not** that models are loaded and ready to serve.

## Solution: Model-Aware HTTP Probes

Use Kubernetes HTTP probes with vLLM's OpenAI-compatible API endpoints to implement model-aware readiness checking.

### Recommended Probe Configuration

```yaml
containers:
- name: vllm
  ports:
  - containerPort: 8000  # or 8200 for decode pods
    protocol: TCP

  # Startup Probe: Wait for model to load during initialization
  # Protects liveness/readiness probes from firing too early
  startupProbe:
    httpGet:
      path: /v1/models
      port: 8000
    initialDelaySeconds: 15    # Time before first probe
    periodSeconds: 30           # How often to probe during startup
    timeoutSeconds: 5           # HTTP request timeout
    failureThreshold: 60        # Max attempts (30s * 60 = 30min max startup time)

  # Liveness Probe: Is the server process alive?
  # Simple health check, restarts container if failing
  livenessProbe:
    httpGet:
      path: /health
      port: 8000
    periodSeconds: 10           # Check every 10s
    timeoutSeconds: 5
    failureThreshold: 3         # Restart after 3 failures

  # Readiness Probe: Is the model loaded and ready?
  # Controls traffic routing, removes from service if failing
  readinessProbe:
    httpGet:
      path: /v1/models
      port: 8000
    periodSeconds: 5            # Check frequently for fast recovery
    timeoutSeconds: 2
    failureThreshold: 3
```

### Port Configuration by Role

Different pod roles use different ports:

| Pod Role | Port | Description |
| ---------- | ------ | ------------- |
| Prefill | 8000 | Direct vLLM API access |
| Decode | 8200 | Proxied through sidecar (8200 → 8000) |
| Standalone | 8000 | Single-node deployments |

Always configure probes to match the pod's serving port.

## How It Works

### `/health` Endpoint

The `/health` endpoint provides a basic liveness check:

```bash
$ curl http://localhost:8000/health
{}
```

**Behavior:**

- Returns `200 OK` immediately when vLLM server starts
- Does **not** wait for model loading
- Use for `livenessProbe` only

### `/v1/models` Endpoint (OpenAI-Compatible)

The `/v1/models` endpoint is model-aware and indicates true readiness:

```bash
$ curl http://localhost:8000/v1/models
{
  "object": "list",
  "data": [
    {
      "id": "meta-llama/Llama-3.1-8B-Instruct",
      "object": "model",
      "created": 1704321600,
      "owned_by": "vllm"
    }
  ]
}
```

**Behavior:**

- Returns `503` or connection refused during model loading
- Returns `200 OK` with model metadata once ready
- Ideal for `startupProbe` and `readinessProbe`

### Probe Lifecycle

```text
Container Start
      ↓
[startupProbe on /v1/models]
  ↓ (30s intervals, up to 30min)
  ✓ Model loaded → Startup complete
      ↓
[livenessProbe on /health] ← Restarts if server crashes
[readinessProbe on /v1/models] ← Routes traffic when ready
```

## Benefits

### HTTP Probes vs Exec Probes

**HTTP Probes (Recommended):**

- ✅ Lightweight, no exec overhead
- ✅ Compatible with cloud load balancers
- ✅ Native Kubernetes integration
- ✅ Better observability and metrics
- ✅ Uses existing vLLM endpoints

**Exec Probes (Legacy):**

- ❌ Higher overhead (fork/exec per probe)
- ❌ Incompatible with many cloud load balancers
- ❌ Requires custom scripts in container
- ⚠️ More complex to debug and maintain

### For Production Deployments

- ✅ Prevent premature traffic routing
- ✅ Avoid failed requests during startup
- ✅ Enable safe rolling updates
- ✅ Faster detection of unhealthy pods
- ✅ Better integration with service meshes

### For E2E Testing

- ✅ Eliminate arbitrary sleep times
- ✅ Faster test execution
- ✅ More reliable test results
- ✅ Better error detection

## Examples

### Simulated Accelerator Deployment

Example from `guides/simulated-accelerators/ms-sim/values.yaml`:

```yaml
decode:
  replicas: 3
  containers:
  - name: vllm
    ports:
    - containerPort: 8200
      protocol: TCP

    startupProbe:
      httpGet:
        path: /v1/models
        port: 8200
      initialDelaySeconds: 15
      periodSeconds: 30
      timeoutSeconds: 5
      failureThreshold: 60

    livenessProbe:
      httpGet:
        path: /health
        port: 8200
      periodSeconds: 10
      timeoutSeconds: 5
      failureThreshold: 3

    readinessProbe:
      httpGet:
        path: /v1/models
        port: 8200
      periodSeconds: 5
      timeoutSeconds: 2
      failureThreshold: 3
```

### Wide Endpoint Deployment

Example from `guides/wide-ep-lws/manifests/modelserver/base/decode.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: vllm-decode
spec:
  containers:
  - name: vllm
    image: ghcr.io/llm-d/llm-d:latest
    ports:
    - containerPort: 8200
      protocol: TCP

    startupProbe:
      httpGet:
        path: /v1/models
        port: 8200
      initialDelaySeconds: 30
      periodSeconds: 10
      timeoutSeconds: 5
      failureThreshold: 60

    livenessProbe:
      httpGet:
        path: /health
        port: 8200
      periodSeconds: 30
      timeoutSeconds: 5
      failureThreshold: 3

    readinessProbe:
      httpGet:
        path: /v1/models
        port: 8200
      periodSeconds: 10
      timeoutSeconds: 5
      failureThreshold: 3
```

## Testing Probes

### Manual Testing

Test the endpoints directly in a running pod:

```bash
# Get pod name
POD=$(kubectl get pods -n llm-d -l app=vllm -o name | head -1)

# Test liveness endpoint
kubectl exec -n llm-d $POD -- curl -sf http://localhost:8000/health

# Test readiness endpoint
kubectl exec -n llm-d $POD -- curl -sf http://localhost:8000/v1/models | jq '.'
```

### Verification

Check probe status in Kubernetes:

```bash
# Watch pod readiness
kubectl get pods -n llm-d -w

# Check probe configuration
kubectl describe pod -n llm-d $POD | grep -A 10 "Liveness:"

# View probe-related events
kubectl get events -n llm-d --field-selector involvedObject.name=${POD##*/}
```

Expected behavior:

1. Pod starts, enters `Running` state
2. Startup probe checks `/v1/models` repeatedly (30s intervals)
3. Once model loads, startup probe succeeds
4. Readiness probe takes over, pod becomes `Ready`
5. Traffic is routed to pod
6. Liveness probe monitors server health continuously

## Troubleshooting

### Pod Stuck in Not Ready

```bash
# Check startup probe status
kubectl describe pod -n llm-d $POD | grep -A 5 "Startup:"

# Check if model is loading slowly
kubectl logs -n llm-d $POD | grep -i "loading model"

# Test endpoint manually
kubectl exec -n llm-d $POD -- curl -v http://localhost:8000/v1/models
```

**Common causes:**

- Model download taking longer than `failureThreshold` allows
- Insufficient resources (CPU/memory/GPU)
- Wrong port in probe configuration
- Network issues preventing model download

**Solutions:**

- Increase `failureThreshold` or `periodSeconds` in `startupProbe`
- Pre-download models using init containers or persistent volumes
- Verify pod has sufficient resources allocated
- Check probe configuration matches actual serving port

### Probe Failures After Startup

```bash
# Check recent probe failures
kubectl get events -n llm-d | grep -i probe

# Check pod logs for errors
kubectl logs -n llm-d $POD --tail=100
```

**Common causes:**

- vLLM server crashed (liveness probe fails)
- Model unloaded or corrupted (readiness probe fails)
- Resource exhaustion (OOM, GPU errors)
- Network connectivity issues

## Additional Resources

- [Kubernetes Probe Configuration](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
- [vLLM OpenAI-Compatible Server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [llm-d Getting Started](../README.md)
- [llm-d Monitoring Guide](monitoring/README.md)

## Related Issues

- [vLLM #6073](https://github.com/vllm-project/vllm/issues/6073) - Request for dedicated `/ready` endpoint
- vLLM currently relies on `/v1/models` for model-aware readiness checking
