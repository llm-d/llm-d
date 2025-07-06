# Step-by-step installation with kgateway

## Deploy llm-d

### 1. Installing GAIE Kubernetes infrastructure

Apply CRDs for Gateway API.

```bash
kubectl apply -k https://github.com/llm-d/llm-d-inference-scheduler/deploy/components/crds-gateway-api
```

Then, Apply CRDs for Gateway API Inference Extention.

```bash
kubectl apply -k https://github.com/llm-d/llm-d-inference-scheduler/deploy/components/crds-gie
```

### 2. Install kgateway

Apply kgateway CRD.

```bash
helm upgrade -i \
    --namespace kgateway-system \
    --create-namespace \
    --version v2.0.3 \
    kgateway-crds oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds
```

After that, deploy kgateway.

```bash
helm upgrade -i \
    --namespace kgateway-system \
    --create-namespace \
    --version v2.0.3 \
    --set inferenceExtension.enabled=true \
    --set securityContext.allowPrivilegeEscalation=false \
    --set securityContext.capabilities.drop={ALL} \
    --set podSecurityContext.seccompProfile.type=RuntimeDefault \
    --set podSecurityContext.runAsNonRoot=true \
    kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway
```

The resources are created as follows. 

```bash
kubectl get pods,svc -n kgateway-system
NAME                           READY   STATUS    RESTARTS   AGE
pod/kgateway-ddbb7668c-8vjz8   1/1     Running   0          114s

NAME               TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/kgateway   ClusterIP   [Cluster IP]    <none>        9977/TCP   114s
```

### 3. Creating HF token secret

Create a namespace to deploy llm-d.

```bash
export NAMESPACE="llm-d"
kubectl create ns "${NAMESPACE}"
```

Then create a secret to clone the models from HuggingFace.

```bash
export HF_TOKEN="<HF Token>"
kubectl create secret generic llm-d-hf-token \
    --namespace "${NAMESPACE}" \
    --from-literal=HF_TOKEN="${HF_TOKEN}" \
    --dry-run=client -o yaml | kubectl apply -n "${NAMESPACE}" -f -
```

### 4. Install llm-d

Apply modelservice CRD.

```bash
kubectl apply -f https://raw.githubusercontent.com/llm-d/llm-d-deployer/refs/heads/main/charts/llm-d/crds/modelservice-crd.yaml
```


Clone the llm-d-deployer repository and change directory.

```bash
git clone https://github.com/llm-d/llm-d-deployer.git
cd llm-d-deployer/charts/llm-d
```

Resolve the helm package's dependencies.

```bash
helm repo add bitnami  https://charts.bitnami.com/bitnami
helm dependency build .
```

Deploy llm-d.

```bash
helm upgrade -i llm-d . --namespace "${NAMESPACE}" \
--set gateway.gatewayClassName=kgateway \
--set gateway.kGatewayParameters.proxyUID=0 \
--set ingress.clusterRouterBase="" \
--set modelservice.metrics.enabled=false \
--set modelservice.epp.metrics.enabled=false \
--set modelservice.vllm.metrics.enabled=false  \
--set sampleApplication.enabled=false
```

If you've already deployed kube-prometheus-stack, you can deploy llm-d with `modelservice.metrics.enabled=true` option to create ServiceMonitor resources.

```bash
helm upgrade -i llm-d . --namespace "${NAMESPACE}" \
--set gateway.gatewayClassName=kgateway \
--set gateway.kGatewayParameters.proxyUID=0 \
--set ingress.clusterRouterBase="" \
--set modelservice.metrics.enabled=true \
--set sampleApplication.enabled=false
```

llm-d resources are created as below.

```bash
kubectl get pods,svc,gateway -n llm-d
```

```bash
NAME                                           READY   STATUS    RESTARTS   AGE
pod/llm-d-inference-gateway-6c5786bf77-rtpgq   1/1     Running   0          102s
pod/llm-d-modelservice-57d64db5c8-cqfkn        1/1     Running   0          102s
pod/llm-d-redis-master-5f85898675-wgq4d        1/1     Running   0          102s

NAME                              TYPE           CLUSTER-IP       EXTERNAL-IP        PORT(S)        AGE
service/llm-d-inference-gateway   LoadBalancer   [Cluster IP]     [LoadBalancer IP]  80:31924/TCP   103s
service/llm-d-modelservice        ClusterIP      [Cluster IP]     <none>             8443/TCP       103s
service/llm-d-redis-headless      ClusterIP      None             <none>             8100/TCP       103s
service/llm-d-redis-master        ClusterIP      [Cluster IP]     <none>             8100/TCP       103s

NAME                                                        CLASS      ADDRESS            PROGRAMMED   AGE
gateway.gateway.networking.k8s.io/llm-d-inference-gateway   kgateway   [LoadBalancer IP]  True         103s
```

## Validation

Currently, You can apply ModelService to deploy inference service.

```YAML
apiVersion: llm-d.ai/v1alpha1
kind: ModelService
metadata:
  name: meta-llama-llama-3-2-3b-instruct
  namespace: llm-d
spec:
  baseConfigMapRef:
    name: basic-gpu-with-nixl-and-redis-lookup-preset
  modelArtifacts:
    uri: hf://meta-llama/Llama-3.2-3B-Instruct
  prefill:
    containers:
    - args:
      - --served-model-name
      - meta-llama/Llama-3.2-3B-Instruct
      env:
      - name: HF_TOKEN
        valueFrom:
          secretKeyRef:
            key: HF_TOKEN
            name: llm-d-hf-token
      name: vllm
      resources:
        limits:
          nvidia.com/gpu: "1"
    replicas: 1
  decode:
    containers:
    - args:
      - --served-model-name
      - meta-llama/Llama-3.2-3B-Instruct
      env:
      - name: HF_TOKEN
        valueFrom:
          secretKeyRef:
            key: HF_TOKEN
            name: llm-d-hf-token
      name: vllm
      resources:
        limits:
          nvidia.com/gpu: "1"
    replicas: 1
  endpointPicker:
    containers:
    - name: epp
    replicas: 1
  routing:
    modelName: meta-llama/Llama-3.2-3B-Instruct
  decoupleScaling: false
```


ModelService resources are created. 

```bash
kubectl get pods -n llm-d
```
```bash
NAME                                                       READY   STATUS    RESTARTS   AGE
llm-d-inference-gateway-istio-69cbf58fb4-ckzkw             1/1     Running   0          19m
llm-d-modelservice-574d4f76b8-98qpv                        1/1     Running   0          19m
llm-d-redis-master-5f77dd4bf9-4s5sp                        1/1     Running   0          19m
meta-llama-llama-3-2-3b-instruct-decode-6f5c75fc45-rbndl   2/2     Running   0          32s
meta-llama-llama-3-2-3b-instruct-epp-6f5556dddd-x99s5      1/1     Running   0          32s
meta-llama-llama-3-2-3b-instruct-prefill-d85997579-f7mts   1/1     Running   0          32s
```