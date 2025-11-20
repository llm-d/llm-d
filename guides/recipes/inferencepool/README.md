import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# InferencePool Recipe

This directory contains a standard `values.yaml` for deploying an `InferencePool`.

## Install InferencePool {#install-inferencepool}

To deploy the `InferencePool`, select your provider below.

<Tabs>
  <TabItem value="gke" label="GKE" default>
    This command deploys the `InferencePool` on GKE with GKE-specific monitoring enabled.

    ```bash
    helm install llm-d-infpool \
      -n ${NAMESPACE} \
      -f ./values.yaml \
      --set "provider.name=gke" \
      --set "inferencePool.apiVersion=inference.networking.k8s.io/v1" \
      --set "inferenceExtension.monitoring.gke.enable=true" \
      oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool \
      --version v1.0.1
    ```
  </TabItem>
  <TabItem value="istio" label="Istio">
    This command deploys the `InferencePool` with Istio, enabling Prometheus monitoring.

    ```bash
    helm install llm-d-infpool \
      -n ${NAMESPACE} \
      -f ./values.yaml \
      --set "provider.name=istio" \
      --set "inferenceExtension.monitoring.prometheus.enable=true" \
      oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool \
      --version v1.0.1
    ```
  </TabItem>
  <TabItem value="kgateway" label="KGateway">
    This command deploys the `InferencePool` with Kgateway.

    ```bash
    helm install llm-d-infpool \
      -n ${NAMESPACE} \
      -f ./values.yaml \
      --set "provider.name=kgateway" \
      oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool \
      --version v1.0.1
    ```
  </TabItem>
</Tabs>

## Verify InferencePool Installation {#verify-inferencepool-installation}

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

## Cleanup InferencePool {#cleanup-inferencepool}

To remove the `InferencePool`, use the following command:

```bash
helm uninstall llm-d-infpool -n ${NAMESPACE}
```

