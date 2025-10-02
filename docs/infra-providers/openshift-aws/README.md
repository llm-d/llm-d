# llm-d on Red Hat OpenShift on AWS

This document serves as a reference guide, pointing you to the official Red Hat and AWS documentation, as well as NVIDIA documentation, for setting up a Red Hat OpenShift Service on AWS (ROSA) cluster with NVIDIA GPU support.

## Prerequisites

Before you begin, ensure you meet the prerequisites outlined in the official documentation for ROSA:

- An AWS account with necessary permissions for ROSA.
- A Red Hat account and access to the Red Hat OpenShift Cluster Manager.
- Required tools (like the rosa CLI) and familiarity with ROSA concepts.

Refer to the official ROSA documentation for detailed prerequisites:
[https://docs.aws.amazon.com/rosa/latest/userguide/prerequisites.html](https://docs.aws.amazon.com/rosa/latest/userguide/prerequisites.html)

## Step 1: Create a Red Hat OpenShift Service on AWS (ROSA) Cluster

The primary method for creating a ROSA cluster is through the Red Hat OpenShift Cluster Manager or using the ROSA CLI.

For detailed instructions on creating your ROSA cluster, refer to the official documentation:

- **Creating a ROSA cluster:** [https://docs.aws.amazon.com/rosa/latest/userguide/create-cluster.html](https://docs.aws.amazon.com/rosa/latest/userguide/create-cluster.html)
- **Getting started with ROSA:** [https://docs.aws.amazon.com/rosa/latest/userguide/getting-started.html](https://docs.aws.amazon.com/rosa/latest/userguide/getting-started.html)

## Step 2: Add a Machineset with NVIDIA GPU Instances

To add worker nodes with GPU capabilities to your ROSA cluster, you will create a new machineset configured to use AWS instance types that include NVIDIA GPUs. This is done through the OpenShift console or oc CLI after the ROSA cluster is provisioned.

Learn how to manage machinesets and configure them for specific instance types in the official documentation:

- **Managing machinesets in OpenShift (applies to ROSA worker nodes):** [https://docs.openshift.com/container-platform/latest/machine\_management/creating-machinesets.html](https://docs.openshift.com/container-platform/latest/machine_management/creating-machinesets.html)
- **Adding machinesets with specific instance types (referencing AWS GPU instances):** Consult the AWS documentation for available GPU instance types (e.g., P, G, and Inf instances) and use the OpenShift machineset documentation to configure your machineset accordingly. [https://aws.amazon.com/ec2/instance-types/](https://aws.amazon.com/ec2/instance-types/)

## Step 3: Enable GPU support on OpenShift with the NFD and GPU Operators

To enable GPU acceleration on your GPU-enabled nodes, you need to install and configure both the Node Feature Discovery (NFD) Operator and the NVIDIA GPU Operator. The NVIDIA documentation provides comprehensive steps for this process on OpenShift.

Refer to the NVIDIA documentation for detailed instructions on installing and configuring both operators:

- **Enabling GPU Support on OpenShift (covers NFD and GPU Operator installation):** [https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/steps-overview.html](https://docs.nvidia.com/datacenter/cloud-native/openshift/latest/steps-overview.html)

## Next Steps

Once the operators are installed and configured, you can proceed with deploying llm-d using the llm-d quick start.

See the [QUICKSTART.md](../../../guides/QUICKSTART.md) for more information.
