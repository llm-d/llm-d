# Inference against llm-d

This document show you how to interact with the model server and inference scheduler.

## Prerequisites

You are assumed to have deployed the llm-d inference stack from a guide, using the model service Helm charts, or otherwise followed the llm-d conventions for deployment of inference scheduler and model server.

## Exposing your gateway

First we need to choose what strategy were going to use to expose / interact with your gateway. It should be noted that this will be affected by the values you used when installing the `llm-d-infra` chart for your given guide. There are three options:

1. Port-forward the gateway service to localhost
    - Pros: works on any k8s cluster type
    - Cons: requires k8s user access
2. Using the gateway's external IP address
    - Pros: publicly accessible endpoint
    - Cons: environment dependent - depends on your `Service.type=LoadBalancer` and cloud-provider integration to your k8s cluster
3. Using an ingress attached to the gateway service
    - Pros:
        - Stable hostname
        - Optional TLS and Traffic policies
    - Cons:
        - Depends on an ingress controller
        - Depends on configuring DNS

**_NOTE:_** If you’re unsure which to use—start with port-forward as its the most reliable and easiest. For anything shareable, use Ingress/Route. Use LoadBalancer if your provider supports it and you just need raw L4 access.

**_NOTE:_** It should also be noted that you can use other platform specific networking options such as Openshift Routes. When benchmarking the `pd-disaggregation` example with OCP routes we noticed that Openshift Networking was enforcing timeouts on gateway requests, which, under heavy load affected our results. If you wish to use a platform dependent option with a benchmarking setup ensure to check your platform docs.

Each of these paths should export the `${ENDPOINT}` environment variable which we can send requests to.

### Port-forward to a gateway on cluster

For gateway providers that install into the cluster you can port forward to the gateway deployment directly.

```bash
GATEWAY_SVC=$(kubectl get svc -n "${NAMESPACE}" -o yaml | yq '.items[] | select(.metadata.name | test(".*-inference-gateway(-.*)?$")).metadata.name' | head -n1)
```

**_NOTE:_** This command assumes you have one gateway in your given `${NAMESPACE}`, even if you have multiple it will only grab the name of the first gateway service in alphabetical order. If you are running multiple gateway deployments in a single namespace, you will have to explicitly set your `$GATEWAY_SVC` to the appropriate gateway endpoint.

```bash
k get services
NAME                                                 TYPE           CLUSTER-IP    EXTERNAL-IP   PORT(S)                        AGE
gaie-inference-scheduling-epp                        ClusterIP      10.16.3.250   <none>        9002/TCP,9090/TCP              18s
gaie-inference-scheduling-ip-18c12339                ClusterIP      None          <none>        54321/TCP                      12s
gaie-sim-epp                                         ClusterIP      10.16.1.220   <none>        9002/TCP,9090/TCP              80m
infra-inference-scheduling-inference-gateway-istio   LoadBalancer   10.16.3.226   10.16.4.3     15021:34529/TCP,80:35734/TCP   22s
infra-sim-inference-gateway                          LoadBalancer   10.16.1.62    10.16.4.2     80:38348/TCP                   81m

export GATEWAY_SVC="infra-inference-scheduling-inference-gateway-istio"
```

After we have our gateway service name, we can port forward it:

```bash
export ENDPOINT="http://localhost:8000"
kubectl port-forward -n ${NAMESPACE} service/${GATEWAY_SVC} 8000:80
```

**_NOTE:_** Port 8000 is the default gateway service port in our guides. You can change this by altering the values for the `llm-d-infra` helm chart and updating your port-forward command appropriately.

### Using the Gateway External IP with service type `LoadBalancer`

