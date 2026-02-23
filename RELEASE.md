# llm-d, Release process

This document describes the release process for llm-d. The release dates should be in the public calendar.

## Phases

### 1. Feature freeze

  The feature freeze phase ensures that all planned work for a release is identified, tracked, and finalized before the pre-release phase begins.

#### Release tracker

  Each release is tracked using a GitHub [milestone](https://github.com/llm-d/llm-d/milestones). The milestone serves as the single source of truth for all features, bug fixes, and tasks targeted for the release.

  **Timeline:**

  1. **Sprint start (weeks 1-2):** The release leaders create the milestone for the upcoming release. SIG leads and project maintainers begin nominating issues and features for inclusion.
  2. **Feature collection (weeks 2-4):** SIG leads propose features from their respective areas during the weekly project standup (Wednesdays 12:30 PM ET) and SIG meetings. Each proposed feature must have a corresponding GitHub issue linked to the milestone.
  3. **Feature freeze (end of week 4):** The milestone is frozen. No new features are added after this point unless an exception is approved by the release leaders and project maintainers. Only bug fixes and documentation updates are accepted after the freeze.

#### Roles and responsibilities

  | Role | Responsibility |
  | ---- | -------------- |
  | **Release leaders** | Creates the milestone, coordinates the freeze timeline, and ensures the tracker is up to date. |
  | **SIG leads** | Propose and advocate for features from their SIG, ensure features are ready by the freeze date, and provide status updates during lead syncs and SIGmeetings. |
  | **Project maintainers** | Review and approve feature nominations, resolve disputes, and approve any post-freeze exceptions. |

#### Feature requirements

  Each feature included in the milestone must meet the following criteria:

- **GitHub issue:** A clearly described issue linked to the milestone, with acceptance criteria.
- **Production-ready example:** The feature must include a realistic, production-ready example (e.g., a guide under `guides/`). Toy examples or placeholder demonstrations are not acceptable.
- **Proposal (if applicable):** Features involving public APIs, new components, or cross-SIG changes must have an approved [project
  proposal](docs/proposals/PROPOSAL_TEMPLATE.md) as described in the [contributing guidelines](CONTRIBUTING.md).
- **Test coverage:** Appropriate unit, integration, or e2e test coverage as defined in the [testing requirements](CONTRIBUTING.md#testing-requirements).

#### Coordination

  Feature tracking is coordinated through:

- **Weekly project standup:** Overall release progress is reviewed every Wednesday at 12:30 PM ET (see the [public calendar](https://red.ht/llm-d-public-calendar)).
- **SIG meetings:** Each [SIG](SIGS.md) reviews the status of their features during their regular weekly meetings.
- **Slack:** Day-to-day coordination happens in the [#llm-d-dev](https://llm-d.slack.com/archives/C08SH9K8JGK) Slack channel and relevant SIG channels.

  > [!NOTE]
  > The release tracker may not always be fully accurate. We are actively working to improve the tracking process so that the milestone reflects the true state of the release at all times.

### 2. Pre-release

Once all the features have been integrated into the repo, the next phase is the pre-release. This phase deals with all the preparation. Usually, a PR is created where we do all the version bumps.

A new version release of llm-d has a version of all of the different components, a new section for each component describing the functionality and the differences between the new and previous version, a `What's Changed` section summarizing the changes in bullets, and finally, a list of the new contributors. See an example of this document in the [release notes (ex. v0.4.0)](https://github.com/llm-d/llm-d/releases/tag/v0.4.0).

In the `Component Summary` section, we need to generate a matrix that shows each component name, the component version, the previous version of the component, and the type.

Also in this phase, we need to include all the CI/CD changes that are required for the release based on the components that we are releasing.

### 3. Release work

The final release work involves creating a tag in the llm-d repo, which triggers a release [workflow](https://github.com/llm-d/llm-d/blob/main/.github/workflows/ci-release.yaml) to build images. We currently use GHCR for image storage. The prep work involves bumping to an image tag that does not exist yet, and then tagging the repo creates the necessary images.

  There are two different types of container image packages: **release** and **dev**.

- **Release** images are created by the release workflow (`ci-release.yaml`) when a version tag is pushed. They follow the naming pattern
  `ghcr.io/llm-d/llm-d-{platform}:{version}` and are tagged with the release version. For example:
  - `ghcr.io/llm-d/llm-d-cuda:v0.5.0`
  - `ghcr.io/llm-d/llm-d-cpu:v0.5.0`
  - `ghcr.io/llm-d/llm-d-aws:v0.5.0`

- **Dev** images are created by the dev build workflow (`build-image.yml`), triggered on PRs that modify Dockerfiles/build scripts and by the nightly build schedule. They follow the naming pattern `ghcr.io/llm-d/llm-d-{platform}-dev:{tag}` and are tagged with the git short SHA or PR number. For example:
  - `ghcr.io/llm-d/llm-d-cuda-dev:sha-abc1234`
  - `ghcr.io/llm-d/llm-d-cpu-dev:pr-123`
  - `ghcr.io/llm-d/llm-d-cuda-dev:latest` (from the default branch)

  The full list of platforms includes: `cuda`, `aws`, `cpu`, `rocm`, `xpu`, and `hpu`. See the [llm-d packages](https://github.com/orgs/llm-d/packages?repo_name=llm-d) for
   the complete list.

The process involves creating a Release Candidate (RC) tag for a dry run of the release workflow, which is a method to test the process and identify necessary updates (such as removing the deprecated images build from the CI). We typically delete RC tags after testing, while the final release is drafted from a new tag using the collected release notes.

It is important to fetch the tags from the upstream repo to avoid any conflicts using:

```bash
git fetch upstream --tags
```

> [!NOTE]
> The release branches need to be created in the upstream repo because the workflow that we have, if we need to make container image changes, only works on the upstream branches.

<!-- ## Example of a dry-run of the release -->

<!-- We create a new tag for the dry run:

```bash
git fetch upstream --tags
git tag v0.5.0-rc.1
git push upstream --tags
```

This new tag triggers the release workflow. In the actions tab, you can see the `Release LLM-D Images` workflow queued.

If, at some point, we want to delete the tags, we can do it using:

```bash
git tag -d v0.5.0-rc.1
git push upstream --delete v0.5.0-rc.1
git fetch upstream --tags -f # this should show no updates
``` -->
## Example of a dry-run of the release

  We create a new tag for the dry run:

  ```bash
  git fetch upstream --tags
  git tag v0.5.0-rc.1
  git push upstream --tags
  ```

  This new tag triggers the release workflow. In the actions tab, you can see the Release LLM-D Images workflow queued.

  When you push an RC tag like v0.5.0-rc.1, the release workflow builds and pushes container images to GHCR. This has two side effects that require cleanup:

  1. latest tag gets overwritten: The release workflow unconditionally tags images as latest, so an RC build will overwrite the current production latest tag.
  2. GHCR auto-generates semver package versions: GitHub automatically creates additional package version entries derived from the tag. For example, pushing v0.5.0-rc.1
  generates:

  | Auto-generated tag | Issue                                           |
  |--------------------|-------------------------------------------------|
  | v0                 | Production tag - should not be updated by an RC |
  | v0.5               | Production tag - should not be updated by an RC |
  | v0.5.0             | Production tag - should not be updated by an RC |
  | v0.5.0-rc          | Needs cleanup after testing                     |
  | v0.5.0-rc.1        | The actual RC tag (needs cleanup after testing) |

  After verifying the dry-run, clean up the auto-generated package versions from the llm-d packages page. For each affected package (e.g., llm-d-cuda, llm-d-cpu, llm-d-aws, etc.), delete the RC-related versions (v0.5.0-rc.1, v0.5.0-rc) and verify that the production tags (v0, v0.5, v0.5.0, latest) still point to the correct release.

  Delete the RC git tags from both local and upstream:

```bash
  git tag -d v0.5.0-rc.1
  git push upstream --delete v0.5.0-rc.1
  git fetch upstream --tags -f # this should show no updates
  ```
