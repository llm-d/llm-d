## Contributing Guidelines

Thank you for your interest in contributing to llm-d. Community involvement is highly valued and crucial for the project's growth and success. The llm-d project accepts contributions via GitHub pull requests. This outlines the process to help get your contribution accepted.

To ensure a clear direction and cohesive vision for the project, the project leads have the final decision on all contributions. However, these guidelines outline how you can contribute effectively to llm-d.

## How You Can Contribute

There are several ways you can contribute to llm-d:

* **Reporting Issues:** Help us identify and fix bugs by reporting them clearly and concisely.
* **Suggesting Features:** Share your ideas for new features or improvements.
* **Improving Documentation:** Help make the project more accessible by enhancing the documentation.
* **Submitting Code Contributions (with consideration):** While the project leads maintain final say, code contributions that align with the project's vision are always welcome.

## Code of Conduct

This project adheres to the llm-d [Code of Conduct and Covenant](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Community and Communication

* **Developer Slack:** [Join our developer Slack workspace](https://llm-d.ai/slack) to connect with the core maintainers and other contributors, ask questions, and participate in discussions.
* **Weekly Meetings:** Project updates, ongoing work discussions, and Q&A will be covered in our weekly project meeting every Wednesday at 12:30 PM ET. Please join by [adding the shared calendar](https://red.ht/llm-d-public-calendar). You can also [join our Google Group](https://groups.google.com/g/llm-d-contributors) for access to shared diagrams and other content.
  * Access meeting recordings and meeting notes on the [llm-d Public Google Drive](https://drive.google.com/drive/folders/1Y9ahJ9BhhDuwnPK6QHmKTi8uoz1FTqvA)
* **SIGs:** Join one of our [Special Interest Groups (SIGs)](SIGS.md) to contribute to specific areas of the project and collaborate with domain experts.
  * Access SIG meeting recordings and project documentation on the [Public SIG Documentation Google Drive](https://drive.google.com/drive/folders/13PVmYoIJitt5iZyaFTZiAgp_o66qAS8t)
* **Code**: Hosted in the [llm-d](https://github.com/llm-d) GitHub organization
* **Issues**: Project-scoped bugs or issues should be reported in [llm-d/llm-d](https://github.com/llm-d/llm-d)
* **Mailing List**: [llm-d-contributors@googlegroups.com](mailto:llm-d-contributors@googlegroups.com) for document sharing and collaboration
* **Social Media:** Follow us on social media for the latest news, announcements, and updates:
  * **X:** [https://x.com/\_llm_d\_](https://x.com/_llm_d_)
  * **LinkedIn:** [https://linkedin.com/company/llm-d](https://linkedin.com/company/llm-d)
  * **Reddit:** [https://www.reddit.com/r/llm_d/](https://www.reddit.com/r/llm_d/)
  * **YouTube** [@llm-d-project](https://youtube.com/@llm-d-project)

## Contributing Process

We follow a **lazy consensus** approach: changes proposed by people with responsibility for a problem, without disagreement from others, within a bounded time window of review by their peers, should be accepted.

### Types of Contributions

#### 1. Features with Public APIs or New Components

All features involving public APIs, behavior between core components, or new core repositories/subsystems must be accompanied by an **approved project proposal**.

**Process:**

1. Create a pull request adding a markdown file under `./docs/proposals` with a descriptive name (e.g., `docs/proposals/disaggregated_serving.md`)
2. Use the template at `./docs/proposals/PROPOSAL_TEMPLATE.md` with these sections:
   * **Summary**: A sentence or two suitable for any contributor or any user to understand the change proposed and the outcome
   * **Motivation**: Problem to be solved, including Goals/Non-Goals, and any necessary background
   * **Proposal**: Can include User Stories ("As a User I want to X"), should have enough detail that reviewers can understand exactly what you're proposing, but should not include things like API designs or implementation. What is the desired outcome and how do we measure success?
   * **Design Details**: Should contain enough information that the specifics of your change are understandable. This may include API specs (though not always required) or even code snippets. If there's any ambiguity about HOW your proposal will be implemented, this is the place to discuss them.
   * **Alternatives**: Provide alternative implementations/proposals and a short summary of why they were rejected
3. Get review from impacted component maintainers
4. Get approval from project maintainers

The proposal must be reviewed by the impacted component maintainers and approved by project maintainers. Proposal review should enforce overall principles and ensure consistency and coherence of the project. Approval of a proposal should reflect lazy consensus that the proposal is the right path, and the proposal should have high priority for review.

#### 2. Fixes, Issues, and Bugs

For changes that fix broken code or add small changes within a component:

* All bugs and commits must have a clear description of the bug, how to reproduce, and how the change is made
* Any other changes can be proposed in a pull-request to a component or an issue in llm-d/llm-d, a maintainer must approve the change (within the spirit of the component design and scope of change)
  * A good way to bring attention for moderate size changes is to create an RFC issue in GitHub, then engage in Slack
  * Within components, use project proposals when scope of change is large or impact to users is high

## Feature testing

The first key step in testing a feature, or bugfix is to identify what layer of the stack are you testing. Here are some test cases:

### Deployment related changes

* Swapping GIE helm chart version and `inference-scheduler` image upgrades - check `inference-scheduler` container logs
  * Check that your `InferencePool` exists (`kubectl get InferencePool.inference.networking.k8s.io`)
* Upgrading Infra helmchart or anything affecting Gateway infrastructure
  * Check the `gateway` object (`kubectl get gateway -o yaml`)
    * Check the `status` seciton, make sure it has an `address` and that there is a message saying `"Resource programmed, assigned to service(s) <gateway_service_address>"`
    * Check the `parametersRef` for the `gateway` `infrastructure` exists (`kubectl get gateway wide-ep-inference-gateway -o yaml | yq .spec.infrastructure.parametersRef`, and then check to ensure that resource itself exists)
  * If using `istio` also check that your `DestinationRule` exists
* Check the `httpRoute` object `status` section (`kubectl get httpRoute -o yaml | yq '.status.parents[]'`)
  * Ensure there is a message in the conditions stating: `"Route was valid"`
  * Ensure there is a parent ref on the `httpRoute`, pointing to the `httpRoute` being attached properly to the `gateway`
* Modelservice helm chart upgrades
  * Ensure `vLLM` pods up
  * `prefill` and `decode` `podmonitor`s are deployed if metrics are enabled

### Container Image Build Changes and Upgrades

* Kernel upgrades and changes (`pplx`, `deepep`, `deepgemm`) - Ignore `flash-infer`
  * To test these ensure you use the proper vLLM backend via the `VLLM_ALL2ALL_BACKEND` environment variable
    * For `pplx` you can set both `prefill` and `decode` `VLLM_ALL2ALL_BACKEND` to `pplx`
      * This can be ran in any example
    * For testing the deepseek kernels, you can set `prefill`s backend to `deepep_high_throughput` and `decode` backend to `deepep_low_latency`
      * This needs to be tested in either `pd-dissagregation` or better yet `wide-ep-lws`
* `UCX` + `NIXL` version bumps and changes
  * This can be tested in `pd-dissagregation` or `wide-ep-lws`
  * Currently we build `UCX` from source, and then build `NIXL` against our build of `NIXL`
* `LMCache` version bumps and changes (coming soon)
  * Currently nothing uses the `LMCache` codepath directly, this will come as a subset of the KVCache offloading epic
* `vLLM` version bumps and changes
  * By default we build `vLLM` with precompiled binaries from the upstream vLLM wheels index.
  * This can be tested in any example
* `EFA`
  * To test the libfabric plugin itself over NIXL you can do the following inside a container image built with EFA support (does not require GPUs or EFA):

```bash
export NIXL_LOG_LEVEL=debug
python3 - <<'EOF'
from nixl._api import nixl_agent, nixl_agent_config
agent_config = nixl_agent_config(backends=["LIBFABRIC"])
nixl_agent1 = nixl_agent("target", agent_config)
EOF
```

* To test actual inference over EFA in AWS with P5+ instances ensure that `UCX_TLS` includes an option with high priority for accelerating over EFA via an ENV var:

```yaml
  - name: UCX_TLS
    value: "efa,sockcm,sm,self,cuda_copy,cuda_ipc"
```

* Ensure that the containers request an instance of the EFA resource:

```yaml
  requests:
    vpc.amazonaws.com/efa: 1
```

### Container Image Checklist

* [ ] `inference-scheduler` guide
* [ ] `precise-kv-cache-aware` example
* [ ] `pd-dissagregation` example (also covers deepseek kernels)
* [ ] `wide-ep-lws` example (also covers deepseek kernels)
* [ ] a `guidellm` benchmark to do a load test for performance regressions (any example)
* [ ] run a guide with the `pplx` backend
* [ ] run `pd-dissagregation` or `wide-ep-lws` with deepseek kernels (for `prefill`s set `VLLM_ALL2ALL_BACKEND` to `deepep_high_throughput` and set `decode` `VLLM_ALL2ALL_BACKEND` to `deepep_low_latency`)

### Code Review Requirements

* **All code changes** must be submitted as pull requests (no direct pushes)
* **All changes** must be reviewed and approved by a maintainer other than the author
* **All repositories** must gate merges on compilation and passing tests
* **All experimental features** must be off by default and require explicit opt-in

### Commit and Pull Request Style

* **Pull requests** should describe the problem succinctly
* **Rebase and squash** before merging
* **Use minimal commits** and break large changes into distinct commits
* **Commit messages** should have:
  * Short, descriptive titles
  * Description of why the change was needed
  * Enough detail for someone reviewing git history to understand the scope
* **DCO Sign-off**: All commits must include a valid DCO sign-off line (`Signed-off-by: Name <email@domain.com>`)
  * Add automatically with `git commit -s`
  * See [PR_SIGNOFF.md](PR_SIGNOFF.md) for configuration details
  * Required for all contributions per [Developer Certificate of Origin](https://developercertificate.org/)

## Code Organization and Ownership

### Components and Maintainers

* **Components** are the primary unit of code organization (repo scope or directory/package/module within a repo)
* **Maintainers** own components and approve changes
* **Contributors** can become maintainers through sufficient evidence of contribution
* Code ownership is reflected in [OWNERS files](https://go.k8s.io/owners) consistent with Kubernetes project conventions

### Core vs Incubating Components

* **Core components**: Supported by the project with strong lifecycle controls and forward compatibility
* **Incubating components**: Rapidly iterating, not yet ready for production use, allowing greater freedom for testing ideas

## Experimental Features and Incubation

We encourage fast iteration and exploration with these constraints:

1. **Clear identification** as experimental in code and documentation
2. **Default to off** and require explicit enablement
3. **Best effort support** only
4. **Removal if unmaintained** with no one to move it forward
5. **No stigma** to experimental or incubating status

### Incubating Components Process

1. **Create repositories** in `llm-d-incubation` GitHub org with maintainers and clear goals
2. **Define timeframe** for experimentation
3. **Iterate and test** with initial users
4. **For well-lit path components**:
   * Create project proposal covering integration
   * Define graduation success criteria
   * Add to well-lit path after approval
5. **For standalone components**:
   * Create project proposal with graduation criteria
   * Component can be used with experimental label
6. **Graduation**: Move to core `llm-d` org and follow core process
7. **If not graduating**: Archive for 3+ months before removal

### Experimental Features in Core Components

1. Open pull request to existing core component
2. Maintainer classifies as experimental, enforces "off-by-default" gating
3. Provide tests for both on/off states
4. When graduating, default to on and remove conditional logic after one release

**Naming convention**: Experimental flags must include `experimental` in name (e.g., `--experimental-disaggregation-v2=true`)

## API Changes and Deprecation

* **No breaking changes**: Once an API/protocol is in GA release (non-experimental), it cannot be removed or behavior changed
* **Includes**: All protocols, API endpoints, internal APIs, command line flags/arguments
* **Exception**: Bug fixes that don't impact significant number of consumers (As the project matures, we will be stricter about such changes - Hyrum's Law is real)
* **Versioning**: All protocols and APIs should be versionable with clear forward and backward compatibility requirements. A new version may change behavior and fields.
* **Documentation**: All APIs must have documented specs describing expected behavior

## Testing Requirements

We use three tiers of testing:

1. **Unit tests**: Fast verification of code parts, testing different arguments
   * Best for fast verification of parts of code, testing different arguments
   * Doesn't cover interactions between code
2. **Integration tests**: Testing protocols between components and built artifacts
   * Best for testing protocols and agreements between components
   * May not model interactions between components as they are deployed
3. **End-to-end (e2e) tests**: Whole system testing including benchmarking
   * Best for preventing end to end regression and verifying overall correctness
   * Execution can be slow

Strong e2e coverage is required for deployed systems to prevent performance regression. Appropriate test coverage is an important part of code review.

## Security

Maintain appropriate security mindset for production serving. The project will establish a project email address for responsible disclosure of security issues that will be reviewed by the project maintainers. Prior to the first GA release we will formalize a security component and process.

## Project Structure

### Core Organization (`llm-d`)

* Production-ready code on well-lit path
* Follows API Changes and Deprecation process
* All major changes require project proposals

### Incubation Organization (`llm-d-incubation`)

* Experimental components not yet fully supported
* Bias towards accepting experimentation with clear goals
* Each repo must have README describing purpose and goal
* Graduated components move to `llm-d` org
