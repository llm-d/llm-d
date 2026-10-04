# llm-d Infrastructure Providers

This directory contains documentation specific to each Kubernetes provider for deploying llm-d, as well as troubleshooting and known issues.

## Other providers

To add a new infrastructure provider to our well-lit paths, we request the following support:

* Documentation on configuring the platform to support one or more [well-lit path guides](../../../guides/README.md)
* The appropriate configuration contributed to the guide to deal with provider-specific variations
* An automated test environment that validates the supported guides
* At least one documented platform maintainer who responds to GitHub issues and is available for regular discussion in the llm-d slack channel `#sig-installation`.

## Tested providers

The following documentation describes llm-d tested setup for cluster infrastructure providers as well as specific deployment settings that will impact how model servers is expected to access accelerators.

* [Azure Kubernetes Service (AKS)](providers/aks/README.md)
* [DigitalOcean Kubernetes (DOKS)](providers/digitalocean/README.md)
* [Google Kubernetes Engine (GKE)](providers/gke/README.md)
* [OpenShift (OCP)](providers/openshift/README.md), [OpenShift on AWS](providers/openshift-aws/README.md)
* [minikube](providers/minikube/README.md) for single-host development

These provider configurations are tested regularly.

Please follow the provider-specific documentation to ensure your Kubernetes cluster and hardware is properly configured before continuing.
