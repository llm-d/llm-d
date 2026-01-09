# Well-lit Path: Intelligent Inference Scheduling

## Overview

This guide deploys the recommended out of the box [scheduling configuration](https://github.com/llm-d/llm-d-inference-scheduler/blob/main/docs/architecture.md) for most vLLM deployments, reducing tail latency and increasing throughput through load-aware and prefix-cache aware balancing. This can be run on a single GPU that can load [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B).

This profile defaults to the approximate prefix cache aware scorer, which only observes request traffic to predict prefix cache locality. The [precise prefix cache aware routing feature](../precise-prefix-cache-aware) improves hit rate by introspecting the vLLM instances for cache entries and will become the default in a future release.

## Hardware Requirements

This example out of the box requires 2 GPUs of any supported kind:
- **NVIDIA GPUs**: Any NVIDIA GPU (support determined by the inferencing image used)
- **Intel XPU/GPUs**: Intel Data Center GPU Max 1550 or compatible Intel XPU device
- **TPUs**: Google Cloud TPUs (when using GKE TPU configuration)

**Alternative CPU Deployment**: For CPU-only deployment (no GPUs required), see the [Hardware Backends](#hardware-backends) section for CPU-specific deployment instructions. CPU deployment requires Intel/AMD CPUs with 64 cores and 64GB RAM per replica.

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Ensure your cluster infrastructure is sufficient to [deploy high scale inference](../prereq/infrastructure)
- Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.
- Create a namespace for installation.
  
  ```
  export NAMESPACE=llm-d-inference-scheduler # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.
- [Choose an llm-d version](../prereq/client-setup/README.md#llm-d-version)
- [Skip if using standalone-inference-scheduling] Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md)


## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-inference-scheduler` in this example.

**_IMPORTANT:_** When using long namespace names (like `llm-d-inference-scheduler`), the generated pod hostnames may become too long and cause issues due to Linux hostname length limitations (typically 64 characters maximum). It's recommended to use shorter namespace names (like `llm-d`) and set `RELEASE_NAME_POSTFIX` to generate shorter hostnames and avoid potential networking or vLLM startup problems.

### Deploy

```bash
cd guides/inference-scheduling
```

<!-- TABS:START -->
<!-- TAB:GPU deployment  -->

**GPU deployment**
```bash
helmfile apply -n ${NAMESPACE}
```

<!-- TAB:CPU deployment  -->
**CPU-only deployment:**
```bash
helmfile apply -e cpu -n ${NAMESPACE}
```

<!-- TABS:END -->

**_NOTE:_** You can set the `$RELEASE_NAME_POSTFIX` env variable to change the release names. This is how we support concurrent installs. Ex: `RELEASE_NAME_POSTFIX=inference-scheduling-2 helmfile apply -n ${NAMESPACE}`

### Inference Request Scheduler and Hardware Options

#### Inference Request Scheduler
<!-- TABS:START -->

<!-- TAB:Gateway Option -->
##### Gateway Option

**_NOTE:_** This uses Istio as the default gateway provider, see [Gateway Options](./README.md#gateway-options) for installing with a specific provider.

To specify your gateway choice you can use the `-e <gateway option>` flag, ex:

```bash
helmfile apply -e kgateway -n ${NAMESPACE}
```


For DigitalOcean Kubernetes Service (DOKS):

```bash
helmfile apply -e digitalocean -n ${NAMESPACE}
```

 **_NOTE:_** DigitalOcean deployment uses public Qwen/Qwen3-0.6B model (no HuggingFace token required) and is optimized for DOKS GPU nodes with automatic tolerations and node selectors. Gateway API v1 compatibility fixes are automatically included.

To see what gateway options are supported refer to our [gateway provider prereq doc](../prereq/gateway-provider/README.md#supported-providers). Gateway configurations per provider are tracked in the [gateway-configurations directory](../prereq/gateway-provider/common-configurations/).

You can also customize your gateway, for more information on how to do that see our [gateway customization docs](../../docs/customizing-your-gateway.md).

<!-- TAB: Standalone Option -->
##### Standalone Option
With this option, the inference scheduler is deployed along with a sidecar Envoy proxy instead of a proxy provisioned using the Kubernetes Gateway API.

To deploy as a standalone inference scheduler, use the `-e standalone` flag, ex:

```bash
helmfile apply -e standalone -n ${NAMESPACE}
```

<!-- TABS:END -->

#### Hardware Backends

Currently in the `inference-scheduling` example we suppport configurations for `xpu`, `tpu`, `cpu`, and `cuda` GPUs. By default we use modelserver values supporting `cuda` GPUs, but to deploy on one of the other hardware backends you may use:

```bash
helmfile apply -e xpu  -n ${NAMESPACE} # targets istio as gateway provider with XPU hardware
# or
helmfile apply -e gke_tpu  -n ${NAMESPACE} # targets GKE externally managed as gateway provider with TPU hardware
# or
helmfile apply -e cpu  -n ${NAMESPACE} # targets istio as gateway provider with CPU hardware
```

##### CPU Inferencing
This case expects using 4th Gen Intel Xeon processors (Sapphire Rapids) or later. 

### Install HTTPRoute When Using Gateway option

Follow provider specific instructions for installing HTTPRoute.

#### Install for "kgateway" or "istio"

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
```

#### Install for "gke"

```bash
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

#### Install for "digitalocean"

```bash
kubectl apply -f httproute.yaml -n ${NAMESPACE}
```

## Verify the Installation

<!-- TABS:START -->

<!-- TAB:Gateway Option -->
### Gateway option

- Firstly, you should be able to list all helm releases to view the 3 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME                        NAMESPACE                 REVISION  UPDATED                               STATUS    CHART                     APP VERSION
gaie-inference-scheduling   llm-d-inference-scheduler 1         2025-08-24 11:24:53.231918 -0700 PDT  deployed  inferencepool-v1.2.0-rc.1 v1.2.0-rc.1
infra-inference-scheduling  llm-d-inference-scheduler 1         2025-08-24 11:24:49.551591 -0700 PDT  deployed  llm-d-infra-v1.3.4        v0.3.0
ms-inference-scheduling     llm-d-inference-scheduler 1         2025-08-24 11:24:58.360173 -0700 PDT  deployed  llm-d-modelservice-v0.3.8 v0.3.0
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                                  READY   STATUS    RESTARTS   AGE
pod/gaie-inference-scheduling-epp-f8fbd9897-cxfvn                     1/1     Running   0          3m59s
pod/infra-inference-scheduling-inference-gateway-istio-6787675b9swc   1/1     Running   0          4m3s
pod/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5b58lw9   2/2     Running   0          3m55s
pod/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5bt5f9s   2/2     Running   0          3m55s

NAME                                                         TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)                        AGE
service/gaie-inference-scheduling-epp                        ClusterIP      10.16.3.151   <none>        9002/TCP,9090/TCP              3m59s
service/gaie-inference-scheduling-ip-18c12339                ClusterIP      None          <none>        54321/TCP                      3m59s
service/infra-inference-scheduling-inference-gateway-istio   LoadBalancer   10.16.1.195   10.16.4.2     15021:30274/TCP,80:32814/TCP   4m3s

NAME                                                                 READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-inference-scheduling-epp                        1/1     1            1           4m
deployment.apps/infra-inference-scheduling-inference-gateway-istio   1/1     1            1           4m4s
deployment.apps/ms-inference-scheduling-llm-d-modelservice-decode    2/2     2            2           3m56s

NAME                                                                           DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-inference-scheduling-epp-f8fbd9897                        1         1         1       4m
replicaset.apps/infra-inference-scheduling-inference-gateway-istio-678767549   1         1         1       4m4s
replicaset.apps/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5b8    2         2         2       3m56s
```

<!-- TAB: Standalone Option -->
### Standalone option

- Firstly, you should be able to list all helm releases to view the 2 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME                        NAMESPACE                 REVISION  UPDATED                               STATUS    CHART                     APP VERSION
gaie-inference-scheduling   llm-d-inference-scheduler 1         2025-08-24 11:24:53.231918 -0700 PDT  deployed  inferencepool-v1.2.0-rc.1 v1.2.0-rc.1
ms-inference-scheduling     llm-d-inference-scheduler 1         2025-08-24 11:24:58.360173 -0700 PDT  deployed  llm-d-modelservice-v0.3.8 v0.3.0
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                                  READY   STATUS    RESTARTS   AGE
pod/gaie-inference-scheduling-epp-f8fbd9897-cxfvn                     1/1     Running   0          3m59s
pod/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5b58lw9   2/2     Running   0          3m55s
pod/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5bt5f9s   2/2     Running   0          3m55s

NAME                                                         TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)                        AGE
service/gaie-inference-scheduling-epp                        ClusterIP      10.16.3.151   <none>        9002/TCP,9090/TCP              3m59s
service/gaie-inference-scheduling-ip-18c12339                ClusterIP      None          <none>        54321/TCP                      3m59s

NAME                                                                 READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-inference-scheduling-epp                        1/1     1            1           4m
deployment.apps/ms-inference-scheduling-llm-d-modelservice-decode    2/2     2            2           3m56s

NAME                                                                           DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-inference-scheduling-epp-f8fbd9897                        1         1         1       4m
replicaset.apps/ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5b8    2         2         2       3m56s
```
**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}` and you have not changed the default release names via the `${RELEASE_NAME}` environment variable.

<!-- TABS:END -->

## Using the stack

For instructions on getting started making inference requests see [our docs](../../docs/getting-started-inferencing.md)

## Cleanup

To remove the deployment:

```bash
# From examples/inference-scheduling
helmfile destroy -n ${NAMESPACE}

# Or uninstall manually
helm uninstall infra-inference-scheduling -n ${NAMESPACE} --ignore-not-found
helm uninstall gaie-inference-scheduling -n ${NAMESPACE}
helm uninstall ms-inference-scheduling -n ${NAMESPACE}
```

**_NOTE:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, your release names will be different from the command above: `infra-$RELEASE_NAME_POSTFIX`, `gaie-$RELEASE_NAME_POSTFIX` and `ms-$RELEASE_NAME_POSTFIX`.

### Cleanup HTTPRoute when using Gateway option

Follow provider specific instructions for deleting HTTPRoute.
#### Cleanup for "kgateway" or "istio"

```bash
kubectl delete -f httproute.yaml -n ${NAMESPACE}
```

#### Cleanup for "gke"

```bash
kubectl delete -f httproute.gke.yaml -n ${NAMESPACE}
```

#### Cleanup for "digitalocean"

```bash
kubectl delete -f httproute.yaml -n ${NAMESPACE}
```
## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../docs/customizing-a-guide.md)
