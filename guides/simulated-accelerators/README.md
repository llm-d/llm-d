# Feature: llm-d Accelerator Simulation

## Overview

Conducting large scale testing of AI/ML workloads is difficult when capacity is limited or already committed to production workloads. `llm-d` provides a lightweight model server that mimics the behavior of executing inference without requiring an attached accelerator. This simulated server can be run in wide or dense configurations on CPU-only machines to validate the correct behavior of other parts of the system, including Kubernetes autoscaling and the `inference-scheduler`.

This guide demonstrates how to deploy the simulator `ghcr.io/llm-d/llm-d-inference-sim` image and generate inference responses.

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md).
- Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.

**_NOTE:_** Unlike other examples which require models, the simulator stubs the vLLM server and so no HuggingFace token is needed.

## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-sim` in this example.

```bash
export NAMESPACE=llm-d-sim # Or any namespace your heart desires
kubectl create namespace ${NAMESPACE}

# Clone the repo and switch to the latest release tag 
tag=$(curl -s https://api.github.com/repos/llm-d/llm-d/releases/latest | jq -r '.tag_name')
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout "$tag"

cd guides/simulated-accelerators
helmfile apply -n ${NAMESPACE}
```

**_NOTE:_** You can set the `$RELEASE_NAME_POSTFIX` env variable to change the release names. This is how we support concurrent installs. ex: `RELEASE_NAME_POSTFIX=sim-2 helmfile apply -n ${NAMESPACE}`

**_NOTE:_** This uses Istio as the default provider, see [Gateway Options](./README.md#gateway-options) for installing with a specific provider.

### Gateway options

To see specify your gateway choice you can use the `-e <gateway option>` flag, ex:

```bash
helmfile apply -e kgateway -n ${NAMESPACE}
```

To see what gateway options are supported refer to our [gateway provider prereq doc](../prereq/gateway-provider/README.md#supported-providers). Gateway configurations per provider are tracked in the [gateway-configurations directory](../prereq/gateway-provider/common-configurations/).

You can also customize your gateway, for more information on how to do that see our [gateway customization docs](../../docs/customizing-your-gateway.md).

### Install HTTPRoute

Follow provider specific instructions for installing HTTPRoute.

#### Install for "kgateway" or "istio"

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
```

#### Install for "gke"

```bash
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

## Verify the Installation

- Firstly, you should be able to list all helm releases to view the 3 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME        NAMESPACE   REVISION   UPDATED                               STATUS     CHART                       APP VERSION
gaie-sim    llm-d-sim   1          2025-08-24 11:44:26.88254 -0700 PDT   deployed   inferencepool-v1.0.1        v1.0.1
infra-sim   llm-d-sim   1          2025-08-24 11:44:23.11688 -0700 PDT   deployed   llm-d-infra-v1.3.3          v0.3.0
ms-sim      llm-d-sim   1          2025-08-24 11:44:32.17112 -0700 PDT   deployed   llm-d-modelservice-v0.2.9   v0.2.0
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                     READY   STATUS    RESTARTS   AGE
pod/gaie-sim-epp-694bdbd44c-4sh92                        1/1     Running   0          7m14s
pod/infra-sim-inference-gateway-istio-68d59c4778-n6n5l   1/1     Running   0          7m19s
pod/ms-sim-llm-d-modelservice-decode-674774f45d-hhlxl    2/2     Running   0          7m10s
pod/ms-sim-llm-d-modelservice-decode-674774f45d-p5lsx    2/2     Running   0          7m10s
pod/ms-sim-llm-d-modelservice-decode-674774f45d-zpp84    2/2     Running   0          7m10s
pod/ms-sim-llm-d-modelservice-prefill-76c86dd9f8-pvbzm   1/1     Running   0          7m10s

NAME                                        TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)                        AGE
service/gaie-sim-epp                        ClusterIP      10.16.0.143   <none>        9002/TCP,9090/TCP              7m14s
service/gaie-sim-ip-207d1d4c                ClusterIP      None          <none>        54321/TCP                      7m14s
service/infra-sim-inference-gateway-istio   LoadBalancer   10.16.1.112   10.16.4.2     15021:33302/TCP,80:31413/TCP   7m19s

NAME                                                READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-sim-epp                        1/1     1            1           7m14s
deployment.apps/infra-sim-inference-gateway-istio   1/1     1            1           7m19s
deployment.apps/ms-sim-llm-d-modelservice-decode    3/3     3            3           7m10s
deployment.apps/ms-sim-llm-d-modelservice-prefill   1/1     1            1           7m10s

NAME                                                           DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-sim-epp-694bdbd44c                        1         1         1       7m15s
replicaset.apps/infra-sim-inference-gateway-istio-68d59c4778   1         1         1       7m20s
replicaset.apps/ms-sim-llm-d-modelservice-decode-674774f45d    3         3         3       7m11s
replicaset.apps/ms-sim-llm-d-modelservice-prefill-76c86dd9f8   1         1         1       7m11s
```

**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}`.

## Using the stack

For instructions on getting started making inference requests see [our docs](../../docs/getting-started-inferencing.md)

## Cleanup

To remove the deployment:

```bash
# From examples/sim
helmfile destroy -n ${NAMESPACE}

# Or uninstall manually
helm uninstall infra-sim -n ${NAMESPACE}
helm uninstall gaie-sim -n ${NAMESPACE}
helm uninstall ms-sim -n ${NAMESPACE}
```

**_NOTE:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, your release names will be different from the command above: `infra-$RELEASE_NAME_POSTFIX`, `gaie-$RELEASE_NAME_POSTFIX` and `ms-$RELEASE_NAME_POSTFIX`.

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../docs/customizing-a-guide.md)
