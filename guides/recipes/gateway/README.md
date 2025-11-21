# Gateway Recipes

This directory contains recipes for deploying the `llm-d-inference-gateway` and `llm-d-route`.

## Installation

The following recipes are available for deploying the gateway and httproute.

<!-- TABS:START -->

<!-- TAB:GKE L7 Regional External Managed:default -->
### GKE L7 Regional External Managed  
This deploys a gateway suitable for GKE, using the `gke-l7-regional-external-managed` gateway class.

```bash
kubectl apply -k ./gke-l7-regional-external-managed -n ${NAMESPACE}
```

<!-- TAB:Istio -->
### Istio
This deploys a gateway suitable for Istio, using the `istio` gateway class.

```bash
kubectl apply -k ./istio -n ${NAMESPACE}
```

<!-- TAB:KGateway -->
### KGateway
This deploys a gateway suitable for KGateway, using the `kgateway` gateway class.

```bash
kubectl apply -k ./kgateway -n ${NAMESPACE}
```

<!-- TAB:KGateway (OpenShift) -->
### KGateway (OpenShift)
 
This deploys a gateway suitable for OpenShift, using the `openshift` gateway class.

```bash
kubectl apply -k ./kgateway-openshift -n ${NAMESPACE}
```

<!-- TABS:END -->

## Verification

You can verify the installation by checking the status of the created resources.

### Check the Gateway

```bash
kubectl get gateway -n ${NAMESPACE}
```

You should see output similar to the following, with the `PROGRAMMED` status as `True`. The `CLASS` will vary depending on the recipe you deployed.

```text
NAME                      CLASS                              ADDRESS     PROGRAMMED   AGE
llm-d-inference-gateway   gke-l7-regional-external-managed   <redacted>  True         1m
```

### Check the HTTPRoute

```bash
kubectl get httproute -n ${NAMESPACE}
```

```text
NAME          HOSTNAMES   AGE
llm-d-route               1m
```
