# Well-lit Path: WEKA GPU Direct

Full doc coming soon, this is just meant to track the changes in this PR for now.

1. Add a readiness prob script to the `llm-d` image to detect if the cufile exists on the node and is properly formatted
2. InitContainer added to run `amgctl` to create the cufile on the node (implemented for decode & prefill)
3. Mount the cufile.json from /opt/weka/amg/cufile.json on the host to /etc/cufile.json on the container

## Usage

```bash
export NAMESPACE=weka
helm install llama-3-3-70b-instruct-fp8-dynamic \
    -n ${NAMESPACE} \
    -f inferencepool.values.yaml \
    oci://us-central1-docker.pkg.dev/k8s-staging-images/gateway-api-inference-extension/charts/inferencepool --version v0.5.1
```

## Refactors in Progress

- needs to add in nvidia GDS packages to dockerfile
- needs to refactor this into the PD example with overlays but for dev work we are keeping it in its own directory

## How the kustomize bits work

Navigate to an overlay and build from there, which will replace the volume for `weka-storage` with either `pvc` or `hostStorage` based on which dir you use.
