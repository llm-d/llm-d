# Scenario 4: loading a large model from a PVC 

Downloading a model from Hugging Face takes a long time for large models like [`meta-llama/Llama-4-Scout-17B-16E`](https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E), and one way to circumvent the long container creation time is to download a model to a PVC ahead of time and mount the PVC in the vLLM container. We have provided a baseconfig with the volume mounts configured, and all that is needed in the ModelService CR is to specify the path to which the model can be found.

- [./llama4.yaml](./llama4.yaml)
- [/universal-baseconfig-pvc.yaml](./universal-baseconfig-pvc.yaml)

Because this LLM requires a certain amount of GPU memory, we have utilized the `acceleratorTypes` section under prefill and decode to specify node affinity for this model. 

```
kubectl apply -f examples/pvc/universal-baseconfig-pvc.yaml
kubectl apply -f examples/pvc/llama4.yaml
```

This should drastically shorten the wait time for pod creation. 

## Expected cluster state 

The state of the cluster is identical to that of Scenario 2 and 3 (see details [here](../nixl-xpyd/README.md#expected-cluster-state)).

## Simulate client request 

Follow the same steps to send a client request in [scenario 2 or 3](./README.md#simulate-client-request).