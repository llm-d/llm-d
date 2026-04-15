# Client Setup Prerequisites

llm-d well-lit path guides use various common client-side tools on Linux and Mac OSX. The provided [install-deps.sh](./install-deps.sh) script will download and install the tools below.

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

