# Scenario 2: serving a model with routing and P/D disaggregation support

The platform owner may create another baseconfig used to serve models with routing enabled, useful for P/D disaggregation. We will continue to use a model that can be downloaded from Hugging Face: `facebook/opt-125m`.

- [./facebook-nixl.yaml](./facebook-nixl.yaml)
- [./universal-baseconfig-hf.yaml](./universal-baseconfig-hf.yaml)

Apply the baseconfig and ModelService CR to a Kubernetes cluster.

```
kubectl apply -f examples/nixl-xpyd/universal-baseconfig-hf.yaml
kubectl apply -f examples/nixl-xpyd/facebook-nixl.yaml
```

You should expect to see the following resources created for this scenario:

- Model components:
  - A decode deployment
  - A prefill deployment
- Routing components:
  - An InferencePool
  - An InferenceModel
  - An EPP deployment 
  - A service for EPP deployment
- RBAC components 
  - A service account for P/D deployments (for custom image pulls)
  - A service account for EPP deployment 
  - A rolebinding for EPP deployment 

## Expected cluster state

1. A `ModelService` in the cluster with status to help you quickly monitor pods

    ```
    $ kubectl get modelservice
    NAME                     DECOUPLE SCALING   PREFILL READY   PREFILL AVAIL   DECODE READY   DECODE AVAIL   AGE
    facebook-opt-125m-nixl   false              1/1             1               1/1            1              3h3m
    ```

2. Three deployments: prefill, decode, and EPP

    ```
    $ kubectl get deployment
    NAME                                READY   UP-TO-DATE   AVAILABLE   AGE
    facebook-opt-125m-nixl-decode       1/1     1            1           3h4m
    facebook-opt-125m-nixl-epp          1/1     1            1           3h4m
    facebook-opt-125m-nixl-prefill      1/1     1            1           3h4m
    ```
    
    If you were to examine the prefill or decode deployment, you should see that pods have selector labels on them. Those labels include the role of the pod and the model served on the vLLM engine, which are both used by the router for discovery.
    
    ```
    $ kubectl get deploy facebook-opt-125m-nixl-decode -o yaml | yq '.metadata.labels'
    llm-d.ai/inferenceServing: "true"
    llm-d.ai/model: facebook-opt-125m
    llm-d.ai/role: decode
    ```

3. A service for the EPP deployment

    ```
    $ kubectl get service
    NAME                                              TYPE           CLUSTER-IP       EXTERNAL-IP   PORT(S)        AGE
    facebook-opt-125m-nixl-epp-service                ClusterIP      172.30.60.249    <none>        9002/TCP       3h5m
    ```

4. An InferecePool

    ```
    $ kubectl get inferencepool
    NAME                                    AGE
    facebook-opt-125m-nixl-inference-pool   3h22m
    ```

5. An InferenceModel with the InferecePool reference. Note that `Criticality` currently defaults to Standard, unless you specify otherwise in the BaseConfig.

    ```
    $ kubectl get inferencemodel
    NAME                     MODEL NAME                         INFERENCE POOL                          CRITICALITY   AGE
    facebook-opt-125m-nixl   facebook/opt-125m                  facebook-opt-125m-nixl-inference-pool                 3h22m
    ```

6. RBAC components
   1. A shared ServiceAccount for P/D deployments and one for EPP deployment. 
        
        ```
        $ kubectl get serviceaccount
        NAME                              SECRETS   AGE
        facebook-opt-125m-nixl-epp-sa     1         3h25m
        facebook-opt-125m-nixl-sa         1         3h25m
        ```
        
        These service accounts are referenced in the respective deployments. For the EPP deployment, for example:
        
        ```
        $ kubectl get deploy facebook-opt-125m-nixl-epp -oyaml | grep serviceAccountName
              serviceAccountName: facebook-opt-125m-nixl-epp-sa
        ```
        
   2. A RoleBinding for EPP deployment. This is the ClusterRole required for EPP in controller start up.
        
        ```
        $ kubectl get rolebinding
        NAME                                       ROLE                                          AGE
        facebook-opt-125m-nixl-epp-rolebinding     ClusterRole/pod-read                          3h26m
        ```

## Simulate client request

You may port-forward the inference gateway installed as part of `llm-d`, and send a request which will route to the EPP.

```
GATEWAY_PORT=$(kubectl get gateway -o jsonpath='{.items[0].spec.listeners[0].port}')
kubectl port-forward svc/inference-gateway 8000:${GATEWAY_PORT}
```

Then, open a new terminal to send a request to the service, which will route to the vLLM pod.
```
curl http://localhost:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
    "model": "facebook/opt-125m",
    "prompt": "Author-contribution statements and acknowledgements in research papers should state clearly and specifically whether, and to what extent, the authors used AI technologies such as ChatGPT in the preparation of their manuscript and analysis. They should also indicate which LLMs were used. This will alert editors and reviewers to scrutinize manuscripts more carefully for potential biases, inaccuracies and improper source crediting. Likewise, scientific journals should be transparent about their use of LLMs, for example when selecting submitted manuscripts. Mention the large language model based product mentioned in the paragraph above:"
}' | jq .
```

You should expect to see something like the following:

```
  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed
100  1137    0   443  100   694   2790   4370 --:--:-- --:--:-- --:--:--  7196
{
  "choices": [
    {
      "finish_reason": "stop",
      "index": 0,
      "logprobs": null,
      "prompt_logprobs": null,
      "stop_reason": null,
      "text": " relevant literature or product description or segmentation for references."
    }
  ],
  "created": 1747352739,
  "id": "cmpl-e714475c-ea13-4bd3-a3cc-0c1c76e21c2b",
  "kv_transfer_params": null,
  "model": "facebook/opt-125m",
  "object": "text_completion",
  "usage": {
    "completion_tokens": 12,
    "prompt_tokens": 112,
    "prompt_tokens_details": null,
    "total_tokens": 124
  }
}
```

# Scenario 3: serving a model with xPyD disaggregation

Previously, we have looked at MSVCs which have just one replica for decode and prefill workloads. ModelService can help you achieve xPyD disaggregation, and all that is required is using different `replicas` in the prefill and decode specs. 

Modify the previous MSVC manifest file ([./facebook-nixl.yaml](./facebook-nixl.yaml)) to use different replica counts for prefill and decode sections. It should look something like this: 

```yaml
spec:
  prefill:
    replicas: 1
    # other fields...
  decode: 
    replicas: 2
    # other fields...
```

Note that in this scenario, we are using the same baseconfig used in the last scenario, because there is really no difference in terms of the base configuration between the two other than model-specific behaviors such as replica count and model name.

Re-apply the CR.

```
kubectl apply -f examples/nixl-xpyd/facebook-nixl.yaml
```

## Expected cluster state 

The cluster state is expected to be similar to the second scenario, with one important change. The decode deployment should now scale up to 2. 

```
$ kubectl get deployment
NAME                                READY   UP-TO-DATE   AVAILABLE   AGE
facebook-opt-125m-nixl-decode       2/2     2            2           3h4m
facebook-opt-125m-nixl-epp          1/1     1            1           3h4m
facebook-opt-125m-nixl-prefill      1/1     1            1           3h4m
```

## Simulate client request 

Follow the same steps to send a client request in [scenario 2](./README.md#simulate-client-request).