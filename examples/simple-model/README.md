# Scenario 1: serving a base model on vLLM on one pod

A simple use case is online serving a model on vLLM using one deployment. We will serve [`ibm-granite/granite-3.3-2b-base`](https://huggingface.co/ibm-granite/granite-3.3-2b-base) which can be downloaded from Hugging Face without the need for a token.

- [granite3.2.yaml](./granite3.2.yaml)
- [simple-baseconfig.yaml](./simple-baseconfig.yaml)

The `simple-baseconfig` contains just one section for `decodeDeployment`, which is a deployment template that spins up one vLLM container. It also specifies a volume and volume mount for downloading the model. The `decodeService` section is optional.

*Note: the term `decodeDeployment` might be misleading. There is no P/D disagreegation in this example. Using `prefillDeployment` will achieve the same result. We just need a deployment template for serving the model.*

Applying the baseconfig and ModelService CR to a Kubernetes cluster, you should expect a deployment and service getting created. The following commands assumes that you are at the root of `llm-d` repository.

```
kubectl apply -f examples/simple-model/simple-baseconfig.yaml
kubectl apply -f examples/simple-model/granite3.2.yaml
```

## Expected cluster state

1. A `ModelService` in the cluster with status to help you quickly monitor pods

    ```
    $ kubectl get modelservice
    NAME                 DECOUPLE SCALING   PREFILL READY   PREFILL AVAIL   DECODE READY   DECODE AVAIL   AGE
    granite-base-model   false              1/1             1               1/1            1              1m
    ```

2. A deployment for the role you specified, which is `decode` in this case

    ```
    $ kubectl get deployment
    NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
    granite-base-model-decode       1/1     1            1           1m
    ```

3. A service for the decode deployment

    ```
    $ kubectl get service
    NAME                                             TYPE           CLUSTER-IP       EXTERNAL-IP   PORT(S)        AGE
    granite-base-model-decode-service                ClusterIP      172.30.60.249    <none>        9002/TCP       1m
    ```

## Simulate client request

You may port-forward the pod or service at port 8000 (because that is the port for the [vLLM container](./simple-baseconfig.yaml#L30) and [decode service]((./simple-baseconfig.yaml#L64)) specified in the baseconfig) and query the vLLM container. The following command port-forwards the service and sends a request.

```
kubectl port-forward svc/granite-base-model-service-decode 8000:8000
```

Then, open a new terminal to send a request to the service, which will route to the vLLM pod.
```
curl http://localhost:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
    "model": "ibm-granite/granite-3.3-2b-base",
    "prompt": "New York is"
}'
```

You should expect to see something like the following:

```

```