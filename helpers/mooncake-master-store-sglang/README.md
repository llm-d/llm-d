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
  the shared helper uses `ghcr.io/llm-d/mooncake-master-store:v0.8.0`, whose tag
  is unrelated to any mooncake release.

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
protocol, so both must come from the same mooncake release. SGLang's own
`docker/Dockerfile` decides the client version, and Mooncake publishes an
official image for each release, so the master image tag simply follows it:

| SGLang image tag | mooncake client it installs | master image to use |
|---|---|---|
| `v0.5.22` | `0.3.13.post1` | `docker.io/kvcacheai/mooncake:0.3.13.post1` |

To re-pin after changing the SGLang tag in
[guides/recipes/modelserver/components/images/gpu-sglang/release/kustomization.yaml](../../guides/recipes/modelserver/components/images/gpu-sglang/release/kustomization.yaml),
read the new `MOONCAKE_VERSION` out of SGLang's Dockerfile at that tag:

```bash
curl -fsSL https://raw.githubusercontent.com/sgl-project/sglang/<tag>/docker/Dockerfile | grep MOONCAKE_VERSION
```

then set `image:` in [base/deployment.yaml](./base/deployment.yaml) to
`kvcacheai/mooncake:<that version>`. Use the exact version, including any
`.postN` suffix: `0.3.13` and `0.3.13.post1` are different releases.

## Cluster-specific settings to review

- `nodeSelector: scale=true` in [base/deployment.yaml](./base/deployment.yaml)
  pins the master to nodes running the Spectrum Scale CSI driver, which
  provisions the snapshots PVC on the cluster this was developed on. Change or
  remove it if your snapshot volume uses a different storage class.

## Snapshots and file ownership

The image runs as a non-root user (uid 65532, or a namespace-assigned uid on
OpenShift), and the master writes snapshots under `/data/snapshots` on the PVC
every `snapshot_interval_seconds`. If the PVC already holds directories created
by a master that ran as root, the new master cannot write into them and logs
`Permission denied` on every snapshot, while RPC keeps working. Start from a
fresh PVC in that case. To confirm snapshots succeed, check that the master's
periodic metrics line reports `Snapshots: Success=<n>, Fail=0`:

```bash
kubectl logs deploy/mooncake-master-store -n ${NAMESPACE} | grep -o 'Snapshots: [^}]*' | tail -1
```
