# Mooncake Master Store (per-namespace, SGLang)

MooncakeStore's metadata service, deployed **once per consuming namespace**
rather than once per cluster. Used by the SGLang HiCache + RoCE/SR-IOV path in
[guides/tiered-prefix-cache](../../guides/tiered-prefix-cache/).

## Why this exists separately from `../mooncake-master-store/`

That helper pins `namespace: mooncake` so a single master serves many
model-server namespaces, and its vLLM consumers address it by the fully
qualified `mooncake-master-store.mooncake.svc.cluster.local:50051`. This copy
differs in two ways that can't be expressed as an overlay on it:

- **It pins no namespace**, so the master lands in whatever namespace you pass
  to `kubectl -n` — beside the decode pods that use it, which is why the
  SGLang config addresses it as the bare `mooncake-master-store:50051`. A
  kustomize overlay can override a base's `namespace:` value but cannot remove
  the transformation, so an unpinned result needs its own base.
- **It pins a mooncake version matched to the SGLang image** (see below), where
  the shared helper uses `ghcr.io/llm-d/mooncake-master-store:v0.8.0`.

The tradeoff is an isolated cache and a separate DRAM/PVC footprint per
namespace, instead of one pool shared across tenants.

## Usage

```bash
kubectl apply -k ${REPO_ROOT}/helpers/mooncake-master-store-sglang/monitoring/ -n ${NAMESPACE}
```

Use `base/` instead of `monitoring/` if you don't have the Prometheus Operator
— `monitoring/` only adds a `ServiceMonitor` for the master's metrics on port
9003.

The `-n` flag is required. Nothing here hardcodes a namespace, so without it
the master goes to your current context's namespace.

## Keeping the master and client versions matched

The master and the mooncake client inside the model server speak a versioned
protocol, so the two must be built from the same mooncake release. SGLang's
own `docker/Dockerfile` decides the client version:

| SGLang image tag | mooncake client it installs | master image to use |
|---|---|---|
| `v0.5.16` | `0.3.11.post1` | `ghcr.io/amit-berman/mooncake-master:0.3.11.post1` |
| `v0.5.19` | `0.3.13` | needs rebuilding — see below |

The tag on the shared helper's image (`v0.8.0`) is an image version unrelated
to any mooncake release, which is why this path builds its own from
[Dockerfile](./Dockerfile) instead.

To re-pin after changing the SGLang tag in
[guides/recipes/modelserver/components/images/gpu-sglang/release/kustomization.yaml](../../guides/recipes/modelserver/components/images/gpu-sglang/release/kustomization.yaml),
read the new `MOONCAKE_VERSION` out of SGLang's Dockerfile at that tag:

```bash
curl -fsSL https://raw.githubusercontent.com/sgl-project/sglang/<tag>/docker/Dockerfile | grep MOONCAKE_VERSION
```

then rebuild and update `image:` in [base/deployment.yaml](./base/deployment.yaml):

```bash
docker build --build-arg MOONCAKE_VERSION=<version> \
  -t <your-registry>/mooncake-master:<version> \
  ${REPO_ROOT}/helpers/mooncake-master-store-sglang/
```

The Dockerfile installs the `-non-cuda` wheel because the master is
metadata-only and needs no GPU; SGLang installs the CUDA build of the same
release for the client side.

## Cluster-specific settings to review

Two things in [base/deployment.yaml](./base/deployment.yaml) are specific to
the cluster this was developed on and likely need adjusting elsewhere:

- `nodeSelector: scale=true` — pins the master to nodes running the Spectrum
  Scale CSI driver, which provisions the snapshots PVC. Change or remove it if
  your snapshot volume uses a different storage class.
- `securityContext.runAsUser: 0` — required by the image as built. Drop it if
  you rebuild the image to run unprivileged.
