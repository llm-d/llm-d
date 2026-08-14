<!--
    Prose is free-form; scripts/guide.py only touches the bash code
    blocks between paired HTML-comment markers naming YAML paths.
-->

# <Guide Title>

## Overview

<!-- 1–2 sentences: what this guide deploys. -->

## Prerequisites

Source the shared environment variables:

<!-- guide:env.source start -->
```bash
source ${REPO_ROOT}/guides/env.sh
```
<!-- guide:env.source end -->

Set the guide's variables (replace `HF_TOKEN_PLACEHOLDER` with a real HuggingFace token):

<!-- guide:env.static start -->
```bash
export GUIDE_NAME=<guide-name>
export NAMESPACE=llm-d-<guide-name>
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export MODEL=<HuggingFace/Model-Name>
export HF_TOKEN=HF_TOKEN_PLACEHOLDER
```
<!-- guide:env.static end -->

Create the namespace and required secrets:

<!-- guide:prerequisites start -->
```bash
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites end -->

## Installation

### Standalone Mode

<!-- guide:deploy.standalone start -->
```bash
helm install ${GUIDE_NAME} <chart> -n ${NAMESPACE}
```
<!-- guide:deploy.standalone end -->

### Gateway Mode

<!-- guide:deploy.gateway start -->
```bash
helm install ${GUIDE_NAME} <chart> -n ${NAMESPACE}
```
<!-- guide:deploy.gateway end -->

## Verification

Get the endpoint IP for your chosen mode:

<!-- guide:verify.endpoint.standalone start -->
```bash
export IP=$(kubectl get service <svc> -n ${NAMESPACE} -o jsonpath='{.spec.clusterIP}')
```
<!-- guide:verify.endpoint.standalone end -->

<!-- guide:verify.endpoint.gateway start -->
```bash
export IP=$(kubectl get gateway <gw> -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```
<!-- guide:verify.endpoint.gateway end -->

Send a test request:

<!-- guide:verify.tests start -->
```bash
curl -sS "http://${IP}/v1/completions" -d '{"model":"'"${MODEL}"'","prompt":"hi"}'
```
<!-- guide:verify.tests end -->

## Cleanup

<!-- guide:cleanup start -->
```bash
helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
```
<!-- llm-d-cicd:skip start -->
```bash
kubectl delete namespace ${NAMESPACE}
```
<!-- llm-d-cicd:skip end -->
<!-- guide:cleanup end -->
