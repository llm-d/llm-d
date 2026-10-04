# Platform setup

This section covers the hardware and software requirements for llm-d, cluster configuration, accelerator specs, and platform adaptations across diverse physical execution environments.

### [Kubernetes Infrastructure Providers](providers/README.md)

Provider-specific cluster setup notes (GKE, AKS, OpenShift, Minikube, DigitalOcean).

### [Multi-Node Serving Orchestration](../capabilities/multi-node.md)

Deploying multi-host inference workloads with LeaderWorkerSet (LWS) and Topology Aware Scheduling.

### [Non-Kubernetes & Bare-Metal Deployments](without-kubernetes.md)

Running the llm-d routing stack on bare metal, HPC Slurm schedulers, or Ray via file-based worker discovery.

### [Fast Internode Networking & RDMA](networking-rdma.md)

Orchestrating multi-host replica topologies and RDMA networking fabrics.

### [Gateway & Ingress Resources](gateways/README.md)

Configuring ingress controllers, Gateway API, and service meshes.

---
