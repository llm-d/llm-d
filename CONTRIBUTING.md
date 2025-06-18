# Contributing to llm-d

Thank you for your interest in contributing to llm-d! This document outlines the guidelines and processes for contributing to the project.

## Code of Conduct

This project adheres to the llm-d [Code of Conduct and Covenant](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

### Community

- **Slack**: Join our public discussion at [llm-d.slack.com](https://llm-d.slack.com) for immediate response and collaboration
- **Join Slack**: You can join the [llm-d Slack at Inviter](https://inviter.co/llm-d-slack)
- **Code**: Hosted in the [llm-d](https://github.com/llm-d) GitHub organization
- **Issues**: Project-scoped bugs or issues should be reported in [llm-d/llm-d](https://github.com/llm-d/llm-d)
- **Mailing List**: [llm-d-contributors@googlegroups.com](mailto:llm-d-contributors@googlegroups.com) for document sharing and collaboration

## Contributing Process

We follow a **lazy consensus** approach: changes proposed by people with responsibility for a problem, without disagreement from others, within a bounded time window of review by their peers, should be accepted.

### Types of Contributions

#### 1. Features with Public APIs or New Components

All features involving public APIs, behavior between core components, or new core repositories/subsystems must be accompanied by an **approved project proposal**.

**Process:**
1. Create a pull request adding a markdown file under `./docs/proposals` with a descriptive name (e.g., `docs/proposals/disaggregated_serving.md`)
2. Use the template at `./docs/proposals/PROPOSAL_TEMPLATE.md` with these sections:
   - **Summary**: A sentence or two explaining the change and outcome
   - **Motivation**: Problem to be solved, goals/non-goals, background
   - **Proposal**: User stories, desired outcome, success metrics
   - **Design Details**: Specific implementation details, API specs if needed
   - **Alternatives**: Other approaches considered and why they were rejected
3. Get review from impacted component maintainers
4. Get approval from project maintainers

#### 2. Fixes, Issues, and Bugs

For changes that fix broken code or add small changes within a component:

- Clearly describe the bug, how to reproduce, and how the change fixes it
- For moderate size changes, create an RFC issue in GitHub and engage in Slack
- Get approval from a component maintainer

### Code Review Requirements

- **All code changes** must be submitted as pull requests (no direct pushes)
- **All changes** must be reviewed and approved by a maintainer other than the author
- **All repositories** must gate merges on compilation and passing tests
- **All experimental features** must be off by default and require explicit opt-in

### Commit and Pull Request Style

- **Pull requests** should describe the problem succinctly
- **Rebase and squash** before merging
- **Use minimal commits** and break large changes into distinct commits
- **Commit messages** should have:
  - Short, descriptive titles
  - Description of why the change was needed
  - Enough detail for someone reviewing git history to understand the scope
- **DCO Sign-off**: All commits must include a valid DCO sign-off line (`Signed-off-by: Name <email@domain.com>`)
  - Add automatically with `git commit -s`
  - See [PR_SIGNOFF.md](https://github.com/llm-d/llm-d/blob/dev/PR_SIGNOFF.md) for configuration details
  - Required for all contributions per [Developer Certificate of Origin](https://developercertificate.org/)

## Code Organization and Ownership

### Components and Maintainers

- **Components** are the primary unit of code organization (repo scope or directory/package/module within a repo)
- **Maintainers** own components and approve changes
- **Contributors** can become maintainers through sufficient evidence of contribution
- Code ownership is reflected in [OWNERS files](https://go.k8s.io/owners) consistent with Kubernetes project conventions

### Core vs Incubating Components

- **Core components**: Supported by the project with strong lifecycle controls and forward compatibility
- **Incubating components**: Rapidly iterating, not yet ready for production use, allowing greater freedom for testing ideas

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
   - Create project proposal covering integration
   - Define graduation success criteria
   - Add to well-lit path after approval
5. **For standalone components**:
   - Create project proposal with graduation criteria
   - Component can be used with experimental label
6. **Graduation**: Move to core `llm-d` org and follow core process
7. **If not graduating**: Archive for 3+ months before removal

### Experimental Features in Core Components

1. Open pull request to existing core component
2. Maintainer classifies as experimental, enforces "off-by-default" gating
3. Provide tests for both on/off states
4. When graduating, default to on and remove conditional logic after one release

**Naming convention**: Experimental flags must include `experimental` in name (e.g., `--experimental-disaggregation-v2=true`)

## API Changes and Deprecation

- **No breaking changes**: Once an API/protocol is in GA release (non-experimental), it cannot be removed or behavior changed
- **Includes**: All protocols, API endpoints, internal APIs, command line flags/arguments
- **Exception**: Bug fixes that don't impact significant number of consumers
- **Versioning**: All protocols and APIs should be versionable with clear compatibility requirements
- **Documentation**: All APIs must have documented specs describing expected behavior

## Testing Requirements

We use three tiers of testing:

1. **Unit tests**: Fast verification of code parts, testing different arguments
2. **Integration tests**: Testing protocols between components and built artifacts
3. **End-to-end (e2e) tests**: Whole system testing including benchmarking

Strong e2e coverage is required for deployed systems to prevent performance regression. Appropriate test coverage is an important part of code review.

## Security

Maintain appropriate security mindset for production serving. A project email address for responsible disclosure will be established and reviewed by project maintainers.

## Project Structure

### Core Organization (`llm-d`)
- Production-ready code on well-lit path
- Follows API Changes and Deprecation process
- All major changes require project proposals

### Incubation Organization (`llm-d-incubation`)
- Experimental components not yet fully supported
- Bias towards accepting experimentation with clear goals
- Each repo must have README describing purpose and goal
- Graduated components move to `llm-d` org

## Questions?

- For immediate help: Join [llm-d.slack.com](https://llm-d.slack.com)
- For issues: Create an issue in [llm-d/llm-d](https://github.com/llm-d/llm-d)
- For collaboration: Contact [llm-d-contributors@googlegroups.com](mailto:llm-d-contributors@googlegroups.com)