## 🛠️ CI/CD Pipeline Overview – Your Project

<!-- NOTE TO CONTRIBUTORS: every repo in the hc4ai organization is intended to have the same contents in this file. The origin is the copy in https://github.ibm.com/mspreitz/hc4ai-hello-neural/blob/dev/.tekton/README.md; submit PRs against that one -->

This pipeline is designed to support safe, efficient, and traceable development and deployment workflows using [OpenShift Pipelines-as-Code](https://pipelinesascode.com/), [Tekton](https://tekton.dev/), [buildah](https://buildah.io/), GitHub, and Quay.io.

This pipeline is used for CI/CD of the `dev` and `main` branches. This pipeline runs from source through container image build to deployment and testing in the hc4ai cluster.

---

### 🔀 Branch Strategy
We use two main branches in each repo:

- **dev** – For active development, testing, and preview builds
- **main** – For production-ready code and deployments

### 📄 About .version.json
Each repo includes a `.version.json` file at its root. This file controls:

```json
{
  "dev-version": "0.0.5",
  "dev-registry": "quay.io/llm-d/<your project name>-dev",
  "prod-version": "0.0.4",
  "prod-registry": "quay.io/llm-d/<your project name>"
}
```

#### 🔑 Fields:
- **dev-version**: Current version of the dev branch. Used to tag dev images.
- **dev-registry**: Container repository location for development image pushes.
- **prod-version**: Managed by automation. Updated during promotion to match the dev-version.
- **prod-registry**: Container repository for production image pushes. The promoted dev image is re-tagged and pushed here.

The pipeline reads this file to:
- Extract the appropriate version tag
- Determine the correct repository for image pushes
- Promote and tag dev images for prod

---

### Container Repositories

This pipeline maintains two container repositories for this GitHub repository, as follows.

- `quay.io/llm-d/<repoName>-dev`. Hold builds from the `dev` branch as described below.
- `quay.io/llm-d/<repoName>`. Holds promotions to prod, as described below.

---

### ⚙️ Pipeline Triggers
Triggered on `push` and `pull_request` events targeting the `dev` or `main` branches. The following workflows are the two behaviors of this pipeline.

### 🔧 dev Branch Workflow
1. Checkout repository
2. Lint, test, and build the Go application
3. Read `.version.json` to extract:
    - dev-version
    - dev-registry
    - prod-version
    - prod-registry
4. Build and push container image to:
   → `<dev-repository>:<dev-version>`
5. Tag the Git commit using the `dev-version`
6. Optionally redeploy objects to OpenShift in the `hc4ai-operator-dev` namespace.

✅ This process ensures that all code merged into dev is validated and deployed for testing.

### 🚀 main Branch Workflow
1. Checkout, lint, test, and parse `.version.json`
2. Skip image rebuild
3. Promote image by copying from:
   → `<dev-repository:<dev-version>` → `<prod-repository>:<prod-version>`
4. Tag the Git commit using the `prod-version`
5. Update the upstream repo’s submodule to reference the new tag
6. Redeploy to OpenShift in the `hc4ai-operator` namespace.

✅ No image rebuilds occur on main. Only validated dev images are promoted, ensuring reproducibility.

---

### 🏷️ Git Tagging
Each time a pipeline runs:
- **dev branch** → Tags the commit with the current `dev-version`
- **main branch** → Tags the commit with the current `prod-version`

Tags are created using the configured Git credentials and pushed to the remote repo.

---

### 📦 Submodule Management
- Submodules are only updated on main
- The submodule commit is pushed to the upstream repo
- Reflects the most recent promoted version/tag

---

### ☸️ OpenShift Deployment
The pipeline includes automated deployment:
- On `dev`: Deploys to the `hc4ai-operator-dev` namespace. The Pod is named `<repoName>-major-minor`, using the `dev-version` from `.version.json`.
- On `main`: Deploys to `hc4ai-operator` namespace. The Pod is named `<repoName>-major-minor`, using the `prod-version` from `.version.json`.

Using `make uninstall-openshift` and `make install-openshift`, resources are cleanly reset.

After deployment, the pipeline:
- Waits and checks the current pod, deployment, service, and route status
- Ensures the promoted code is successfully running in the appropriate namespace

---

### 🧠 Key Benefits
- 🔄 Reusable artifacts: Images built once in dev are reused in main
- ✅ Safe promotion: No differences between tested and released versions
- 🔍 Traceability: Version tags link Git commits to builds and deployments
- ☁️ Consistent deployment: Controlled via Makefile and namespaced environments

---

### 🧰 Developer Notes
- Always branch off `dev` for new work
- Submit PRs to `dev` for image builds and testing
- Merge `dev` to `main` to promote and deploy to production

---

### 🧠 Why `.version.json` Matters
- Decouples versioning from Git commit hashes
- Provides a single source of truth for version and repository info
- Enables deterministic builds and controlled releases
- Simplifies debugging and auditing across environments

