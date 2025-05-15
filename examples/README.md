# Sample ModelService CRs and BaseConfigs

This folder contains example baseconfigs (ConfigMap) and ModelService CRs for various scenarios, including downloading models from Hugging Face or loading from a PVC. In particular, we provide a "universal" baseconfig that can be used for those scenarios. We will also show how to apply to an OpenShift cluster and the results you can expect from applying the ModelService CR and its referenced baseconfig. 

👉 [baseconfigs](./baseconfigs/)

👉 [msvcs](./msvcs/)

## Prerequisite
Before you get started, ensure that you have the `llm-d` installed on a Kubernetes cluster. See [Quick Start](https://github.com/llm-d/llm-d-deployer/blob/main/quickstart/README.md) for detailed instructions. This Quick Start installs the required CRDs and controller RBACs required to run the following scenarios.

## Scenarios 

### Scenario 1: serving a base model on vLLM on one pod
A simple use case is online serving a model on vLLM using one deployment. We will serve [`ibm-granite/granite-3.3-2b-base`](https://huggingface.co/ibm-granite/granite-3.3-2b-base) which can be downloaded from Hugging Face without the need for a token.

- [msvcs/granite3.2.yaml](./msvcs/granite3.2.yaml)
- [baseconfigs/simple-baseconfig.yaml](./baseconfigs/simple-baseconfig.yaml)

The `simple-baseconfig` contains just one section for `decodeDeployment`, which is a deployment template that spins up one vLLM container. It also specifies a volume and volume mount for downloading the model. The `decodeService` section is optional.

*Note: the term `decodeDeployment` might be misleading. There is no P/D disagreegation in this example. Using `prefillDeployment` will achieve the same result. We just need a deployment template for serving the model.*

Applying the baseconfig and CR to a Kubernetes cluster, you should expect a deployment and service getting created. 

```
kubectl apply -f examples/baseconfigs/simple-baseconfig.yaml
kubectl apply -f examples/msvcs/granite3.2.yaml
```

You may port-forward the pod or service at port 8000 (because that is the port for the [vLLM container](./baseconfigs/simple-baseconfig.yaml#L30) and [decode service]((./baseconfigs/simple-baseconfig.yaml#L64)) specified in the baseconfig) and query the vLLM container. The following command port-forwards the service and sends a request.

```
kubectl port-forward svc/granite-base-model-service-decode 8000:8000
curl -vvv http://localhost:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
    "model": "ibm-granite/granite-3.3-2b-base",
    "prompt": "New York is"
}'
```

### Scenario 2: serving a model with routing and P/D disaggregation support
The platform owner may create another baseconfig used to serve models with routing enabled, useful for P/D disaggregation. We will continue to use a model that can be downloaded from Hugging Face: `facebook/opt-125m`.

- [msvcs/facebook-nixl.yaml](./msvcs/facebook-nixl.yaml)
- [baseconfigs/universal-baseconfig-hf.yaml](./baseconfigs/universal-baseconfig-hf.yaml)

Apply the baseconfig and CR to a Kubernetes cluster.

```
kubectl apply -f examples/baseconfigs/universal-baseconfig-hf.yaml
kubectl apply -f examples/msvcs/facebook-nixl.yaml
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

You may port-forward the inference gateway installed as part of `llm-d`, and send a request which will route to the EPP.

<!-- TODO: fix this -->
```
GATEWAY_PORT=$(kubectl get gateway -o jsonpath='{.items[0].spec.listeners[0].port}')
kubectl port-forward svc/inference-gateway 8000:${GATEWAY_PORT}
curl http://localhost:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
    "model": "facebook/opt-125m",
    "prompt": "Author-contribution statements and acknowledgements in research papers should state clearly and specifically whether, and to what extent, the authors used AI technologies such as ChatGPT in the preparation of their manuscript and analysis. They should also indicate which LLMs were used. This will alert editors and reviewers to scrutinize manuscripts more carefully for potential biases, inaccuracies and improper source crediting. Likewise, scientific journals should be transparent about their use of LLMs, for example when selecting submitted manuscripts. Mention the large language model based product mentioned in the paragraph above:"
}'
```

curl http://localhost:8000/v1/completions \
    -H "Content-Type: application/json" \
    -d '{
    "model": "facebook/opt-125m",
    "prompt": "short prompt"
}'

### Scenario 3: serving a model with xPyD disaggregation
Previously, we have looked at MSVCs which have just one replica for decode and prefill workloads. ModelService can help you achieve xPyD disaggregation, and all that is required is using different `replicas` in the prefill and decode specs. 

Modify the previous MSVC manifest file ([msvcs/facebook-nixl.yaml](./msvcs/facebook-nixl.yaml)) to use different replica counts for prefill and decode sections. It should look something like this: 

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
kubectl apply -f examples/msvcs/facebook-nixl.yaml
```

and you should see the corresponding number of pods spin up for each deployment. Use the same request as in the last scenario to verify.

### Scenario 4: loading a large model from a PVC 

<!-- We need deployer to tell us the configs for this example, and also verify-->

Downloading a model from Hugging Face takes a long time for large models like [`meta-llama/Llama-4-Scout-17B-16E`](https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E), and one way to circumvent the long container creation time is to download a model to a PVC ahead of time and mount the PVC in the vLLM container. We have provided a baseconfig with the volume mounts configured, and all that is needed in the ModelService CR is to specify the path to which the model can be found.

- [msvcs/llama4.yaml](./msvcs/llama4.yaml)
- [baseconfigs/universal-baseconfig-pvc.yaml](./baseconfigs/universal-baseconfig-pvc.yaml)

Furthermore, because this large language model requires a certain amount of GPU memory, we have utilized the `acceleratorTypes` section under prefill and decode to specify node affinity for this model. 

```
kubectl apply -f examples/baseconfigs/universal-baseconfig-pvc.yaml
kubectl apply -f examples/msvcs/llama4.yaml
```

This should drastically shorten the wait time for pod creation. 

## Limitations
- **HttpRoute dependency on InferencePool**: for each base model (MSVC) you deploy with EPP enabled, one must configure HttpRoute to the InferencePool name that the ModelService controller creates. This is counter-intuitive and not user-friendly. This is a known issue and we can resolve it by enabling ModelService to create this resource as a children.
- The idea of "universal" BaseConfigs is to provide configuration for deploying multiple base models. We provide two universal BaseConfigs in our examples, conditioned on how the model artifacts are retrieved. In theory, this layer should be abstracted from BaseConfigs and the BaseConfigs for these two scenarios should be the same. This is a known issue and we have issues in our repository to track this.