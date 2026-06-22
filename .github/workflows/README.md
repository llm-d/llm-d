# Nightly Benchmark Regression tests

Each one of the [guides](../guides) in `llm-d` (also known as "well-lit paths", undergoes a nightly test cycle. This test aims to check not only the basic functionality of a given guide, but in addition to it, track performance regressions by running a "representative" (as defined by the "guide owners", e.g., [optimized-baseline](../guides/optimized-baseline/OWNERS)) workload against it.

## Components

The main component of this arrangement are the following.

1. Clusters: several members of the `llm-d` project have contributed generously with signifcant computational and human resources to allow for the nightly benchmark testing to take place: AMD, Coreweave, Google, IBM, Intel. Each one of these clusters house a (GitHub) "Actions Runner Controller" (ARC) which will be tasked with executing a particular guide with a combination of parameters.
2. Automation: the `llm-d` stack on each guide, described via a combination of `Kubernetes` manifests patched with `kustomize` files, it stood up by our benchmark tooling [`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark).
This tool can automatically parse a guide's README (e.g., [optimized-baseline](../guides/optimized-baseline/README.md)) and automatically execute the commands described there. Furthermore, it is `llm-d-benchmark`'s responsibility, once a new stack is fully stood up, to test for its basically functionality (i.e., does it respond to a small set of inference queries?) and then proceed to executed the representative workload defined by the guide owner.
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

Given the fact that, due to the complex nature of `llm-d` stack standup across multiple cluster from different providers can result in **transient** errors not directly related to a particular guide (i.e., not related to `llm-d`), each individual guide has its own set of status badges on its README (e.g, look at the top of [optimized-baseline](../guides/optimized-baseline/README.md)), according to the following rule: a guide is considered to be "passing" (i.e., `green`) if there was at least ONE job which managed to successfully stand it up in the past 5 days.

While the aforementioned [release matrix](https://github.com/llm-d/llm-d/blob/main/release/README.md) is of interest for mantainers and developers, all users/deployers/customers are encouraged to focus on the status presented at the top of guide's README.

## Adding a new guide

All nightly benchmark workflows rely heavily on these two "reusable" workflows on [`llm-d-infra`](https://github.com/llm-d/llm-d-infra):

* [`reusable-ci-nightly-benchmark.yaml`](https://github.com/llm-d/llm-d-infra/blob/main/.github/workflows/reusable-ci-nightly-benchmark.yaml)
* [`reusable-query-success-past-runs.yaml`](https://github.com/llm-d/llm-d-infra/blob/main/.github/workflows/reusable-query-success-past-runs.yaml).

Developers aiming to add a new guide or testing an existing guide on a new cluster or with a new set of parameters should open a PR with **two** new workflows: one for the "nightly benchmark", and one for "consolidated status". Again, an illustratibe example using `optimized-baseline`:

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

* The second is to comment directly in an open PR, using **PR Slash Commands**. Here, the author of the PR, **provided he or she has the right permissions**, can simply comment with `/test-nightly <name of the workflow>` and new CI/CD job will be created **using the code from the PR**. For instance, `/test-nightly nightly-e2e-pd-disaggregation-gke-acc-gpu-vllm-x` will start a test against the `GKE` cluster available for `llm-d`, with the parameters specified on the name.