> [!REQUIREMENTS]
> This requires that the release of the `llm-d-infra` chart must have `.gateway.serviceType` set to `LoadBalancer`. Currently this is the [default value](https://github.com/llm-d-incubation/llm-d-infra/blob/main/charts/llm-d-infra/values.yaml#L167), however its worth noting.
> This requires your K8s cluster is deployed on a cloud provider with LB integration (EKS/GKE/AKS/AWS/…).

If you are using the GKE gateway or have are using the default service type of `LoadBalancer` for you gateway and you are on a cloud platform with loadbalancing, you can use the `External IP` of your gateway service (you should see the same thing under your gateway with `kubectl get gateway`.)

```bash
export ENDPOINT=$(kubectl get gateway --no-headers -n ${NAMESPACE} -o jsonpath='{.items[].status.addresses[0].value}')
```

**_NOTE:_** This command assumes you have one gateway in your given `${NAMESPACE}`, if you have multiple, it will just grab one. Therefore, in the case you do have multiple gateways, you should find the correct gateway and target that specifically:

```bash
kubectl get gateway -n ${NAMESPACE}
NAME                                           CLASS      ADDRESS                                                                   PROGRAMMED   AGE
infra-inference-scheduling-inference-gateway   kgateway   af805bef3ec444a558da28061b487dd5-2012676366.us-east-1.elb.amazonaws.com   True         11m
infra-sim-inference-gateway                    kgateway   a67ad245358e34bba9cb274bc220169e-1351042165.us-east-1.elb.amazonaws.com   True         45s

GATEWAY_NAME=infra-inference-scheduling-inference-gateway
export ENDPOINT=$(kubectl get gateway ${GATEWAY_NAME} --no-headers -n ${NAMESPACE} -o jsonpath='{.status.addresses[0].value}')
```

### Using an ingress

> [!REQUIREMENTS]
> This requires that the release of the `llm-d-infra` chart must have `.ingress.enabled` set to `true`, and the `.gateway.service.type` to `ClusterIP`.
> This requires some load-balancer configuration for your cluster / ingress-controller. This could be either cloud-provider integration or something like metalLB.

This is the most environment dependent of all the options, and can be tricky to set up. For more information on this see [our gateway customization docs](../docs/customizing-your-gateway.md#using-an-ingress). You should be able to get your endpoint from you ingress with the following:

```bash
export ENDPOINT=$(kubectl get ingress --no-headers -o jsonpath='{.items[].status.loadBalancer.ingress[0].ip}')
```

**_NOTE:_** This command assumes you have one ingress in your given `${NAMESPACE}`, if you have multiple, it will just grab one. Therefore, in the case you do have multiple gateways, you should find the correct gateway and target that specifically:

```bash
kubectl get ingress -n ${NAMESPACE}
NAME                                           CLASS     HOSTS   ADDRESS         PORTS   AGE
infra-inference-scheduling-inference-gateway   traefik   *       166.19.16.120   80      21m
infra-sim-inference-gateway                    traefik   *       166.19.16.132   80      7m

INGRESS_NAME=infra-inference-scheduling-inference-gateway
export ENDPOINT=$(kubectl get ingress ${GATEWAY_NAME} --no-headers -n ${NAMESPACE} -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
```

## Sending the Requests

### /v1/models endpoint

The first path we can hit is `/v1/models`. This endpoint is only dependent on a vllm server being up and running in your InferencePool, so even if you are following the wide expert-parallelism guide and only 1 decode or prefill instance is up, you should be able to get a response (this depends on configurations used by EPP as well). This is generally the safest request to make because while models per guide vary, the `/v1/models` is always available.

1. Try curling the `/v1/models` endpoint:

```bash
curl -s ${ENDPOINT}/v1/models \
  -H "Content-Type: application/json" | jq
```

Expected output:

```json
{
  "data": [
    {
      "created": 1752727169,
      "id": "random",
      "object": "model",
      "owned_by": "vllm",
      "parent": null,
      "root": "random"
    },
    {
      "created": 1752727169,
      "id": "",
      "object": "model",
      "owned_by": "vllm",
      "parent": "random",
      "root": ""
    }
  ],
  "object": "list"
}
```

### /v1/completions

Now lets try hitting the `/v1/completions` endpoint (this is model dependent, ensure your model matches what the server returns for the `v1/models` curl).

```bash
curl -X POST ${ENDPOINT}/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "random",
        "prompt": "How are you today?"
      }' | jq
```

Expected output:

```json
{
  "choices": [
    {
      "finish_reason": "stop",
      "index": 0,
      "message": {
        "content": "Today is a nice sunny day.",
        "role": "assistant"
      }
    }
  ],
  "created": 1752727735,
  "id": "chatcmpl-af42e9e3-dab0-420f-872b-d23353d982da",
  "model": "random"
}
```

Some other useful tricks you can with `/v1/completions` would be to control the `max_tokens` and pass a `seed`.

`max_tokens` is helpful because it will limit the size of tokens the server returns, and so by setting it to `1` you can mimic a test for TTFT. You can do so with the following:

```bash

curl -s http://${ENDPOINT}/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1-0528",
    "prompt": "Hi, how are you?",
    "max_tokens": "1"
  }' | jq
```

`seed` is helpful because it allows each request to be treated as unique to the inference server. It should be noted that this will not affect anything to do with prefix caching, or anything at the `inference-scheduler` level, so requests will still be routed to the same locations, but it should not cache anything at the vllm server level. We frequently construct

```bash
curl -s http://${ENDPOINT}/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-ai/DeepSeek-R1-0528",
    "prompt": "Hi, how are you?",
    "seed": "'$(date +%M%H%M%S)'"
  }' | jq
```

For more information options to set for the `/v1/completions` endpoint, see the [openAI API server /v1/completions docs](https://platform.openai.com/docs/api-reference/completions/create)

### /v1/chat/completions (coming soon)

While `/v1/completions` is a legacy API, it is still supported for now. We are in the process of converting over to `/v1/chat/completions`, and this work is being tracked in a few places.

The [initial implementation for the kv-cache-manager package](https://github.com/llm-d/llm-d-kv-cache-manager/issues/10) landed as part of the v0.2 sprint.

[Support within the `llm-d-inference-scheduler` image](https://github.com/llm-d/llm-d-inference-scheduler/issues/83) is in progress and part of the v0.3 roadmap, and further support to the upstream `epp` image is in progress and being [tracked here](https://github.com/kubernetes-sigs/gateway-api-inference-extension/issues/827).

## Following logs for requests

We recommend the `stern` tool for following requests because it will allow you to follow multiple pod logs at once. This is particularly useful in the context of llm-d because most deployments have multiple `decode` pods. For more information on this see our docs on [optional tools](../guides/prereq/client-setup/README.md#optional-tools).

To grab all decode pods in a namespace we can do the following:

```bash
DECODE_PODS=$(kubectl get pods --no-headers -n ${NAMESPACE} -l "llm-d.ai/role=decode" -o custom-columns=":metadata.name")
```

Then you can view those logs together using stern:

```bash
stern -n ${NAMESPACE} "$(echo "$DECODE_PODS" | paste -sd'|' -)"
```

Just with `kubectl logs` you can specify the container name following your pod names to only grab logs from specific containers (`stern` by default will print logs for all containers in a pod). You can do so as follows:

```bash
stern -n ${NAMESPACE} "$(echo "$DECODE_PODS" | paste -sd'|' -)" -c routing-proxy # for routing sidecar logs
stern -n ${NAMESPACE} "$(echo "$DECODE_PODS" | paste -sd'|' -)" -c vllm # for vllm logs
```

For grabbing the prefill pods you can re-use our same command from earlier just swap the role label:

```bash
PREFILL_PODS=$(kubectl get pods --no-headers -n ${NAMESPACE} -l "llm-d.ai/role=prefill" -o custom-columns=":metadata.name")
```

Typically this won't be as necessary because prefill does better with lower parallelism, and currently all of our guides use 1 prefill instance.

### `grep`ing out noise from logs

We have some helpful `grep -v` commands that can help you remove noise from your `vllm` and `routing-proxy` containers.

#### `routing-proxy` container noise

The moment the vllm container comes online from a k8s perspective (when its `status` becomes ready), the sidecar will start trying to connect to and communicate with it. However, after the `vllm` pod spins up, the vllm API server still needs to start up to be able to respond to requests. For the duration of this delta you will get some ugly logs in the sidecar that should look like the following:

```log
ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5bjvpbm routing-proxy E0824 16:49:51.115884       1 proxy.go:268] "waiting for vLLM to be ready" err="dial tcp [::1]:8200: connect: connection refused" logger="proxy server"
...
```

To avoid this, consider adding the following to your log command

```bash
stern ... | grep -v "waiting for vLLM to be ready"
```

#### `vllm` container noise

The `vllm` container will log any hits on its metrics endpoint, which should be getting continuously polled, you can grep them out with the following:

```bash
stern ... | grep -v "GET /metrics HTTP/1.1"
```

In some cases you might also want to ignore vllm's usage information which it will log every so often, so that you can isolate logs on a per-request basis. An example usage log from vllm might look like:

```log
ms-inference-scheduling-llm-d-modelservice-decode-8ff7fd5bxf7lt vllm DEBUG 08-24 18:09:51 [loggers.py:122] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 0.0 tokens/s, Running: 0 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.0%, Prefix cache hit rate: 0.0%
```

To target all of these usage logs you could just grep out their shared prefix:

```bash
stern ... | grep -v "Avg prompt throughput"
```

However you can also customize this `grep -v` command to include the 0 values so as to view usage metrics only once the server has started receiving traffic. You can also chain multiple terms together to `grep` out using the `\|` delimiter, ex: `grep -v "do not show a in logs\|also do not show b in logs"`.
