# Model Caching Proposal

## Summary

This proposal introduces a new llm-d component controlling the location of models to make vLLM startup time faster and more reliable. It also includes an implementation design focusing on caching models to local storage, with plans to expand functionalities based on community feedback.

# Motivation

vLLM startup time can greatly be reduced when models are cached on local storage. All supported vLLM model loaders, safetensor (builtin), [fastsafetensors](https://docs.vllm.ai/en/latest/models/extensions/fastsafetensor.html?h=fastsafete), [Run.ai model streamer](https://docs.vllm.ai/en/latest/models/extensions/runai_model_streamer.html), [Coreweave’s tensoriser](https://docs.vllm.ai/en/latest/models/extensions/tensorizer.html) support loading models from local storage. Each has consistently shown faster startup compared to loading models from HuggingFace (see [\#1](https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/benchmarks.md), [\#2](https://github.com/vllm-project/vllm/pull/10647), [\#3](https://v1.docs.coreweave.com/coreweave-machine-learning-and-ai/inference/tensorizer#benchmarks)).

In addition, loading models from local storage is considered more reliable than loading from the network (eg. S3, Huggingface, etc...), as it reduces dependency on external services, mitigates issues related to connectivity and latency, and makes system operations more predictable.

# Goals

* Be agnostic about which model loader is being used.
* Provide a low-level API to cache models on local storage.

# Non-Goals

* Provisioning Persistent Volumes, which is better handled by existing external static provisioners, such as [Local Persistent Volume Static Provisioner](https://github.com/kubernetes-sigs/sig-storage-local-static-provisioner), [Openshift Local Storage Operator](https://catalog.redhat.com/software/container-stacks/detail/66993e0f4f284e980ee072d3) and [Open EBS](https://openebs.io).
* Supporting other storage types such as S3, HTTP, etc…
* Supporting model revision (GIE does not support it yet).
* Providing an high-level CRD-based API.
* Supporting sharing models across namespaces.

# Proposal

The proposal below takes a minimalist approach, aiming to deliver an MVP quickly in order to gather feedback from the community for further improvements.

<!--
## Determining Models to Cache

The [Gateway API Inference Extension (GIE)](https://gateway-api-inference-extension.sigs.k8s.io) introduces the concept of [InferenceModel](https://gateway-api-inference-extension.sigs.k8s.io/concepts/api-overview/#inferencemodel) (and a proposed [revised InferenceModel API](https://docs.google.com/document/d/1x6aI9pbTF5oOsaEQYc9n4pBBY3_AuEY2X51VKxmBSnU/edit?tab=t.0#heading=h.towq7jyczzgo)) defining which base models (and adapters) can potentially be served. The `InferenceModel` spec defines the `modelName` field corresponding to the name of the model as it will be set in the "model" parameter for an incoming request. By collecting all `InferenceModel` model names we get all the models to cache on local storage.
-->

## Assigning Models to Nodes

Not all models need to be cached on all nodes, due to accelerator memory constraints or other non-technical considerations such as cost and GPU availabilities. This proposal introduces the following annotations selecting which models to cache:

* **llm-d.io/desired-cached-models**: comma-separated model names of desired models to cache on the node.
* **llm-d.io/cached-models**: comma-separated model names of models existing on the node.

In Kubernetes, local storage is made accessible via the [local persistent volumes](https://kubernetes.io/docs/concepts/storage/volumes/#local) (LPV) feature. Since multiple LPVs can be created per node, all referencing the same or a different local path, we propose to attach the aforementioned annotations to Local Persistent Volume Claims (LPVCs) for determining where to download selected models.

Example

```yaml

apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: example-pvc
  annotations:
    llm-d.io/desired-cached-models: "ibm-granite/granite-3.3-8b-instruct,deepseek-ai/DeepSeek-R1-0528"
    llm-d.io/cached-models: "ibm-granite/granite-3.3-8b-instruct"
spec:
  accessModes:
  - ReadWriteOnce
  volumeMode: Filesystem
  resources:
    requests:
      storage: 100Gi
  storageClassName: local-sc
```

## Downloading Models to Local Storage

This proposal introduces a new controller for applying the desired LPVCs state as specified by the annotations defined above. This controller watches LPVCs in the cluster and performs the following actions depending on its operating mode:

* **agent mode**. An agent is created for each local Persistent Volume Claim (PVC). This agent is deployed on the node matching the PVC. It monitors and updates the PVC’s annotations and ensures that the required models are cached in the PV.
* **job mode.** A k8s job is created each time the PVC desired state is updated (ie. created, updated and deleted).

In general, the agent mode is more suitable for a system serving a wide variety of models whereas the other mode is considered lightweight when the system serves few models.

## Future Work

Here are potential future work items:

* Cache models in CPU RAM (Page cache).
* Cache eviction.
* Support for other storage types (S3, HTTP, etc..).
* Reformat model files to be compatible with specific model loaders (eg. Tensorizer) and sharding techniques (TP, PP).
* Caching pytorch compilation artifacts.
* Better UX.

# Alternatives

## Reusing [KServe Local Model Cache](https://kserve.github.io/website/master/modelserving/storage/modelcache/localmodel/)

KServe local model cache provides an opinionated way to cache models on local storage and has been designed to be an optional KServe extension, as opposed to an independent component. This proposal borrows many ideas from KServe local model cache and adapts them to fit nicely with llm-d.

## Defining CRDs instead of annotations

This proposal is about enabling caching models on local storage using existing mechanisms provided by Kubernetes and GIE. Another proposal may be created in the future to capture common patterns and best practices, and it may define new CRDs.

## OCI Volume Source

OCI Volume Source is a new Kubernetes feature, tracked by [KEP 4639](https://github.com/kubernetes/enhancements/issues/4639), allowing mounting container images directly as read-only volumes in Kubernetes Pods. Kubernetes v1.33 has promoted the OCI Volume Source feature to Beta. The kubelet initiates the pull for the OCI object based on the volume source and stores this object at a statically defined location set by cluster admin. OCIVolumeSource does not offer the level of flexibility and guarantees required for fast model loading, such as NVMes and large volume size.

## Volumes Populators

[Volume Populator](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#volume-populators-and-data-sources) lets you preload data from a source storage to a destination PersistentVolumeClaim during dynamic provisioning. The downside is that the flow is only triggered during volume creation; it does not allow adding or removing data after volume creation.
