# InferencePool Recipe

This directory contains a standard `values.yaml` for deploying an `InferencePool`.

## Installation

To deploy the `InferencePool`, select your provider below.

> [!NOTE]
> Prefer `agentgateway` for new self-installed inference deployments. The current Gateway API Inference Extension chart uses `provider.name=none` for both `agentgateway` and the deprecated `kgateway` migration path. See the upstream [`inferencepool` chart values for v1.4.0](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/config/charts/inferencepool/values.yaml).

<!-- TABS:START -->

<!-- TAB:GKE:default -->
### GKE

This command deploys the `InferencePool` on GKE with GKE-specific monitoring enabled.

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./values.yaml \
  --set "provider.name=gke" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.4.0
```

<!-- TAB:Istio -->
### Istio

This command deploys the `InferencePool` with Istio, enabling Prometheus monitoring.

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./values.yaml \
  --set "provider.name=istio" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.4.0
```

<!-- TAB:Agentgateway -->
### Agentgateway

This command deploys the `InferencePool` for `agentgateway`.

```bash
helm install llm-d-infpool \
  -n ${NAMESPACE} \
  -f ./values.yaml \
  --set "provider.name=none" \
  oci://registry.k8s.io/gateway-api-inference-extension/charts/inferencepool \
  --version v1.4.0
```

<!-- TABS:END -->

## Verification

You can verify the installation by checking the status of the created resources.

### Check the InferencePool

```bash
kubectl get inferencepool -n ${NAMESPACE}
```

```text
NAME            AGE
llm-d-infpool   1m
```

### Check the Pods

```bash
kubectl get pods -l app.kubernetes.io/instance=llm-d-infpool -n ${NAMESPACE}
```

You should see the InferencePool's endpoint pod in a `Running` state.

```text
NAME                                  READY   STATUS    RESTARTS   AGE
llm-d-infpool-epp-xxxxxxxx-xxxxx     1/1     Running   0          1m
```

## Cleanup

To remove the `InferencePool`, use the following command:

```bash
helm uninstall llm-d-infpool -n ${NAMESPACE}
```
