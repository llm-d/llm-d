# Feature: Precise Prefix Cache Aware Routing

## Overview

This guide demonstrates how to configure the inference scheduler to use the new precise prefix cache aware routing based on [vLLM KV-Events](https://github.com/vllm-project/vllm/issues/16669) data. Precise prefix cache aware routing pulls up-to-date prefix cache status from serving instances, eliminating the need for additional indexing services and increasing cache hit rate at high throughput.

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Configure and deploy your [Gateway control plane](../prereq/gateway-provider/README.md).
- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.

## Installation

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the `${NAMESPACE}` environment variable. If you have not set this, it will default to `llm-d-precise` in this example.

```bash
export NAMESPACE=llm-d-precise # Or any namespace your heart desires
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

## Verify the Installation

- Firstly, you should be able to list all helm releases to view the 3 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME            NAMESPACE     REVISION  UPDATED                               STATUS    CHART                     APP VERSION
gaie-kv-events  llm-d-precise 1         2025-08-24 12:05:31.484748 -0700 PDT  deployed  inferencepool-v0.5.1      v0.5.1
infra-kv-events llm-d-precise 1         2025-08-24 12:05:27.485812 -0700 PDT  deployed  llm-d-infra-v1.3.0        v0.3.0
ms-kv-events    llm-d-precise 1         2025-08-24 12:05:37.660439 -0700 PDT  deployed  llm-d-modelservice-v0.2.7 v0.2.0
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
kubectl logs -l inferencepool=gaie-kv-events-epp -n ${NAMESPACE} --tail 100 | grep "Got pod scores"
```

You should see output similar to:

```bash
2025-08-24T19:19:16Z  LEVEL(-4) prefix-cache-scorer/prefix-cache-scorer scorer/prefix_cache_tracking.go:125 Got pod scores  {"x-request-id": "28b10175-d1f3-45c4-b970-a13dfc6811e3", "model": "Qwen/Qwen3-0.6B", "resolvedTargetModel": "Qwen/Qwen3-0.6B", "criticality": "Sheddable", "scores": null}
```

1. Repeat the steps above to see the prefix-cache-scorer in action

You should see output similar to:

```log
2025-07-18T22:00:24Z    LEVEL(-4)       prefix-cache-scorer/prefix-cache-scorer scorer/prefix_cache_tracking.go:133     Got pod scores  {"x-request-id": "0e08703d-30c0-4624-a7b3-31e94dc99bc8", "model": "Qwen/Qwen3-0.6B", "resolvedTargetModel": "Qwen/Qwen3-0.6B", "criticality": "Sheddable", "scores": null}
2025-07-18T22:00:46Z    LEVEL(-4)       prefix-cache-scorer/prefix-cache-scorer scorer/prefix_cache_tracking.go:133     Got pod scores  {"x-request-id": "8d0b587d-058f-4d2e-a062-a859a565d37a", "model": "Qwen/Qwen3-0.6B", "resolvedTargetModel": "Qwen/Qwen3-0.6B", "criticality": "Sheddable", "scores": {"${POD_IP}":2}}
```

**_NOTE:_** These logs will only appear for unique requests, so if you don't see repeated instances of these logs make sure to redo them in a unique way.

Notice that the second time we called the `/v1/completions` endpoint, the prefix-cache-scorer was able to return a score for the pod,
indicating that it had cached the KV-blocks from the first call.

1. See the `kvblock.Index` metrics in the `gaie-kv-events-epp` pod:

```bash
kubectl logs -l inferencepool=gaie-kv-events-epp -n llm-d-precise --tail 100 | grep "metrics beat"
```

You should see output similar to:

```log
I0718 23:57:10.781371       1 collector.go:107] "metrics beat" logger="metrics" admissions=3 evictions=0 lookups=1 hits=2 latency_count=1 latency_sum=0.000006859 latency_avg=0.0000022863333333333334
```

The `admissions` count indicates how many KV-blocks were added to the index through vLLM's KV-Events,
while the `hits` count indicates how many times the index was able to find a KV-block for a pod.

If the beat is missing lookups, wait for the next one (1 minute beats).

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

**_NOTE:_** You do not need to specify your `environment` with the `-e <environment>` flag to `helmfile` for removing a installation of the guide, even if you use a non-default option. You do, however, have to set the `-n ${NAMESPACE}` otherwise it may not cleanup the releases in the proper namespace.

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../docs/customizing-a-guide.md)
