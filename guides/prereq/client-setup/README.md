# Client Setup Prerequisites

llm-d guides use a standard set of client tools on Linux and Mac OSX. The provided [install-deps.sh](./install-deps.sh) script will download and install the tools below.

## Supported Development Platforms

Currently llm-d community only supports OSX and Linux development.

## Required Tools

| Binary      | Minimum Required Version | Download / Installation Instructions                                                            |
| ----------- | ------------------------ | ----------------------------------------------------------------------------------------------- |
| `yq`        | v4+                      | [yq (mikefarah) – installation](https://github.com/mikefarah/yq?tab=readme-ov-file#install)     |
| `git`       | v2.30.0+                 | [git – installation guide](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git)       |
| `helm`      | v3.12.0+                 | [Helm – quick-start install](https://helm.sh/docs/intro/install/)                               |
| `helmfile`  | v1.1.0+                  | [Helmfile - installation](https://github.com/helmfile/helmfile?tab=readme-ov-file#installation) |
| `kubectl`   | v1.28.0+                 | [kubectl – install & setup](https://kubernetes.io/docs/tasks/tools/install-kubectl/)            |

### Optional Tools

| Binary             | Recommended Version      | Download / Installation Instructions                                                             |
| ------------------ | ------------------------ | ------------------------------------------------------------------------------------------------ |
| `stern`            | 1.30+                    | [stern - installation](https://github.com/stern/stern?tab=readme-ov-file#installation)           |
| `helm diff` plugin | v3.10.0+                 | [helm diff installation docs](https://github.com/databus23/helm-diff?tab=readme-ov-file#install) |

## HuggingFace Token

Most guides download their model from Huggingface directly in the `llm-d` image. There are exceptions to this like the [`simulated-accelerators` guide](../../simulated-accelerators/) that uses no model, or the [`wide-ep-lws` guide](../../wide-ep-lws/) which uses a model loaded from storage directly on the nodes for faster development cycle iterations.

For the rest you will need to create a Kubernetes secret in your deployment namespace containing your HuggingFace Token. For more information on getting a token, see [the huggingface docs](https://huggingface.co/docs/hub/en/security-tokens).

The following script will create the token in the current namespace using the name `llm-d-hf-token`, which is used in all guides:

```bash
export HF_TOKEN=<from Huggingface>
export HF_TOKEN_NAME=${HF_TOKEN_NAME:-llm-d-hf-token}
kubectl create secret generic ${HF_TOKEN_NAME} \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
```

## Pulling llm-d Images from GitHub Container Registry (GHCR)

All of the container images in the `llm-d` organization are public on GitHub and require no authentication to pull.
