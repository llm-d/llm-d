# llm-d, the Project

`llm-d` is a Kubernetes-native distributed inference serving stack - a well-lit path for anyone to serve at scale, with the fastest time-to-value and competitive performance per dollar for most models across most hardware accelerators.

## Principles

1. Keep it simple - Users rapidly achieve running inference along a few well-lit paths  
2. Composition is preferred to configurability - components connect at API boundaries  
3. We move fast - experimental code and features are encouraged as long as they are opt-in and isolated when off  
4. We respect our upstreams - vLLM and inference-gateway are where code changes start, no forks  
5. We are shipping to production - core code should have a high review, test, and reliability bar  
6. vLLM-first but not vLLM-only - build the modular architecture for most people and collaborate with other projects  
7. [Hyrum’s Law](https://www.hyrumslaw.com/) is real - we do not regress published APIs or ship breaking changes, only new APIs

## People

### Code Ownership

Code is owned by the project, and all code must have at least one (preferably two or more) humans responsible for its care.  The primary unit of code organization is a **component** at a repo scope or within a repo (a directory / package / module / image / subsystem).

Components are owned by **maintainers**, who approve changes in their components.  Both contributors and maintainers are expected to review code changes. **Contributors** can become maintainers through sufficient evidence of contribution.  A maintainer provides approval of changes to merge to a component.  Any contributor can offer review, but maintainers are expected to ensure review is thorough and complete before providing approval.

Components are either **core** or **incubating**.  A core component is supported by the project and has strong lifecycle controls and forward compatibility for users.  An incubating component is rapidly iterating and is not yet ready for use - this allows greater freedom to test new ideas.

Code ownership is reflected as [OWNERS files](https://go.k8s.io/owners) in repositories and directories consistent with the Kubernetes project (maintainers are approvers, maintainers and frequent contributors are named as reviewers).

### Project Ownership

The project (including all components owned by the project) direction and scope is determined by the initial mission and principles above, and can be modified via the project [process](#process).

To rapidly align the technical vision across the major stakeholders in the project, a small group of technical leads will devote a majority of their time as **project maintainers** to:

* Act together in the best interests of the project, maintainers, and contributors  
* Provide consistent architectural direction  
* Approve and review code changes with project-level context  
* Enable component maintainers with quick decision loops in the initial few months  
* Grow strong and stable component maintainers and delegate responsibility  
* Accelerate consensus and resolve disagreements  
* Approve changes to project process

Project maintainers are listed as in the [OWNERS](./OWNERS) file of this repository as approvers (also referred to as “top level approvers”).  The initial project maintainers are Carlos Costa, Clayton Coleman, and Robert Shaw, representing inference optimization research, the inference gateway project, and vLLM respectively.

As the project matures we expect to separate and devolve these responsibilities and to rely on component maintainers for architectural alignment.

We will continually review this structure as we make grow as we desire broad participation in project direction.

## Community

We discuss in public Slack [llm-d.slack.com](https://llm-d.slack.com).  We prefer to discuss active issues via Slack for immediate response and collaboration.

Our code is hosted in the [llm-d](https://github.com/llm-d) GitHub organization, and project scoped bugs or issues should be reported in [llm-d/llm-d](https://github.com/llm-d/llm-d).

The google group [llm-d-contributors@googlegroups.com](mailto:llm-d-contributors@googlegroups.com) is used for sharing documents for comment and edit access.

## Process

Changes to the project process will be managed by pull requests to this file and approved by **project maintainers**.

### The Default Process is Lazy Consensus

The default process is [lazy consensus](https://community.apache.org/committers/decisionMaking.html#lazy-consensus).  Changes proposed by people with responsibility for a problem, without disagreement from others, within a bounded time window of review by their peers, should be accepted. Objections should be accompanied by a legitimate reason and be open to discussing possible alternative approaches.

All important changes should be documented in a public location (code / docs in repo for large, GitHub issues for small) and communicated to end users as part of releases.

### Design and Code Review

We use a lightweight change approval process process:

#### Features that include public APIs, behavior between components, or new core components / subsystems

All features involving public APIs, behavior between core components, or new core repos / subsystems must be accompanied by an approved project proposal to be merged. If a change has high impact to users, maintainers, or components, describe the change as a new proposal or update an existing one with additional details.

The project proposal is a pull-request that adds a markdown file under [./docs/proposals](./docs/proposals) with a short and descriptive name for the change - e.g. `docs/proposals/disaggregated_serving.md`. The file should contain the following sections (templated in [./docs/proposals/PROPOSAL_TEMPLATE.md](./docs/proposals/PROPOSAL_TEMPLATE.md)):

* Summary: A sentence or two suitable for any contributor or any user to understand the change proposed and the outcome  
* Motivation: for the change which includes the problem to be solved, including Goals / Non-Goals, and any necessary background  
* Proposal: Can include User Stories (“As a User I want to X”), should have enough detail that reviewers can understand exactly what you're proposing, but should not include things like API designs or implementation. What is the desired outcome and how do we measure success?  
* Design Details: Should contain enough information that the specifics of your change are understandable. This may include API specs (though not always required) or even code snippets. If there's any ambiguity about HOW your proposal will be implemented, this is the place to discuss them.   
* Alternatives: Provide alternative implementations / proposals and a short summary of why they were rejected.  

The proposal must be reviewed by the impacted component maintainers and approved by a project maintainers. Proposal review should enforce overall principles and ensure consistency and coherence of the project. See the [process on experimentation](#experimentation-and-incubation) for how proposals differ when they are for experimental features. Approval of a proposal should reflect lazy consensus that the proposal is the right path, and the proposal should have high priority for review.

#### Fixes, Issues, and Bugs

Changes intended to fix broken code or to add small clear changes that do not cross out of a component

* All bugs and commits must have a clear description of the bug, how to reproduce, and how the change is made.  
* Any other changes can be proposed in a pull-request to a component or an issue in llm-d/llm-d, a maintainer must approve the change (within the spirit of the component design and scope of change)  
  * A good way to bring attention for moderate size changes is to create a RFC issue in GitHub, then engage in Slack  
  * Within components, use project proposals when scope of change is large or impact to users is high

#### Code Changes and Review

We require lightweight code review:

* All code changes must be in the form of pull-requests - no direct pushes except by a GitHub administrator
* All code changes must be reviewed and approved by a maintainer other than the author  
* All repos must gate merges of pull-requests on compilation and running tests at minimum  
* All experimental features must be off by default and require explicit opt-in

#### Commit and Pull-request Style

* Pull-requests should describe the problem succinctly
* Rebase and squash before merging
* Use the minimal number of commits, and break large changes up into distinct commits
* Commits should have short titles and a description of why the change was needed and enough detail that someone looking at the git history can determine the scope of the change.

### API Changes and Deprecation

Once an API or protocol is part of a GA release in a non-experimental (on by default) fashion, it may not be removed or the behavior changed.  This includes all protocols, API endpoints, internal API interfaces in code, command line flags and arguments.  

The only exception to changes is when a bug prevents the functioning of the protocol/API/feature as designed and the fix would not impact a significant number of consumers. As the project matures, we will be stricter about such changes (Hyrum’s Law is real).

All protocols and APIs should be versionable, and have clear forward and backward compatibility requirements.  A new version may change behavior and fields.

All APIs must have a documented spec that describes expected behavior.

### Experimentation and Incubation

We seek to enable fast iteration and exploration, and therefore endorse creating new components and features that may not be ready for production or endorsement.  We are biased towards accepting experimental code or components with the following constraints:

1. The feature or component must be clearly identified as experimental in code and documentation  
2. The feature or component must default to off and be explicitly enabled  
3. An experimental feature or component has best effort support  
4. Experimental features and components will be removed if they go unmaintained and there is no one to move it forward
5. There is no stigma to being experimental code or maintaining an incubating component
6. Our well-lit path may reference incubating components as long as they are opt-in

#### Incubating Components

We use a separate GitHub org `llm-d-incubation` (under the same process as `llm-d`) to identify incubating components.  When a component is sufficiently complete and tested to be on / accessible by default (and thus part of our API change process), we **graduate** it into the `llm-d` org.

Normal process:

1. Create one or more repositories in the incubation org with maintainers and a clear goal  
   1. Define a clear timeframe for experimentation  
2. Iterate on the code in the component and test with initial users  
3. If the component will be part of a well-lit path:  
   1. Create a project proposal covering the component  
   2. Describe how the component integrates in the path  
   3. Describe what will be required for graduation (success criteria)  
   4. Once the proposal is reviewed and approved, the experimental component may be added to a well-lit path  
4. If the component will be standalone:
   1. Create a project proposal covering the component  
   2. Describe what will be required for graduation (success criteria)  
   3. The component can be widely used with the experimental label by others  
5. Once the component graduates, it moves to the core github org and follows the core process  
6. If the component does not graduate or stops being maintained, it will be archived for at least 3 months before being moved out of the incubation org.

#### Experimental Features and APIs

Code that is added to core components may not be ready for wide use.  They must be gated - users must explicitly opt in to their use - and are off by default at runtime.

Normal process:

1. Open a pull request to an existing core component  
2. Maintainer classifies the PR as experimental, enforces “off-by-default” via flag gating or code use  
3. Contributor provides additional tests for when the code is on, but adds unit tests verifying the code is inert when off  
4. When the feature graduates, the gating defaults to on and the conditional logic can be removed after one release where it is on by default.

Experiments must be behind flags and have the word `experimental` in their name, i.e. `--experimental-disaggregation-v2=true` for a CLI flag.

### Testing

llm-d utilizes the three tiers of testing:

* Unit tests for local code and repositories  
  * Best for fast verification of parts of code, testing different arguments  
  * Doesn’t cover interactions between code  
* Integration tests for components in composition (including built artifacts like images)  
  * Best for testing protocols and agreements between components  
  * May not model interactions between components as they are deployed  
* End to end (e2e) testing of the whole system (including benchmarking)  
  * Best for preventing end to end regression and verifying overall correctness  
  * Slow

We seek strong e2e coverage for the deployed system and to prevent regression of performance changes. Appropriate test coverage of core components is an important part of review.

### Security

An appropriate security mindset is important for production serving and the project will establish a project email address for responsible disclosure of security issues that will be reviewed by the project maintainers. Prior to the first GA release we will formalize a security component and process.

## Artifacts

### Docs and API Docs

Published to the website [https://llm-d.github.io/](https://llm-d.github.io/).

### Container Images

Images are published to [ghcr.io/llm-d](http://ghcr.io/llm-d).

### Project Automation

We will leverage elements of Kubernetes automation (OWNERS files, GitHub sync, /lgtm) to reduce toil in the repos where we can.

### CI/CD

CI/CD biases towards GHA.  For more involved e2e testing and benchmarking we will explore alignment with the Kube ecosystem, since we will need that for testing e2e scenarios llm-d adds to the inference gateway components.

### Test infrastructure is provided by sponsors

Red Hat and Google will provide initial hardware for testing.

## Source Code

Located on GitHub as two top level organizations:

* `llm-d`
  * Description: Our core organization, all code that is on a well-lit path to production  
  * Rules:  
    * Follows the API Changes and Deprecation process  
    * All major changes require project proposals  
    * If/when we need midstream repos, we will consider them in this repo  
* `llm-d-incubation`
  * Description: All components that are experimental and not yet fully supported  
  * Purpose  
    * Reduce the scope of code in the llm-d org to our minimum viable project  
    * Provide a clear place to experiment and stage projects with lower overhead for end users  
  * Rules:  
    * Bias towards accepting experimentation, with a clear goal for each component  
    * Each repo must have a README with a short paragraph describing the purpose and goal  
    * Components that graduate are moved to llm-d org  

Note: some projects at the current time may be incubating and located in the `llm-d` org, and will be moved in the near future.

All llm-d projects should:

* Enable branch protection
* Limit write permission to git administrators
* Rely on automation for verifying permission to merge