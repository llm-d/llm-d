# Installing Gateway API CRDs

Before deploying any Gateway provider, you must install the Gateway API and Gateway API Inference Extension CRDs.

> [!NOTE]
> GKE automatically installs all GA CRDs for Gateway API and Gateway API Inference Extension on GKE versions `1.34.0-gke.1626000` or later. If using GKE with this version or newer, you can skip the installation steps below.

## Option 1: Using the Installation Script

The quickest way to install the CRDs is using the provided script:

```bash
bash guides/recipes/gateway/install-gateway-crds.sh
```

This script installs both Gateway API and Gateway API Inference Extension CRDs.

## Option 2: Manual Installation

If you prefer to install manually or need to customize versions:

```bash
GATEWAY_API_VERSION=v1.5.1
GAIE_VERSION=v1.5.0
GATEWAY_API_INSTALL_URL=https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml
GAIE_INSTALL_URL=https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml

kubectl apply -f "${GATEWAY_API_INSTALL_URL}"
kubectl apply -f "${GAIE_INSTALL_URL}"
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

The quickest way to uninstall the CRDs is using the provided script:

```bash
bash guides/recipes/gateway/install-gateway-crds.sh delete
```

### Option 2: Manual Uninstall

If you prefer to uninstall manually:

```bash
GATEWAY_API_VERSION=v1.5.1
GAIE_VERSION=v1.5.0

kubectl delete -f https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml
```
