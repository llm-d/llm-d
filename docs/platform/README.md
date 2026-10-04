# Infrastructure & Environments

This section covers the hardware and software requirements for llm-d, cluster configuration, accelerator specs, and platform adaptations across diverse physical execution environments.

### [Kubernetes Infrastructure Providers](providers/README.md)

Provider-specific cluster setup notes (GKE, AKS, OpenShift, Minikube, DigitalOcean).

### [Multi-Node Serving Orchestration](multi-node.md)

Deploying multi-host inference workloads with LeaderWorkerSet (LWS) and Topology Aware Scheduling.

### [Non-Kubernetes & Bare-Metal Deployments](no-kubernetes-deployment.md)

Running the llm-d routing stack on bare metal, HPC Slurm schedulers, or Ray via file-based worker discovery.

### [Fast Internode Networking & RDMA](rdma/README.md)

Orchestrating multi-host replica topologies and RDMA networking fabrics.

### [Gateway & Ingress Resources](gateway/README.md)

Configuring ingress controllers, Gateway API, and service meshes.

---
