# Customizing your Gateway

This document is meant to walk through some choices when setting up your gateway.

## Using an Ingress

If using a gateway service of type `ClusterIP` you have the option to create an ingress to expose your gateway

```yaml
gateway:
  service:
    type: ClusterIP
ingress:
  enabled: true
  ingressClassName: traefik
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
```

## Gateway Considerations for Benchmarking

In our experience there are a few gotchas we encountered when attempting to benchmark for competitive numbers, this document is meant to capture those gateway related challenges we encountered and workarounds we derived.

### Increasing Envoy Pod Resources

To remove the chance that the envoy pod can be a bottle neck, we thought it might be a good idea to increase its resource.  You can see how to set this from our [benchmarking values file](../guides/prereq/gateway-provider/common-configurations/benchmarking.yaml).

This option gets exposed from the `llm-d-infra` chart by either a [gatewayParameters manifest](https://github.com/llm-d-incubation/llm-d-infra/blob/main/charts/llm-d-infra/templates/gateway-infrastructure/gatewayparameters.yaml) if you are using Kgateway, or a [configmap](https://github.com/llm-d-incubation/llm-d-infra/blob/main/charts/llm-d-infra/templates/gateway-infrastructure/configmap.yaml) if you are using Istio.

### Increasing Max Connections and Timeout (Istio only)

Currently we only have a workaround for increasing max connections and timeout for Istio, we would like to expand this to other providers in the future. This is provided via the Istio destination rule, you can see a values configuration for this in our [benchmarking values file](../guides/prereq/gateway-provider/common-configurations/benchmarking.yaml).

This gets exposed by the [`DestinationRule` template](https://github.com/llm-d-incubation/llm-d-infra/blob/main/charts/llm-d-infra/templates/gateway-infrastructure/destinationrule.yaml) in the `llm-d-infra` charts, but we hope this manifest will make its way upstream to the GAIE charts.

### Changing log levels

In an effort to reduce the workload of the envoy gateway container, we have dropped the logs to level error, only showing us critical issues. Again this is visible in the [benchmarking values file](../guides/prereq/gateway-provider/common-configurations/benchmarking.yaml).
