# Feature: Precise Prefix Cache Aware Routing

## Overview

This guide demonstrates how to configure the inference scheduler to use the new precise prefix cache aware routing based on [vLLM KV-Events](https://github.com/vllm-project/vllm/issues/16669) data. Precise prefix cache aware routing pulls up-to-date prefix cache status from serving instances, eliminating the need for additional indexing services and increasing cache hit rate at high throughput.

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md).
- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.
- Have the [Monitoring stack](../../docs/monitoring/README.md) installed on your system.

## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-precise` in this example.

```bash
export NAMESPACE=llm-d-precise # Or any namespace your heart desires
kubectl create namespace ${NAMESPACE}

# Clone the repo and switch to the latest release tag 
tag=$(curl -s https://api.github.com/repos/llm-d/llm-d/releases/latest | jq -r '.tag_name')
git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout "$tag"

cd guides/precise-prefix-cache-aware
helmfile apply -n ${NAMESPACE}
```

**_NOTE:_** You can set the `$RELEASE_NAME_POSTFIX` env variable to change the release names. This is how we support concurrent installs. Ex: `RELEASE_NAME_POSTFIX=kv-events-2 helmfile apply -n ${NAMESPACE}`

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
NAME            NAMESPACE       REVISION        UPDATED                                 STATUS          CHART                           APP VERSION
gaie-kv-events  llm-d-precise   1               2025-09-25 21:36:52.452999581 +0000 UTC deployed        inferencepool-v1.0.1            v1.0.1
infra-kv-events llm-d-precise   1               2025-09-25 21:36:50.848300265 +0000 UTC deployed        llm-d-infra-v1.3.3              v0.3.0     
ms-kv-events    llm-d-precise   1               2025-09-25 21:36:55.955958022 +0000 UTC deployed        llm-d-modelservice-v0.2.11      v0.2.0 
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                          READY   STATUS    RESTARTS   AGE
pod/gaie-kv-events-epp-687b78968b-wvswh                       1/1     Running   0          80s
pod/infra-kv-events-inference-gateway-istio-949d87f84-zvsp2   1/1     Running   0          85s
pod/ms-kv-events-llm-d-modelservice-decode-b874d48d9-bgm5r    2/2     Running   0          75s
pod/ms-kv-events-llm-d-modelservice-decode-b874d48d9-ph64c    2/2     Running   0          75s

NAME                                              TYPE           CLUSTER-IP   EXTERNAL-IP   PORT(S)                        AGE
service/gaie-kv-events-epp                        ClusterIP      10.16.2.44   <none>        9002/TCP,9090/TCP,5557/TCP     81s
service/gaie-kv-events-ip-805c964d                ClusterIP      None         <none>        54321/TCP                      75s
service/infra-kv-events-inference-gateway-istio   LoadBalancer   10.16.1.30   10.16.4.2     15021:32033/TCP,80:39332/TCP   86s

NAME                                                      READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-kv-events-epp                        1/1     1            1           81s
deployment.apps/infra-kv-events-inference-gateway-istio   1/1     1            1           86s
deployment.apps/ms-kv-events-llm-d-modelservice-decode    2/2     2            2           76s

NAME                                                                DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-kv-events-epp-687b78968b                       1         1         1       81s
replicaset.apps/infra-kv-events-inference-gateway-istio-949d87f84   1         1         1       86s
replicaset.apps/ms-kv-events-llm-d-modelservice-decode-b874d48d9    2         2         2       76s
```

**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}` and you have not changed the default release names via the `${RELEASE_NAME}` environment variable.

## Testing this "well lit path"

We have docs on getting started sending inference requests [available here](../../docs/getting-started-inferencing.md) that are general to all examples. However, this example has unique instructions to interact with it which will be provided here:

1. First, you will need to send a basic inference request to your gateway. For in depth documentation on how to do this, please see the link above, but a command will be provided to work out of the box with default settings:

```bash
kubectl port-forward -n ${NAMESPACE} service/infra-kv-events-inference-gateway-istio 8000:80
export LONG_TEXT_200_WORDS="Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum."

curl -s http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-0.6B",
    "prompt": "'"$LONG_TEXT_200_WORDS"'",
    "max_tokens": 50
  }' | jq
```

1. Check the inference-scheduler's prefix-cache-scorer's scores with the following command:

```bash
kubectl logs -l inferencepool=gaie-kv-events-epp -n ${NAMESPACE} --tail 100 | grep "Calculated score" | grep "precise-prefix-cache-scorer/precise-prefix-cache-scorer"
```

You should see output similar to:

```json
{"level":"Level(-4)","ts":"2025-10-07T16:07:36Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"77790804-deb4-441a-9a03-d771d8e20778","objectiveKey":"","incomingModelName":"Qwen/Qwen3-0.6B","targetModelName":"Qwen/Qwen3-0.6B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-75499f8dc5-pbp84","namespace":"llm-d-precise"},"score":0}
{"level":"Level(-4)","ts":"2025-10-07T16:07:36Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"77790804-deb4-441a-9a03-d771d8e20778","objectiveKey":"","incomingModelName":"Qwen/Qwen3-0.6B","targetModelName":"Qwen/Qwen3-0.6B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-75499f8dc5-kgnqh","namespace":"llm-d-precise"},"score":0}
```

1. Repeat the steps above to see the prefix-cache-scorer in action

You should see output similar to:

```json
{"level":"Level(-4)","ts":"2025-10-07T16:09:21Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"f4c967aa-ad15-4be2-8640-55164da18dfa","objectiveKey":"","incomingModelName":"Qwen/Qwen3-0.6B","targetModelName":"Qwen/Qwen3-0.6B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-75499f8dc5-pbp84","namespace":"llm-d-precise"},"score":0}
{"level":"Level(-4)","ts":"2025-10-07T16:09:21Z","caller":"framework/scheduler_profile.go:165","msg":"Calculated score","x-request-id":"f4c967aa-ad15-4be2-8640-55164da18dfa","objectiveKey":"","incomingModelName":"Qwen/Qwen3-0.6B","targetModelName":"Qwen/Qwen3-0.6B","priority":0,"plugin":"precise-prefix-cache-scorer/precise-prefix-cache-scorer","endpoint":{"name":"ms-kv-events-llm-d-modelservice-decode-75499f8dc5-kgnqh","namespace":"llm-d-precise"},"score":1}
```

**_NOTE:_** These logs will only appear for unique requests, so if you don't see repeated instances of these logs make sure to redo them in a unique way.

Notice that the second time we called the `/v1/completions` endpoint, the prefix-cache-scorer was able to return a score for the pod,
indicating that it had cached the KV-blocks from the first call.

## Cleanup

To remove the deployment:

```bash
# Remove the model services
# From examples/precise-prefix-cache-aware
helmfile destroy -n ${NAMESPACE}

# Or uninstall manually
helm uninstall infra-kv-events -n ${NAMESPACE}
helm uninstall gaie-kv-events -n ${NAMESPACE}
helm uninstall ms-kv-events -n ${NAMESPACE}
```

**_NOTE:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, your release names will be different from the command above: `infra-$RELEASE_NAME_POSTFIX`, `gaie-$RELEASE_NAME_POSTFIX` and `ms-$RELEASE_NAME_POSTFIX`.

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../docs/customizing-a-guide.md)
