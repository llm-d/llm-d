# Multimedia Downloader Proxy

A pluggable caching proxy designed to speed up the fetching of large multimedia assets like videos, images, and optionally machine learning models.

## Motivation

Multimodal LLM inference workloads constantly fetch heavy assets (images, videos) from remote origins. These repeated "cold" downloads introduce significant latency, spike egress costs, and expose the system to external rate limits and network jitter.

This is particularly impactful in llm-d's
[disaggregated serving](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/disaggregation.md)
configurations (E/P/D). In an encode-prefill-decode split, dedicated encode
workers, prefill pods, and decode pods are scheduled independently and may each
fetch the same multimedia asset from the origin on the same request — amplifying
egress costs and download latency proportionally to the number of disaggregated
components. A shared cluster-local cache eliminates this redundancy: the first
component to request an asset pays the origin fetch cost; every subsequent
request across all components in the llm-d ecosystem is served from cache.

## Supported Implementations

The proxy is designed to support different caching backends, which are maintained in the [`implementations/`](implementations/) directory. Each backend manages its own specific configuration and resource footprints.

* **[Squid](https://github.com/squid-cache/squid) (Default):** A robust, high-performance HTTP/HTTPS caching proxy with two variants:
  * **[http](implementations/squid/http/)** — HTTP caching proxy. HTTPS requests are not cached; they are tunneled through an opaque `CONNECT` tunnel with no content inspection.
  * **[https-ssl-bump](implementations/squid/https-ssl-bump/)** — HTTPS caching proxy. Requires a custom CA certificate and a custum docker image.

  For setup instructions see the [Squid Implementation Guide](implementations/squid/README.md).

## Quick Start

### Prerequisites

* **[kind](https://kind.sigs.k8s.io/)**
* **`kubectl`**

### 1. Create a kind Cluster

```bash
kind create cluster --name multimedia-test --wait 90s
```

### 2. Deploy the Proxy
Deploy the default implementation to the newly created cluster using Kustomize:

```bash
kubectl apply -k helpers/multimedia-downloader
```

### 3. Wait for the Deployment to Be Ready

```bash
kubectl rollout status deployment/multimedia-downloader
```

### 4. Verify It Works

Send a request through the proxy and check the logs:

```bash
kubectl run curl-test --image=curlimages/curl:8.11.1 --restart=Never --rm -it -- \
  curl -s -x http://multimedia-downloader:80 http://images.cocodataset.org/val2017/000000039769.jpg -o /dev/null -w "%{http_code}\n"
```

Check the cache log (run twice to see `TCP_MISS` → `TCP_HIT`):

```bash
kubectl logs -l app=multimedia-downloader --tail=5
```

> Note: For a detailed breakdown of what TCP_MISS, TCP_HIT, and other log statuses mean, see the [Squid Implementation Guide](implementations/squid/README.md).

### 5. Cleanup

```bash
kind delete cluster --name multimedia-test
```

## Usage: Routing Application Traffic

Once the proxy is running, you must configure your downstream applications to route their downloads through it. Set the standard proxy environment variables in your client deployments:

```bash
export HTTP_PROXY=http://multimedia-downloader:80
export HTTPS_PROXY=http://multimedia-downloader:80
export NO_PROXY=localhost,127.0.0.1,.svc,.cluster.local
```

- `HTTP_PROXY` — Routes unencrypted web traffic
- `HTTPS_PROXY` — Routes secure, encrypted web traffic
- `NO_PROXY` — Bypasses the proxy for specific internal hosts or domains

For Python applications:
```python
import os
os.environ['HTTP_PROXY'] = 'http://multimedia-downloader:80'
os.environ['HTTPS_PROXY'] = 'http://multimedia-downloader:80'
os.environ['NO_PROXY'] = 'localhost,127.0.0.1,.svc,.cluster.local'
```

## Configuration

### Base Configuration

The base directory contains:
- [service.yaml](service.yaml) - Base service definition (port 80, `targetPort: http-proxy`)
- [kustomization.yaml](kustomization.yaml) - References the service and selected implementation

Regardless of the backend's internal port, the proxy always listens on port 80. Therefore, when configuring HTTP_PROXY or HTTPS_PROXY for clients, you must specify port 80.

### Implementation-Specific Configuration

Each implementation lives under `implementations/<name>/` and may contain one or more variant subdirectories. Each variant has its own:
- `deployment.yaml` - Kubernetes deployment; must expose a container port named `http-proxy`
- `kustomization.yaml` - Kustomize configuration

The base service uses the named port `http-proxy` as its `targetPort`. Variants resolve this automatically by naming their container port `http-proxy` — no service patch is required.

## Adding New Implementations

To add a new cache implementation:

1. Create a variant directory under `implementations/`

2. Add variant-specific files:
   - `deployment.yaml` - Your proxy deployment; name the container port `http-proxy` (the base service resolves `targetPort: http-proxy` automatically)
   - `kustomization.yaml` - List resources and labels

3. Modify the base `kustomization.yaml` to point to any new default implementation.
