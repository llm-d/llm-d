# Installing Gateway API CRDs

Before deploying any Gateway provider, you must install the Gateway API and Gateway API Inference Extension CRDs.

> [!NOTE]
> On GKE `1.34.0-gke.1626000` or later **with the Gateway API enabled** (`--gateway-api=standard`; always on
> in Autopilot), GKE installs and manages the Gateway API Standard-channel CRDs and the v1 `InferencePool` CRD,
> and you can skip the steps below. A cluster without the Gateway API enabled has neither, whatever its version.
> The release GKE manages can trail the one the guides pin (`latest` via `guides/env.sh`); see
> [GKE Inference Gateway](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway)
> for what GKE manages before applying a newer `InferencePool` CRD yourself.

## Option 1: Using the Installation Script

The quickest way to install the CRDs is using the provided script. It reads `GATEWAY_API_VERSION` and
`GAIE_VERSION` from the environment, so source `guides/env.sh` first to install the same release the guides
use (`latest` by default; export either variable before sourcing to pin a release):

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh
bash ${REPO_ROOT}/guides/recipes/gateway/install-gateway-crds.sh
```

This script installs both Gateway API and Gateway API Inference Extension CRDs.

## Option 2: Manual Installation

If you prefer to install manually, `guides/env.sh` turns `GATEWAY_API_VERSION` and `GAIE_VERSION` into the
release URL fragments the guides use (`releases/latest/download` by default, `releases/download/<tag>` when
pinned):

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
source ${REPO_ROOT}/guides/env.sh   # or export GATEWAY_API_VERSION / GAIE_VERSION first to pin

kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api/${GATEWAY_API_URL}/standard-install.yaml"
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml"
```

## Verification

Verify the APIs are available:

```bash
kubectl api-resources --api-group=gateway.networking.k8s.io
kubectl api-resources --api-group=inference.networking.k8s.io
```

You should see resources like `gateways`, `httproutes`, and `inferencepools`.

## Uninstalling Gateway API CRDs

### Option 1: Using the Installation Script

The quickest way to uninstall the CRDs is using the provided script, with the same environment you installed with:

```bash
bash ${REPO_ROOT}/guides/recipes/gateway/install-gateway-crds.sh delete
```

### Option 2: Manual Uninstall

If you prefer to uninstall manually, with the same environment you installed with:

```bash
kubectl delete -f "https://github.com/kubernetes-sigs/gateway-api/${GATEWAY_API_URL}/standard-install.yaml"
kubectl delete -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml"
```
