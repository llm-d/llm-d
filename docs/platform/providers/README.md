# llm-d Infrastructure Providers

This directory contains documentation specific to each Kubernetes provider for deploying llm-d, as well as troubleshooting and known issues.

## Tested providers

The following documentation describes llm-d tested setup for cluster infrastructure providers as well as specific deployment settings that will impact how model servers is expected to access accelerators.

* [Azure Kubernetes Service (AKS)](aks.md)
* [DigitalOcean Kubernetes (DOKS)](digitalocean.md)
* [Google Kubernetes Engine (GKE)](gke.md)
* [OpenShift (OCP)](openshift.md), [OpenShift on AWS](openshift-aws.md)
* [minikube](minikube.md) for single-host development

These provider configurations are tested regularly.

Please follow the provider-specific documentation to ensure your Kubernetes cluster and hardware is properly configured before continuing.
