# Client Setup Prerequisites

[install-deps.sh](./install-deps.sh) downloads and installs the following client-side tools listed below for use with the llm-d guides.

## Required Tools

| Binary      | Minimum Required Version | Download / Installation Instructions                                                            |
| ----------- | ------------------------ | ----------------------------------------------------------------------------------------------- |
| `yq`        | v4+                      | [yq (mikefarah) – installation](https://github.com/mikefarah/yq?tab=readme-ov-file#install)     |
| `git`       | v2.30.0+                 | [git – installation guide](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)       |
| `helm`      | v3.12.0+                 | [Helm – quick-start install](https://helm.sh/docs/intro/install/)                               |
| `helmfile`  | v1.1.0+                  | [Helmfile - installation](https://github.com/helmfile/helmfile?tab=readme-ov-file#installation) |
| `kubectl`   | v1.28.0+                 | [kubectl – install & setup](https://kubernetes.io/docs/tasks/tools/install-kubectl/)            |
| `kustomize` | v5.0.0+                  | [Kustomize – installation](https://kubectl.docs.kubernetes.io/installation/kustomize/)          |

### Optional Tools

| Binary             | Recommended Version      | Download / Installation Instructions                                                             | Notes                                                          |
| ------------------ | ------------------------ | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| `stern`            | 1.30+                    | [stern - installation](https://github.com/stern/stern?tab=readme-ov-file#installation)           |                                                                |
| `helm diff` plugin | v3.10.0+                 | [helm diff installation docs](https://github.com/databus23/helm-diff?tab=readme-ov-file#install) |                                                                |
| `timeout`          | -                        | Part of `coreutils` package                                                                      | Required for benchmark scripts                                 |

## GKE-managed Gateway API CRDs

On GKE with the Gateway API enabled, GKE owns the Gateway API and `InferencePool` CRDs. A guide's
`kubectl apply` of a newer Gateway API Inference Extension manifest can report success, but the GKE addon
restores its bundled schema shortly afterwards. Check the effective owner and bundle before applying the guide's
CRD step:

```bash
kubectl get crd inferencepools.inference.networking.k8s.io \
  -o jsonpath='{.metadata.annotations.components\.gke\.io/component-name}{"\t"}{.metadata.annotations.inference\.networking\.k8s\.io/bundle-version}{"\n"}'
```

If the first value is `gateway-api-crds`, skip the manual `InferencePool` CRD apply. The second value is the
schema version that remains effective. Confirm that version supports the fields required by the guide; applying a
newer upstream manifest does not override the addon. See [Installing Gateway API CRDs](../../docs/infrastructure/gateway/install-crds.md)
and Google's [GKE Inference Gateway documentation](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway).
