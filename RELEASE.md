# llm-d, Release process

This document describes the release process for llm-d. The release dates should be in the public calendar.

## Phases

### 1. Feature freeze

This section describes the general process for tracking work for a release, which involves attending lead syncs and SIG meetings. There is also a milestone release tracker that needs to be kept up to date. The release tracker can be found in the [milestones](https://github.com/llm-d/llm-d/milestones) section of the repository.

The release tracker is created 1-2 weeks into the sprint. 4-weeks later, the release tracker is frozen and it should contain all the features that will be in the release. Each feature should have a specific example: the example should be production-ready, meaning that the feature should show an example that is realistic and that can be used in production. Toy-examples should be avoided.

> [!NOTE]
> The release tracker is not necessarily accurate. However, in the future, we want to keep this tracker up to date and as accurate as possible.

### 2. Pre-release

Once all the features have been integrated into the repo, the next phase is the pre-release. This phase deals with all the preparation. Usually, a PR is created where we do all the version bumps.

A new version release of llm-d has a version of all of the different components, a new section for each component describing the functionality and the differences between the new and previous version, a `What's Changed` section summarizing the changes in bullets, and finally, a list of the new contributors. See an example of this document in the [release notes (ex. v0.4.0)](https://github.com/llm-d/llm-d/releases/tag/v0.4.0).

In the `Component Summary` section, we need to generate a matrix that shows each component name, the component version, the previous version of the component, and the type.

Also in this phase, we need to include all the CI/CD changes that are required for the release based on the components that we are releasing.

### 3. Release work

The final release work involves creating a tag in the llm-d repo, which triggers a release [workflow](https://github.com/llm-d/llm-d/blob/main/.github/workflows/ci-release.yaml) to build images. We currently use GHCR for image storage. The prep work involves bumping to an image tag that does not exist yet, and then tagging the repo creates the necessary images.

There are two different packages: main and `dev`. The `dev` packages are created by the PRs, while the main packages are created by the release process. For example, see [llm-d-cpu](https://github.com/llm-d/llm-d/packages?q=llm-d-cpu) packages.

The process involves creating a Release Candidate (RC) tag for a dry run of the release workflow, which is a method to test the process and identify necessary updates (such as removing the deprecated images build from the CI). We typically delete RC tags after testing, while the final release is drafted from a new tag using the collected release notes.

It is important to fetch the tags from the upstream repo to avoid any conflicts using:

```bash
git fetch upstream --tags
```

> [!NOTE]
> The release branches need to be created in the upstream repo because the workflow that we have, if we need to make container image changes, only works on the upstream branches.

## Example of a dry-run of the release

We create a new tag for the dry run:

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
```
