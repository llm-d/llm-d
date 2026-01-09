# vLLM Recipe

This directory contains recipes for deploying a vLLM model server.

## Installation

The following recipes are available for deploying the vLLM model server.

### Standard

This deploys a standard vLLM model server with default configuration.

```bash
kubectl apply -k ./standard -n ${NAMESPACE}
```

This is an overlay of the base recipe. You can create additional overlays to provide specific arguments and resource requests for your model by referencing the base:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../base
patches:
  - target:
      kind: Deployment
      name: llm-d-model-server
    patch: |-
      # Your customizations here
```
