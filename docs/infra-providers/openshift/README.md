# llm-d on OpenShift

## Prerequisites

### Platform Setup

- OpenShift - This guide was tested on OpenShift 4.17. Older versions may work but have not been tested.
- NVIDIA GPU Operator and NFD Operator - The installation instructions can be found [here](https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/steps-overview.html).
- NO Service Mesh or Istio installation as Istio CRDs will conflict with the gateway
- Cluster administrator privileges are required to install the llm-d cluster scoped resources

## Installation

### Clone this repository
```sh
git clone https://github.com/llm-d/llm-d.git
cd llm-d
```

### Install Dependencies 
Go to the `guides/prereq` directory and run `client-setup/install-deps.sh`. 
This script detects your OS and uses its package management to install required utilities like `yq`, `helm`, etc.

```sh
cd guides/prereq
./client-setup/install-deps.sh
```

### Deploy the gateway and infrastructure
Switch to the `gateway-providers` directory and run the `install-gateway-provider-dependencies.sh` script located in `guides/prereqs/gateway-provider`
```sh
cd gateway-provider     
./install-gateway-provider-dependencies.sh
helmfile apply -f istio.helmfile.yaml
```


