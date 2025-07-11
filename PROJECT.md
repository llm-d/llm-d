# llm-d, the Project

`llm-d` is a Kubernetes-native distributed inference serving stack - a well-lit path for anyone to serve at scale, with the fastest time-to-value and competitive performance per dollar for most models across most hardware accelerators.

## Principles

1. Keep it simple - Users rapidly achieve running inference along a few well-lit paths  
2. Composition is preferred to configurability - components connect at API boundaries  
3. We move fast - experimental code and features are encouraged as long as they are opt-in and isolated when off  
4. We respect our upstreams - vLLM and inference-gateway are where code changes start, no forks  
5. We are shipping to production - core code should have a high review, test, and reliability bar  
6. vLLM-first but not vLLM-only - build the modular architecture for most people and collaborate with other projects  
7. [Hyrum's Law](https://www.hyrumslaw.com/) is real - we do not regress published APIs or ship breaking changes, only new APIs

## Code of Conduct
This project adheres to the llm-d [Code of Conduct and Covenant](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Contributing

For detailed information about contributing to llm-d, including the contribution process, code organization, experimental features, and more, please see our [CONTRIBUTING.md](CONTRIBUTING.md) document.

## People

### Code Ownership

Code is owned by the project, and all code must have at least one (preferably two or more) humans responsible for its care. The primary unit of code organization is a **component** at a repo scope or within a repo (a directory / package / module / image / subsystem).

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

Project maintainers are listed as in the [OWNERS](./OWNERS) file of this repository as approvers (also referred to as "top level approvers").  The initial project maintainers are Carlos Costa, Clayton Coleman, and Robert Shaw, representing inference optimization research, the inference gateway project, and vLLM respectively.

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

## Artifacts

### Docs and API Docs

Published to the website [https://llm-d.github.io/](https://llm-d.github.io/).

### Container Images

Images are published to [ghcr.io/llm-d](https://github.com/llm-d/llm-d/pkgs/container/llm-d).

### Project Automation

We will leverage elements of Kubernetes automation (OWNERS files, GitHub sync, /lgtm) to reduce toil in the repos where we can.

### CI/CD

CI/CD biases towards GHA.  For more involved e2e testing and benchmarking we will explore alignment with the Kube ecosystem, since we will need that for testing e2e scenarios llm-d adds to the inference gateway components.

### Test Infrastructure Sponsorship

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
  * Purpose:  
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
