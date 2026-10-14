# Nightly Benchmark Regression tests

Each one of the [guides](../../guides) in `llm-d` (also known as "well-lit paths", undergoes a nightly test cycle. This test aims to check not only the basic functionality of a given guide, but in addition to it, track performance regressions by running a "representative" (as defined by the "guide owners", e.g., [optimized-baseline](../../guides/optimized-baseline/OWNERS)) workload against it.

## Components

The main component of this arrangement are the following.

1. Clusters: several members of the `llm-d` project have contributed generously with signifcant computational and human resources to allow for the nightly benchmark testing to take place: AMD, Coreweave, Google, IBM, Intel. Each one of these clusters house a (GitHub) "Actions Runner Controller" (ARC) which will be tasked with executing a particular guide with a combination of parameters.
2. Automation: the `llm-d` stack on each guide, described via a combination of `Kubernetes` manifests patched with `kustomize` files, it stood up by our benchmark tooling [`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark).
This tool can automatically parse a guide's README (e.g., [optimized-baseline](../../guides/optimized-baseline/README.md)) and automatically execute the commands described there. Furthermore, it is `llm-d-benchmark`'s responsibility, once a new stack is fully stood up, to test for its basically functionality (i.e., does it respond to a small set of inference queries?) and then proceed to executed the representative workload defined by the guide owner.
The list of templates for the workloads can be located at the [`workloads`](https://github.com/llm-d/llm-d-benchmark) directory, in the format `<harness name>/guide_<guide name>_<sequential number>.yaml.in` (the "sequential number" allows for multiple representative workloads for each guide).
3. Jobs: while, for technical reasons (e.g., lack of hardware resources), not every guide is stood up against each cluster, the job names on this directory are normalized following the convention `<workflow>-<guide>-<provider>-<offload destination>-<accelerator>-<inference engine>-<cache connector>.yaml`. Each component of a job name can assume the following values:
   * `<workflow>`: kept as `nightly-e2e` for historical reasons
   * `<guide>`:  list provided by $(`find guides/ -maxdepth 2 -name README.md -print | grep -Ev "rollouts|prereqs|recipes|guides/README.md"`)
   * `<provider>`: indicates which companies/members (and teams) are providing hardware resources for testing (currently, `amd`, `cks`, `gke`, `ibm`, and `intel`)
   * `<offload destination>`: possible values are `acc` (indicating that no offloading is done, as all blocks are retained within the accelerator),   `gpu`, `storage`
   * `<accelerator>`: `gpu`, `tpu`, `rocm` and `xpu`
   * `<cache connector>`: `x` (for "don't care"/"not defined"), `native` (for vllm) and `lmcache`

## Status Reporting

The result of each run is displayed on "status badges" on the [release matrix](https://github.com/llm-d/llm-d/blob/main/release/README.md). This matrix presents results in a color-code format specified at [this workflow](https://github.com/llm-d/llm-d-infra/blob/main/.github/workflows/reusable-update-badge.yaml).

Given the fact that, due to the complex nature of `llm-d` stack standup across multiple cluster from different providers can result in **transient** errors not directly related to a particular guide (i.e., not related to `llm-d`), each individual guide has its own set of status badges on its README (e.g, look at the top of [optimized-baseline](../../guides/optimized-baseline/README.md)), according to the following rule: a guide is considered to be "passing" (i.e., `green`) if there was at least ONE job which managed to successfully stand it up in the past 5 days.

While the aforementioned [release matrix](https://github.com/llm-d/llm-d/blob/main/release/README.md) is of interest for mantainers and developers, all users/deployers/customers are encouraged to focus on the status presented at the top of guide's README.

`release/README.md` holds two matrices, both generated from the `nightly-e2e-*.yaml`
workflow files:

* **Nightly Testing** — a live mirror of `main`, kept in sync by
  `scripts/sync-nightly-matrix.py` (its badges point at the shields endpoint and
  update continuously).
* **Release Testing** — one section per release, reporting runs made against the
  *release branch* rather than `main`. Both steps are manual, and neither is
  triggered by tagging:

  1. [`release-e2e.yaml`](release-e2e.yaml) dispatches the `nightly-e2e-*` lanes with
     `matrix_type=release-<major>.<minor>`. That input makes the reusables check out
     the release branch and write their badge to
     `badges/<badge_name>_release-<major>.<minor>.json` instead of the unsuffixed
     nightly file, so the two matrices never overwrite each other. `list_only`
     reviews the matched lanes without dispatching them, and `dry_run` is forwarded
     to every lane that is dispatched, so a batch can be exercised against the
     release branch without standing up a stack. Before dispatching, it runs
     `scripts/seed-release-badges.py` to place a grey `never run` badge on any cell
     that has none — a shields.io endpoint has no default-if-missing, so an absent
     file renders `custom badge: resource not found`. Seeding is create-if-absent and
     covers the whole matrix rather than the `lanes` subset.
  2. [`release-matrix.yaml`](release-matrix.yaml) then runs
     `scripts/sync-release-matrix.py` to render that release's section and open a PR
     against `main`.

  These badges are live endpoints too, so re-running one lane updates the matrix on
  its own. Older sections keep working because their badge files are never
  rewritten. Step-by-step commands are in the [new release issue
  template](../ISSUE_TEMPLATE/new-release.md).

## Slack notifications

[`notify-slack-nightly.yaml`](notify-slack-nightly.yaml) posts failed, unstable, and timed-out **scheduled** nightly results to `#llm-d-ci-alerts`. Successful runs are not posted. For these runs, the notifier mentions the owners in that guide's `OWNERS` file when their Slack IDs are listed in [`.github/slack-owner-ids.yaml`](../slack-owner-ids.yaml).

This replaces the old `/github subscribe` integration, which also fired on manual `workflow_dispatch` runs.

### How routing works

[`.github/slack-channels.yaml`](../slack-channels.yaml) is the source of truth. It lists notified nightlies under `#llm-d-ci-alerts`, **keyed by workflow file name**, and lists nightlies that are deliberately not notified (each with a reason).

The `workflows:` list in the `workflow_run` trigger is *generated* from that file:

```bash
python scripts/sync-slack-channels.py --fix     # regenerate
python scripts/sync-slack-channels.py --check   # verify (runs in pre-commit)
python scripts/sync-slack-channels.py --audit   # print the routing table
```

Two different identifiers are in play, deliberately:

* The **trigger** must use display names, because `workflow_run` matches on nothing else. That makes it the part that breaks silently — rename a nightly and the notifications just stop, with no error, which looks exactly like a quiet night. Generating the list means the rename turns CI red in the same PR that does the renaming.
* **Runtime routing** uses the workflow *file* name (`workflow_run.path`), which is stable — it is already baked into `badge_name`, `release/README.md`, `/test-nightly` and the `consolidate-status-*` workflows. So a renamed display name can only stop notifications; it can never send them to the wrong channel.

A nightly that is in neither `channels` nor `skip` fails the `sync-slack-channels` pre-commit hook. That check is the only place the mistake can be caught: an unrouted nightly is also absent from the generated trigger, so it never fires at all, and the symptom is *missing messages* — invisible by construction.

### Deliberate decisions

These are easy to mistake for bugs, so they are recorded here:

* **Only `schedule` runs notify.** Manual dispatches and `/test-nightly` runs are filtered out via `github.event.workflow_run.event`. The replay input checks an existing scheduled run.
* **Re-runs of a scheduled run DO notify**, marked `(attempt N)`. When a SIG re-runs a nightly that failed on a transient cluster error, the re-run's result is the current truth about that guide — suppressing it would leave the channel showing an already-fixed failure as the last word.
* **Success, `cancelled`, and `skipped` runs do not notify.** An all-skipped run means nothing exercised the guide. GitHub's `neutral`, `stale`, and `action_required` conclusions are reported as unstable.
* **The guide's result is computed from the `nightly` jobs, not just the run's conclusion.** A failed `update-badge` job still posts a warning to the alert channel, but it does not mention guide owners when the guide test passed.

### Testing a change

`workflow_run` **and** `workflow_dispatch` only work from the default branch, so the workflow itself cannot be exercised from a feature branch. Test the logic locally against any scheduled run instead:

```bash
GH_TOKEN=$(gh auth token) python .github/scripts/notify-slack-nightly.py \
    --repo llm-d/llm-d --run-id <run-id> --dry-run
```

Once merged, the workflow's `workflow_dispatch` inputs replay an existing run (`run_id`), optionally to a scratch channel (`channel_override`) and without posting (`dry_run`).

### Slack app setup

One-time, needs Slack workspace admin plus repo admin:

1. Create an app at [api.slack.com/apps](https://api.slack.com/apps). *From an app manifest* is the quickest route — paste the manifest below — or use *From scratch* and add the scope by hand in step 2.

   ```yaml
   display_information:
     name: llm-d CI
     description: Posts failed nightly CI results to the llm-d CI alert channel.
   features:
     bot_user:
       display_name: llm-d-ci
       always_online: false
   oauth_config:
     scopes:
       bot:
         - chat:write
   settings:
     org_deploy_enabled: false
     socket_mode_enabled: false
     token_rotation_enabled: false
   ```

   Note `bot_user.display_name` is the bot's handle and only accepts lowercase letters, digits, `-`, `_` and `.` — hence `llm-d-ci` rather than `llm-d CI`. That handle is what you type in step 4.
2. Under *OAuth & Permissions*, add the **`chat:write`** bot scope. Optionally add `channels:join` so the bot can add itself to public channels, which removes the `not_in_channel` failure mode. Do **not** add `chat:write.public` — it allows posting to any channel without membership, bypassing that control entirely.
3. Install the app and copy the bot token (`xoxb-…`). A token is valid for one workspace only.
4. **Invite the bot to `#llm-d-ci-alerts`**: `/invite @llm-d-ci` (let Slack's autocomplete resolve the handle). For a private channel, a member has to do this from inside it. Forgetting this makes the posting step fail with `not_in_channel`.
5. Add the token as the `SLACK_BOT_TOKEN` repository secret (*Settings → Secrets and variables → Actions*).

The [`.github/slack-owner-ids.yaml`](../slack-owner-ids.yaml) file lists every login in `guides/**/OWNERS`. Fill in each Slack member ID, and comment out owners without a Slack account; add new guide owners there as they are added. Only listed owners with a blank ID produce a warning. Slack member IDs look like `U012ABCDEF`; GitHub logins alone do not create Slack mentions.

## Adding a new guide

All nightly benchmark workflows rely heavily on these two "reusable" workflows on [`llm-d-infra`](https://github.com/llm-d/llm-d-infra):

* [`reusable-ci-nightly-benchmark.yaml`](https://github.com/llm-d/llm-d-infra/blob/main/.github/workflows/reusable-ci-nightly-benchmark.yaml)
* [`reusable-query-success-past-runs.yaml`](https://github.com/llm-d/llm-d-infra/blob/main/.github/workflows/reusable-query-success-past-runs.yaml).

Developers aiming to add a new guide or testing an existing guide on a new cluster or with a new set of parameters should open a PR with **two** new workflows: one for the "nightly benchmark", and one for "consolidated status". Again, an illustratibe example using `optimized-baseline`:

The same PR must also assign the new nightly a Slack channel in [`.github/slack-channels.yaml`](../slack-channels.yaml) (or add it to `skip` with a reason) and run `python scripts/sync-slack-channels.py --fix`; see [Slack notifications](#slack-notifications) above. The `sync-slack-channels` pre-commit hook fails until this is done.

```bash
[llm-d]$ ls .github/workflows/*optimized-baseline*
.github/workflows/consolidate-status-optimized-baseline-amd-acc-rocm-vllm-x.yaml   .github/workflows/nightly-e2e-optimized-baseline-amd-acc-rocm-vllm-x.yaml
.github/workflows/consolidate-status-optimized-baseline-cks-acc-gpu-vllm-x.yaml    .github/workflows/nightly-e2e-optimized-baseline-cks-acc-gpu-vllm-x.yaml
.github/workflows/consolidate-status-optimized-baseline-gke-acc-gpu-vllm-x.yaml    .github/workflows/nightly-e2e-optimized-baseline-gke-acc-gpu-vllm-x.yaml
.github/workflows/consolidate-status-optimized-baseline-gke-acc-tpu-vllm-x.yaml    .github/workflows/nightly-e2e-optimized-baseline-gke-acc-tpu-vllm-x.yaml
.github/workflows/consolidate-status-optimized-baseline-ibm-acc-gpu-vllm-x.yaml    .github/workflows/nightly-e2e-optimized-baseline-ibm-acc-gpu-vllm-x.yaml
.github/workflows/consolidate-status-optimized-baseline-intel-acc-xpu-vllm-x.yaml  .github/workflows/nightly-e2e-optimized-baseline-intel-acc-xpu-vllm-x.yaml
```

## Triggering a nightly benchmark regression test

There are two main possibilities to trigger a nightly test job.

* The first is to go `Actions` on **GitHub Actions UI** and select a praticular workflow to be executed (look for the ones prefixed by `Nightly -`). This is useful if the goal is to quickly re-test a guide using nightly built images, or after a cluster-specific issue was fixed.

* The second is to comment directly in an open PR, using **PR Slash Commands**. Here, the author of the PR, **provided he or she has the right permissions**, can simply comment with `/test-nightly <name of the workflow>` and new CI/CD job will be created **using the code from the PR**. For instance, `/test-nightly e2e-pd-disaggregation-gke-acc-gpu-vllm-x` will start a test against the `GKE` cluster available for `llm-d`, with the parameters specified on the name.

## llm-d.ai previews for PRs

Every PR that touches content published on [llm-d.ai](https://llm-d.ai) gets a preview of the site built with that PR's content. That means `docs/**`, `guides/**`, and the community pages mirrored from the repo root (`CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `SIGS.md`). Two workflows split the work:

* [`ci-site-preview.yaml`](ci-site-preview.yaml) ("CI Site Preview") runs on `pull_request`. It checks out the PR (merged into `main`) and [`llm-d/llm-d.github.io`](https://github.com/llm-d/llm-d.github.io)@`main`. It then runs the site's normal build against the PR checkout: `npm ci`, `make llmd-site`, `LLMD_REPO=<pr checkout> ./bin/llmd-site sync main` and `./bin/llmd-site build`. The static site is uploaded as the `site-preview` artifact (kept 7 days), together with a small `site-preview-meta` artifact holding the PR number and head SHA. The run's job summary explains how to view the artifact.
* [`site-preview-deploy.yaml`](site-preview-deploy.yaml) runs on `workflow_run` after a successful "CI Site Preview" run for a PR. It publishes the artifact to Netlify when configured. It also keeps one sticky comment on the PR, updated on every push, that links to the preview, or to the artifact when Netlify is not configured.

PR content shows up under the **dev** docs version (`/docs/dev/...`), the same place `main` is published. "Edit this page" and source links point at `llm-d/llm-d@main`.

### Security model

The build runs content from the PR, which may come from a fork. It therefore runs with a read-only `GITHUB_TOKEN` and no secrets, like any `pull_request` workflow.
Anything that needs a secret or write access lives in the `workflow_run` workflow. GitHub always runs that workflow from the default branch, in the base repository's context, so a PR cannot change what it does.
It never checks out or executes PR code. It downloads the built static files and reads the metadata as data. It deploys only if the metadata matches the PR resolved from the event and the API (number and head SHA).
Each job asks for the minimum permissions: `actions: read` and `pull-requests: read` to deploy, and `pull-requests: write` to comment. The Netlify secrets are only exposed to the deploy steps.

Two consequences of `workflow_run`:

* It matches the build workflow by display name. Renaming "CI Site Preview" silently stops deploys unless the `workflows:` list in `site-preview-deploy.yaml` is updated too.
* Changes to `site-preview-deploy.yaml` only take effect once merged to `main`.

### Maintainer setup

Without any setup, previews are available as artifacts and the PR comment links to them. To publish them instead (one-time, needs repo admin):

1. Create a Netlify site for previews, for example from the Netlify UI with *Add new site → Deploy manually*. It does not need a Git connection or build settings; the workflow uploads prebuilt files.
2. Create a Netlify personal access token (*User settings → Applications*). A dedicated bot account limited to that site is preferable.
3. Add the repository secrets `NETLIFY_AUTH_TOKEN` (the token) and `NETLIFY_SITE_ID` (*Site configuration → Site ID*) under *Settings → Secrets and variables → Actions*.

Each PR is then deployed to a stable alias, `https://pr-<number>--<site-name>.netlify.app`, which is overwritten on every push.

### Testing locally

The build job is plain shell. To reproduce it, with Go and Node 24:

```bash
git clone https://github.com/llm-d/llm-d.github.io site
cd site
npm ci
make llmd-site
LLMD_REPO=/path/to/your/llm-d/checkout ./bin/llmd-site sync main   # reads the checkout as-is, uncommitted edits included
./bin/llmd-site build
npx serve build    # then open http://localhost:3000/docs/dev
```

To view a CI preview, download the `site-preview` artifact from the run, unzip it into `build/`, and serve it the same way. `python3 -m http.server -d build` also works, but only for URLs ending in `.html`.
