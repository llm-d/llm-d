import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Gateway Recipes

This directory contains recipes for deploying the `llm-d-inference-gateway` and `llm-d-route`.

## Installation

Choose the installation method that matches your deployment pattern:

### Install Gateway and HTTPRoute

Use this when deploying both Gateway and HTTPRoute via Kustomize (e.g., `wide-ep-lws`, `prefix-cache-storage`).

<Tabs>
    <TabItem value="gke" label="GKE L7 Regional External Managed" default>
        This deploys a gateway suitable for GKE, using the `gke-l7-regional-external-managed` gateway class.

        ```bash
        kubectl apply -k ./gke-l7-regional-external-managed -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="istio" label="Istio">
        This deploys a gateway suitable for Istio, using the `istio` gateway class.

        ```bash
        kubectl apply -k ./istio -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="kgateway" label="KGateway">
        This deploys a gateway suitable for KGateway, using the `kgateway` gateway class.

        ```bash
        kubectl apply -k ./kgateway -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="kgateway-ocp" label="KGateway (OpenShift)">
        This deploys a gateway suitable for OpenShift, using the `openshift` gateway class.

        ```bash
        kubectl apply -k ./kgateway-openshift -n ${NAMESPACE}
        ```
    </TabItem>
</Tabs>

### Install HTTPRoute Only

Use this when the Gateway is deployed via Helmfile (infra chart) and you only need to deploy the HTTPRoute separately. This is common in guides like `pd-disaggregation`, `simulated-accelerators`, `inference-scheduling`, and `precise-prefix-cache-aware`.

**Note:** When using this pattern, ensure your HTTPRoute YAML references the correct Gateway name (typically `infra-*-inference-gateway` created by the helmfile).

<Tabs>
    <TabItem value="kgateway-istio" label="KGateway or Istio">
        ```bash
        kubectl apply -f httproute.yaml -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="gke" label="GKE">
        ```bash
        kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
        ```
    </TabItem>
</Tabs>

## Verification

### Verify Gateway Installation

You can verify the Gateway installation by checking its status:

```bash
kubectl get gateway -n ${NAMESPACE}
```

You should see output similar to the following, with the `PROGRAMMED` status as `True`. The `CLASS` will vary depending on the recipe you deployed.

```text
NAME                      CLASS                              ADDRESS     PROGRAMMED   AGE
llm-d-inference-gateway   gke-l7-regional-external-managed   <redacted>  True         1m
```

**Note:** If your Gateway was deployed via Helmfile, the name will be different (e.g., `infra-*-inference-gateway`).

### Verify HTTPRoute Installation

You can verify the HTTPRoute installation by checking its status:

```bash
kubectl get httproute -n ${NAMESPACE}
```

You should see output similar to the following:

```text
NAME          HOSTNAMES   AGE
llm-d-route               1m
```

**Note:** The HTTPRoute name will vary depending on your deployment. If deployed via guide-specific YAML, it may have a different name (e.g., `llm-d-pd-disaggregation`).

## Cleanup

### Cleanup Gateway and HTTPRoute

Use this when both Gateway and HTTPRoute were deployed via Kustomize:

<Tabs>
    <TabItem value="gke" label="GKE L7 Regional External Managed" default>
        ```bash
        kubectl delete -k ./gke-l7-regional-external-managed -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="istio" label="Istio">
        ```bash
        kubectl delete -k ./istio -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="kgateway" label="KGateway">
        ```bash
        kubectl delete -k ./kgateway -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="kgateway-ocp" label="KGateway (OpenShift)">
        ```bash
        kubectl delete -k ./kgateway-openshift -n ${NAMESPACE}
        ```
    </TabItem>
</Tabs>

### Cleanup HTTPRoute Only

Use this when only the HTTPRoute was deployed separately (Gateway deployed via Helmfile):

<Tabs>
    <TabItem value="kgateway-istio" label="KGateway or Istio">
        ```bash
        kubectl delete -f httproute.yaml -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="gke" label="GKE">
        ```bash
        kubectl delete -f httproute.gke.yaml -n ${NAMESPACE}
        ```
    </TabItem>
    <TabItem value="digitalocean" label="DigitalOcean">
        ```bash
        kubectl delete -f httproute.yaml -n ${NAMESPACE}
        ```
    </TabItem>
</Tabs>

**Note:** When using this pattern, the Gateway is typically cleaned up via `helmfile destroy` or `helm uninstall` commands, not through this recipe.
