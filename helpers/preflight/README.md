# Preflight

Checks, before you deploy a guide, whether your cluster has what the guide
needs. It is read-only: it renders the guide's model-server overlay with
`kubectl kustomize` and reads the cluster with `kubectl get`. Nothing is
created or changed.

Supported today: `guides/wide-ep`, model `vllm-deepseek-r1-0528`, overlays
`gke`, `coreweave`, `base`, `dgx-cloud-gb200`. Other overlays that render a
`DisaggregatedSet` run best-effort: claims the render doesn't define are
reported as not checked. Overlays without one, or with unrendered placeholders,
exit with code 2.

## Run

Needs a recent `kubectl` (the overlays use kustomize components, which its built-in
kustomize renders) and Python 3 with PyYAML (`python3 -m pip install pyyaml`).

```bash
python3 helpers/preflight/preflight.py guides/wide-ep \
  --overlay modelserver/gpu/vllm-deepseek-r1-0528/gke
```

Options: `--context NAME` to check another kubeconfig context,
`--gateway-mode` if the router runs behind a Gateway, `--output json`.
With [uv](https://docs.astral.sh/uv/) you can run `./helpers/preflight/preflight.py` directly.

Exit code: `0` no FAIL, `1` at least one FAIL, `2` the check itself couldn't run.

## What it checks

| Check | Source |
| --- | --- |
| APIs and CRDs the overlay and the router need | render + `crds` in `preflight.yaml` |
| LWS controller version, DisaggregatedSet webhook | Deployment image, validating webhooks |
| RDMA: DRA DeviceClasses and published devices (GKE), `rdma/ib` (CoreWeave) | render, DeviceClasses, ResourceSlices, node allocatable |
| GPU driver major version | ResourceSlice `driverVersion`, GPU feature discovery labels, GKE annotations |
| Enough nodes for every model-server pod (GPU, CPU, memory, ephemeral-storage, devices) | render + node allocatable, taints |
| A CPU node for the router | `preflight.yaml` router size |

A check that can't be performed (missing permission, unknown version format)
is a WARN, not a FAIL.

Not checked: free capacity (pods already running are not subtracted), memory
use at decode time, RDMA on overlays whose manifests don't request an RDMA
resource, and actual RDMA connectivity between pods (see
[RDMA and Networking Configuration](../../docs/infrastructure/rdma/README.md)).
DRA devices are counted per driver, without evaluating each DeviceClass's
CEL filter, so partitioned devices (for example MIG) may be over-counted.

## Adding a guide

Add `<guide>/preflight.yaml` with what can't be read from the manifests,
under `requirements.cluster` (see `guides/wide-ep/preflight.yaml`). That is the
block a `guide.yaml` `requirements:` section would hold, so it can move there
unchanged. The overlay must render a `DisaggregatedSet`.

## Tests

```bash
python -m pytest helpers/preflight/tests/ -v
```

If a test reports a stale render fixture, regenerate it with the command it prints.
