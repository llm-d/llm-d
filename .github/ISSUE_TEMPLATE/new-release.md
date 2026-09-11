---
name: New Release
about: Propose a new release
title: Release v0.x.0
labels: ''
assignees: ''

---

- [Introduction](#introduction)
- [Prerequisites](#prerequisites)
- [Release Process](#release-process)
- [Announce the Release](#announce-the-release)
- [Final Steps](#final-steps)

## Introduction

This document defines the process for releasing llm-d.

## Prerequisites

1. Permissions to push to the llm-d repository.

1. Set the required environment variables based on the expected release number:

   ```shell
   export MAJOR=0
   export MINOR=1
   export PATCH=0
   export REMOTE=origin
   ```

1. If creating a release candidate, set the release candidate number.

   ```shell
   export RC=1
   ```

1. If needed, clone the llm-d [repo].

   ```shell
   git clone -o ${REMOTE} git@github.com:llm-d/llm-d.git
   ```

## Release Process

### Create or Checkout branch

1. If you already have the repo cloned, ensure it’s up-to-date and your local branch is clean.

1. Release Branch Handling:
   - For a Release Candidate:
     Create a new release branch from the `main` branch. The branch should be named `release-${MAJOR}.${MINOR}`, for example, `release-0.1`:

     ```shell
     git checkout -b release-${MAJOR}.${MINOR}
     ```

   - For a Major, Minor or Patch Release:
     A release branch should already exist. In this case, check out the existing branch:

     ```shell
     git checkout -b release-${MAJOR}.${MINOR} ${REMOTE}/release-${MAJOR}.${MINOR}
     ```

1. Set all `branch` environment variables on all the [guides](../../guides) to the release branch name (i.e.,`release-${MAJOR}.${MINOR}`)

     ```shell
     git commit -a -s -S -m "updated branch environment variable on all guides to release-${MAJOR}.${MINOR}"
     ```

1. Push your release branch to the llm-d remote.

    ```shell
    git push ${REMOTE} release-${MAJOR}.${MINOR}
    ```

### Create documentation release branch

1. Create a release branch in the llm-d.github.io repository:

   ```shell
   gh workflow run create-release-branch.yml \
     --repo llm-d/llm-d.github.io \
     --field version=${MAJOR}.${MINOR}.${PATCH} \
     --field source_branch=release-${MAJOR}.${MINOR}
   ```

1. This creates a `release-${MAJOR}.${MINOR}.${PATCH}` branch that syncs docs nightly from the llm-d release branch.

### Tag commit and trigger image build

1. Tag the head of your release branch with the sem-ver release version.

   For a release candidate:

    ```shell
    git tag -s -a v${MAJOR}.${MINOR}.${PATCH}-rc.${RC} -m 'llm-d v${MAJOR}.${MINOR}.${PATCH}-rc.${RC} Release Candidate'
    ```

   For a major, minor or patch release:

    ```shell
    git tag -s -a v${MAJOR}.${MINOR}.${PATCH} -m 'llm-d v${MAJOR}.${MINOR}.${PATCH} Release'
    ```

1. Push the tag to the llm-d repo.

   For a release candidate:

    ```shell
    git push ${REMOTE} v${MAJOR}.${MINOR}.${PATCH}-rc.${RC}
    ```

   For a major, minor or patch release:

    ```shell
    git push ${REMOTE} v${MAJOR}.${MINOR}.${PATCH}
    ```

1. Pushing the tag triggers CI action to build and publish the container image to the [ghcr registry].
1. Test the steps in the tagged quickstart guide after the PR merges. TODO add e2e tests! <!-- link to an e2e tests once we have such one -->

### Validate the release branch and update the release matrix

The nightly e2e lanes test `main`, so a [Release Testing
matrix](../../release/README.md#release-testing) that reflects the release branch
needs those lanes dispatched against it explicitly. Neither step below is triggered
by the tag.

1. Dry run the dispatcher to review the lane list and each lane's cron window:

   ```shell
   gh workflow run release-e2e.yaml --repo llm-d/llm-d --ref main \
     --field release_branch=release-${MAJOR}.${MINOR} \
     --field lanes='*' \
     --field dry_run=true
   ```

1. Dispatch the lanes, in batches. A lane's own nightly cron cancels an in-flight
   release run of that lane, so avoid starting one within ~4h of the cron time shown
   in the dry-run summary:

   ```shell
   gh workflow run release-e2e.yaml --repo llm-d/llm-d --ref main \
     --field release_branch=release-${MAJOR}.${MINOR} \
     --field lanes='optimized-baseline-*' \
     --field dry_run=false
   ```

1. Wait for the lanes to publish their badges, and list the ones still missing:

   ```shell
   grep -h 'badge_name:' .github/workflows/nightly-e2e-*.yaml \
     | awk '{print $2}' | sort -u > /tmp/expected.txt
   gh api repos/llm-d/llm-d/contents/badges?ref=gh-pages --jq '.[].name' \
     | sed -n "s/_release-${MAJOR}\.${MINOR}\.json\$//p" | sort -u > /tmp/got.txt
   comm -23 /tmp/expected.txt /tmp/got.txt
   ```

   Two lanes share a badge name, so expect 42 names for 43 lanes.

1. Re-dispatch any lane that failed for infrastructure reasons:

   ```shell
   gh workflow run <lane>.yaml --repo llm-d/llm-d --ref main \
     --field matrix_type=release-${MAJOR}.${MINOR}
   ```

1. Render the matrix into `release/README.md` on `main`.

   For a release candidate:

   ```shell
   gh workflow run release-matrix.yaml --repo llm-d/llm-d --ref main \
     --field version=v${MAJOR}.${MINOR}.${PATCH}-rc.${RC} \
     --field release_branch=release-${MAJOR}.${MINOR}
   ```

   For a major, minor or patch release:

   ```shell
   gh workflow run release-matrix.yaml --repo llm-d/llm-d --ref main \
     --field version=v${MAJOR}.${MINOR}.${PATCH} \
     --field release_branch=release-${MAJOR}.${MINOR}
   ```

1. Take ownership of the generated commit — CI cannot produce a DCO sign-off or a
   signature on your behalf — and merge the PR:

   ```shell
   gh pr checkout release-matrix/v${MAJOR}.${MINOR}.${PATCH}
   git commit --amend --reset-author -s -S --no-edit
   git push --force-with-lease
   ```

### Create the release

1. Create a [new release]:
    1. Choose the tag that you created for the release.
    1. Use the tag as the release title, i.e. `v0.1.0` refer to previous release for the content of the release body.
    1. Click "Generate release notes" and preview the release body.
    1. Copy the **Release Testing** matrix section for `release-${MAJOR}.${MINOR}`
       from [`release/README.md`](../../release/README.md) into the release body, so
       the notes carry the guide validation status for this release.
    1. Go to Gateway Inference Extension latest release and make sure to include the highlights in llm-d as well.
    1. If this is a release candidate, select the "This is a pre-release" checkbox.
1. If you find any bugs in this process, create an [issue].

## Announce the Release

Use the following steps to announce the release.

1. Send an announcement email to `llm-d-contributors@googlegroups.com` with the subject:

   ```shell
   [ANNOUNCE] llm-d v${MAJOR}.${MINOR}.${PATCH} is released
   ```

1. Add a link to the final release in this issue.

1. Close this issue.

[repo]: https://github.com/llm-d/llm-d
[ghcr registry]: https://github.com/llm-d/llm-d/pkgs/container/llm-d
[new release]: https://github.com/llm-d/llm-d/releases/new
[issue]: https://github.com/llm-d/llm-d/issues/new/choose
