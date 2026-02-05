# Google TPU P/D Disaggregation Deployment Guide

## Overview

This document provides complete steps for deploying a PD (Prefill-Decode) disaggregation service on a Google Kubernetes Engine (GKE) cluster using the Llama-3.3-70B-Instruct model. PD disaggregation separates the prefill and decode phases of inference, enabling more efficient resource utilization and improved throughput.

For broader context or GPU setup, refer to this [p/d guide](./README.md)

## Hardware Requirements

This guide uses Cloud TPU v6e (Trillium) accelerators on Google Cloud Platform (GCP), specifically the `ct6e-standard-8t` machine type. You may also choose other compatible TPU VM types.

## Prerequisites

- Have the [proper client tools installed on your local system](../prereq/client-setup/README.md) to use this guide.
- Create a namespace for installation.

  ```bash
  export NAMESPACE=llm-d-pd # or any other namespace (shorter names recommended)
  kubectl create namespace ${NAMESPACE}
  ```

- [Create the `llm-d-hf-token` secret in your target namespace with the key `HF_TOKEN` matching a valid HuggingFace token](../prereq/client-setup/README.md#huggingface-token) to pull models.
- [Choose an llm-d version](../prereq/client-setup/README.md#llm-d-version)

## Installation Steps

The following steps detail a fresh deployment of a PD disaggregation service on GKE using TPU accelerators. If you are using existing infrastructure, skip the relevant steps.

### Step 1 Prepare GKE Cluster

Please refer to [llm-d on GKE Documentation](../../docs/infra-providers/gke/README.md) to properly setup GKE cluster and GKE Inference Gateway.

### Step 2 Install the Stack

#### 2.1 Install the stack via helmfile

Use the helmfile to compose and install the stack. The Namespace in which the stack will be deployed will be derived from the ${NAMESPACE} environment variable. If you have not set this, it will default to llm-d-pd in this example.

```bash
cd guides/pd-disaggregation
helmfile apply -e gke_tpu -n ${NAMESPACE}
```

#### 2.2 Install HTTPRoute

Apply the HTTPRoute configuration:

```bash
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

## Verify the Installation

- Firstly, you should be able to list all helm releases to view the 3 charts got installed into your chosen namespace:

```bash
helm list -n ${NAMESPACE}
NAME     NAMESPACE  REVISION UPDATED                                 STATUS   CHART                      APP VERSION
gaie-pd  llm-d-pd   1        2025-11-07 00:31:54.106881562 +0000 UTC deployed inferencepool-v1.3.0       v1.3.0
infra-pd llm-d-pd   1        2025-11-07 00:31:50.355629868 +0000 UTC deployed llm-d-infra-v1.3.6         v0.3.0
ms-pd    llm-d-pd   7        2025-11-07 17:45:30.946563039 +0000 UTC deployed llm-d-modelservice-v0.4.5   v0.4.0
```

- Out of the box with this example you should have the following resources:

```bash
kubectl get all -n ${NAMESPACE}
NAME                                                    READY   STATUS    RESTARTS   AGE
pod/gaie-pd-epp-5c7454f499-srrws                        1/1     Running   0          19h
pod/ms-pd-llm-d-modelservice-decode-666666bcb4-jmfzg    2/2     Running   0          137m
pod/ms-pd-llm-d-modelservice-prefill-855b6d74cc-7s66s   1/1     Running   0          136m
pod/ms-pd-llm-d-modelservice-prefill-855b6d74cc-tmwt2   1/1     Running   0          136m

NAME                           TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)             AGE
service/gaie-pd-epp            ClusterIP   123.123.123.1   <none>        9002/TCP,9090/TCP   19h
service/gaie-pd-ips-bb618139   ClusterIP   None            <none>        54321/TCP           19h

NAME                                               READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/gaie-pd-epp                        1/1     1            1           19h
deployment.apps/ms-pd-llm-d-modelservice-decode    1/1     1            1           19h
deployment.apps/ms-pd-llm-d-modelservice-prefill   2/2     2            2           19h

NAME                                                          DESIRED   CURRENT   READY   AGE
replicaset.apps/gaie-pd-epp-5c7454f499                        1         1         1       19h
replicaset.apps/ms-pd-llm-d-modelservice-decode-666666bcb4    1         1         1       137m
replicaset.apps/ms-pd-llm-d-modelservice-prefill-855b6d74cc   2         2         2       137m
```

- The GKE Gateway should also be deployed in the namespace. Verify the GKE Gateway is programmed and has an address:

```bash
kubectl get gateway  -n ${NAMESPACE}

NAME                         CLASS                              ADDRESS          PROGRAMMED   AGE
infra-pd-inference-gateway   gke-l7-regional-external-managed   123.123.123.123   True         19h
```

**_NOTE:_** This assumes no other guide deployments in your given `${NAMESPACE}` and you have not changed the default release names via the `${RELEASE_NAME}` environment variable.

## Using the stack

1. Get the endpoint of GKE Gateway using below command

    ```bash
    export ENDPOINT="http://$(kubectl get gateway -n ${NAMESPACE} -o jsonpath='{.items[0].status.addresses[0].value}')"
    echo "Using endpoint: $ENDPOINT"
    ```

1. Try curling the `/v1/models` endpoint:

    ```bash
    curl -s ${ENDPOINT}/v1/models \
    -H "Content-Type: application/json" | jq
    ```

    Expected output:

    ```json
    {
    "object": "list",
    "data": [
        {
        "id": "meta-llama/Llama-3.3-70B-Instruct",
        "object": "model",
        "created": 1762546640,
        "owned_by": "vllm",
        "root": "meta-llama/Llama-3.3-70B-Instruct",
        "parent": null,
        "max_model_len": 32000,
        "permission": [
            {
            "id": "modelperm-646be61270214738ab306c941871b7d6",
            "object": "model_permission",
            "created": 1762546640,
            "allow_create_engine": false,
            "allow_sampling": true,
            "allow_logprobs": true,
            "allow_search_indices": false,
            "allow_view": true,
            "allow_fine_tuning": false,
            "organization": "*",
            "group": null,
            "is_blocking": false
            }
        ]
        }
    ]
    }
    ```

1. Now lets try hitting the `/v1/completions` endpoint (this is model dependent, ensure your model matches what the server returns for the `v1/models` curl).

    ```bash
    curl -X POST ${ENDPOINT}/v1/completions \
    -H 'Content-Type: application/json' \
    -d '{
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "max_tokens": 64,
        "prompt": "How are you today?"
    }' | jq
    ```

    Expected output:

    ```json
    {
    "choices": [
        {
        "finish_reason": "length",
        "index": 0,
        "logprobs": null,
        "prompt_logprobs": null,
        "prompt_token_ids": null,
        "stop_reason": null,
        "text": " I hope you are having a great day. I am doing well, thanks for asking. I just wanted to share with you a few things that I have been thinking about lately. I have been thinking about how important it is to take care of ourselves, both physically and mentally. It is so easy to get caught up",
        "token_ids": null
        }
    ],
    "created": 1762546790,
    "id": "cmpl-ef5fcbcb-4cd7-4c7b-a037-0333cc1f6a44",
    "kv_transfer_params": {
        "remote_block_ids": [
        5
        ],
        "remote_host": "10.52.5.4",
        "remote_port": "9100",
        "uuid": 1223960640850184400
    },
    "model": "meta-llama/Llama-3.3-70B-Instruct",
    "object": "text_completion",
    "service_tier": null,
    "system_fingerprint": null,
    "usage": {
        "completion_tokens": 64,
        "prompt_tokens": 6,
        "prompt_tokens_details": null,
        "total_tokens": 70
    }
    }
    ```

For more information see [our docs](../../docs/getting-started-inferencing.md)

## Tuning Selective PD

Selective PD is a feature in the `inference-scheduler` within the context of prefill-decode dissagregation, although it is disabled by default. This features enables routing to just decode even with the P/D deployed.

For information on this plugin, see our [`pd-profile-handler` docs in the inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler/blob/v0.5.0/docs/architecture.md?plain=1#L205-L210)

## Cleanup

To remove the deployment:

```bash
# Remove the model services
helmfile destroy -n ${NAMESPACE}

# Remove the infrastructure
helm uninstall ms-pd -n ${NAMESPACE}
helm uninstall gaie-pd -n ${NAMESPACE}
helm uninstall infra-pd -n ${NAMESPACE}
```

**_NOTE:_** If you set the `$RELEASE_NAME_POSTFIX` environment variable, your release names will be different from the command above: `infra-$RELEASE_NAME_POSTFIX`, `gaie-$RELEASE_NAME_POSTFIX` and `ms-$RELEASE_NAME_POSTFIX`.

### Cleanup HTTPRoute

```bash
kubectl delete -f httproute.gke.yaml -n ${NAMESPACE}
```

## Customization

For information on customizing a guide and tips to build your own, see [our docs](../../docs/customizing-a-guide.md)
