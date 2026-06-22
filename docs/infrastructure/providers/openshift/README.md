# llm-d on OpenShift

This document covers configuring OpenShift clusters for running high performance LLM inference with llm-d.

For deployment instructions, see the [well-lit path guides](../../../../guides/).

## Prerequisites

llm-d on OpenShift is tested with the following configurations:

* Versions: OpenShift 4.19, 4.20, 4.21
* Ensure no ServiceMesh(OSSM) or Istio installations exist on the cluster — included CRDs may conflict with the llm-d gateway component
* Cluster administrator privileges are required to install cluster-scoped resources

## Cluster Configuration

### GPU Setup

Install the NFD (Node Feature Discovery) and NVIDIA GPU Operators before deploying llm-d workloads.

The [ocp-gpu-setup](https://github.com/rh-aiservices-bu/ocp-gpu-setup) repo provides guided scripts for provisioning GPU nodes and deploying the required operators on AWS:

```bash
git clone https://github.com/rh-aiservices-bu/ocp-gpu-setup.git
cd ocp-gpu-setup

# Configure GPU MachineSet
./machine-set/gpu-machineset.sh

# Deploy NFD Operator
oc apply -f ./nfd

# Deploy NVIDIA GPU Operator
oc apply -f ./gpu-operator

# Apply supporting CRs
oc apply -f ./crs
```

### GPU Node Taints

GPU nodes on OpenShift may have taints applied (e.g. `nvidia.com/gpu: NVIDIA-L40S-PRIVATE`). If model server pods are stuck in `Pending`, add the appropriate toleration to the deployment:

```bash
oc patch deployment <deployment-name> \
  -p '{"spec":{"template":{"spec":{"tolerations":[{"key":"nvidia.com/gpu","operator":"Equal","value":"NVIDIA-L40S-PRIVATE","effect":"NoSchedule"}]}}}}'
```

## Deploying llm-d

Follow the [well-lit path guides](../../../../guides/) to deploy llm-d workloads. Each guide includes OpenShift-specific steps where applicable.

Use `oc` in place of `kubectl` for OpenShift CLI commands, or configure `kubectl` to use your OpenShift cluster credentials via `oc login`.
