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

[`notify-slack-nightly.yaml`](notify-slack-nightly.yaml) posts **one message a day** to `#llm-d-ci-alerts`, at **20:00 UTC**, summarising every **scheduled** nightly that failed, timed out, came back unstable, or did not run at all. The failures are grouped by guide, and each group mentions the owners in that guide's `OWNERS` file when their Slack IDs are listed in [`.github/slack-owner-ids.yaml`](../slack-owner-ids.yaml).

This replaces the per-run notifier from #2598, which posted one message per failing nightly. Because the nightlies are staggered across 01:00–18:30 UTC, that produced a dozen-odd separate messages a day, and none of them answered "how was last night?" without scrolling the channel.

### Why 20:00 UTC

The nightly cycle starts at 01:00 and the last lane kicks off at 18:30, with runs taking ~45–60m, so the cycle is done by ~19:30. Firing in that quiet gap means each digest covers exactly one complete night and never splits one across two messages. The window is the preceding 24h.

### Why nothing is accumulated

The digest holds no state between runs: it asks the Actions API what each routed nightly did in the window.

The tempting alternative — having each nightly append to a shared store (an artifact, an orphan branch, issue comments) that the digest then drains — needs locking for the lanes that finish in the same minute, loses an alert outright when a write fails, and has to be pruned. The API already holds every result, so copying it buys nothing. Being stateless is also what makes any past window replayable exactly.

### How routing works

[`.github/slack-channels.yaml`](../slack-channels.yaml) is the source of truth. It lists notified nightlies under `#llm-d-ci-alerts`, **keyed by workflow file name**, and lists nightlies that are deliberately not notified (each with a reason).

```bash
python scripts/sync-slack-channels.py --check   # verify (runs in pre-commit)
python scripts/sync-slack-channels.py --audit   # print the routing table
```

File names are used rather than display `name:` values because the file name is the stable identifier — it is already baked into `badge_name`, `release/README.md`, `/test-nightly` and the `consolidate-status-*` workflows. Renaming a workflow's display name cannot misroute or silence it.

> The per-run notifier also needed a *generated* list of display names, because `workflow_run` matches on nothing else — which made renaming a nightly silently stop its notifications. The digest resolves routing from file names at runtime, so that list and its failure mode are both gone.

A nightly that is in neither `channels` nor `skip` fails the `sync-slack-channels` pre-commit hook. That check is the guardrail: an unrouted nightly is simply absent from the digest, and the symptom is a *missing line* in a message nobody diffs against a list — invisible by construction. As a backstop, the digest appends a warning naming any nightly on disk that is routed nowhere.

### Deliberate decisions

These are easy to mistake for bugs, so they are recorded here:

* **Nothing is posted when there is nothing to report.** A daily all-green message was considered and declined — this is an alert channel. So that silence stays trustworthy, a failure of the digest job itself posts a short notice to `fallback_channel`: otherwise a broken digest would look exactly like a quiet night.
* **Only `schedule` runs count.** Manual dispatches and `/test-nightly` runs are excluded by the `event=schedule` API filter, so a green manual re-run cannot paper over a failed nightly.
* **The newest run in the window wins**, and a re-run is marked `(attempt N)`. When a SIG re-runs a nightly that failed on a transient cluster error, that re-run is the current truth about the guide — reporting the earlier failure would leave the digest showing an already-fixed problem as the last word.
* **Success, `cancelled` and `skipped` do not appear** beyond the footer counts. An all-skipped run means nothing exercised the guide. GitHub's `neutral`, `stale` and `action_required` conclusions are reported as unstable.
* **The guide's result is computed from the `nightly` jobs, not the run's conclusion.** `update-badge` runs with `if: always()`, so a green test whose badge push failed comes out as a failed *run*. Those are listed separately as "guide passed, `update-badge` failed" and do **not** mention guide owners.
* **"Did not run" only covers nightlies with an active `cron`.** Seven routed lanes (the IBM/OCP ones) have their whole `schedule:` block commented out; listing those every day would train people to ignore the section that exists to catch a genuinely stalled schedule. They appear only in the footer's "not scheduled" count.
* **Above 25 failures the digest collapses to one line each** and says main is likely broken. At that point the answer is one investigation, not forty.

### Testing a change

The digest script is fully exercisable locally against the real repository — this is the main way to test a change, and it posts nothing:

```bash
# The last 24h.
GH_TOKEN=$(gh auth token) python .github/scripts/slack-nightly-digest.py \
    --repo llm-d/llm-d --dry-run

# Any past window, e.g. to reproduce a specific night.
GH_TOKEN=$(gh auth token) python .github/scripts/slack-nightly-digest.py \
    --repo llm-d/llm-d --since 2026-10-04T20:00:00Z --until 2026-10-05T20:00:00Z --dry-run
```

`schedule` and `workflow_dispatch` only work from the default branch, so the posting path itself can only be exercised once merged. The `workflow_dispatch` inputs cover that: `window_hours` or `since`/`until` to pick the window, `dry_run` (default `true`) to render into the job summary without posting, and `channel_override` to post to a scratch channel such as `#llm-d-ci-test`.

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

The same PR must also assign the new nightly a Slack channel in [`.github/slack-channels.yaml`](../slack-channels.yaml) (or add it to `skip` with a reason); see [Slack notifications](#slack-notifications) above. The `sync-slack-channels` pre-commit hook fails until this is done.

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
