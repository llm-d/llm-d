# Triggering E2E Nightly Regression Tests on GitHub

The `llm-d` repository maintains a suite of nightly End-to-End (E2E) regression tests across different Kubernetes environments (GKE, CKS, OpenShift). While these workflows run automatically every day, you can trigger them on-demand to validate PR changes before merging.

This guide explains how to trigger these tests using **PR Slash Commands** or manually via the **GitHub Actions UI**.

---

## 1. Triggering via PR Slash Commands

If you are working on a Pull Request, you can trigger one or more nightly E2E tests directly by writing a comment on the PR page.

### Command Format

```text
/test-nightly <nightly-name-or-pattern>
```

### Examples

* **Trigger a single test:**

  ```text
  /test-nightly optimized-baseline-gke
  ```

* **Trigger all GKE tests:**

  ```text
  /test-nightly *-gke
  ```

* **Trigger all OpenShift (OCP) tests:**

  ```text
  /test-nightly *-ocp
  ```

* **Trigger all disaggregation tests:**

  ```text
  /test-nightly pd-disaggregation-*
  ```

### Available Nightly Names

You can use any of the following exact names or glob patterns matching them:

| Name | Environment | Guide / Path |
| :--- | :--- | :--- |
| `optimized-baseline-cks` | CoreWeave Kubernetes Service (CKS) | [optimized-baseline](../guides/optimized-baseline) |
| `optimized-baseline-gke` | Google Kubernetes Engine (GKE) | [optimized-baseline](../guides/optimized-baseline) |
| `optimized-baseline-ocp` | OpenShift (OCP) | [optimized-baseline](../guides/optimized-baseline) |
| `pd-disaggregation-cks` | CKS | [pd-disaggregation](../guides/pd-disaggregation) |
| `pd-disaggregation-gke` | GKE | [pd-disaggregation](../guides/pd-disaggregation) |
| `pd-disaggregation-ocp` | OCP | [pd-disaggregation](../guides/pd-disaggregation) |
| `precise-prefix-cache-cks` | CKS | [precise-prefix-cache-routing](../guides/precise-prefix-cache-routing) |
| `precise-prefix-cache-gke` | GKE | [precise-prefix-cache-routing](../guides/precise-prefix-cache-routing) |
| `precise-prefix-cache-ocp` | OCP | [precise-prefix-cache-routing](../guides/precise-prefix-cache-routing) |
| `predicted-latency-cks` | CKS | [predicted-latency-routing](../guides/predicted-latency-routing) |
| `predicted-latency-gke` | GKE | [predicted-latency-routing](../guides/predicted-latency-routing) |
| `tiered-prefix-cache-cpu-offloading-gke` | GKE | [tiered-prefix-cache](../guides/tiered-prefix-cache) |
| `tiered-prefix-cache-cpu-offloading-lmcache-gke` | GKE | [tiered-prefix-cache](../guides/tiered-prefix-cache) |
| `tiered-prefix-cache-cpu-offloading-ocp` | OCP | [tiered-prefix-cache](../guides/tiered-prefix-cache) |
| `wide-ep-lws-cks` | CKS | [wide-ep-lws](../guides/wide-ep-lws) |
| `wide-ep-lws-gke` | GKE | [wide-ep-lws](../guides/wide-ep-lws) |
| `wide-ep-lws-ocp` | OCP | [wide-ep-lws](../guides/wide-ep-lws) |
| `wva-cks` | CKS | [wva](../guides/workload-autoscaling/README.wva.md) |
| `wva-ocp` | OCP | [wva](../guides/workload-autoscaling/README.wva.md) |

---

## 2. Triggering Manually via GitHub Actions UI

You can trigger any nightly workflow manually using the **Run workflow** button in the GitHub repository UI.

### Steps

1. Navigate to the main page of the GitHub repository.
2. Click on the **Actions** tab at the top of the page.
3. In the left sidebar, find the workflow under the **Workflows** list (e.g., `Nightly - Tiered Prefix Cache CPU Offloading E2E (GKE)`).
4. Click on the workflow name.
5. Click the **Run workflow** dropdown on the right side of the workflow runs list.
6. Select the branch you want to run the workflow on.
7. Set optional parameters:
   * **Skip cleanup after tests (for debugging):** Set to `true` if you want the test namespace, cluster resources, and nodes to persist for debugging post-run.
8. Click the green **Run workflow** button.

---

## 3. Viewing Test Status and Logs

1. **PR Comments:** When you trigger a test via a slash command, a GitHub Actions bot will add a comment on your PR reacting with a 🚀 (rocket) and linking directly to the running workflow.
2. **Actions Log:** Click the provided link or go to the **Actions** tab to watch the test progression.
3. **Artifacts:** Test artifacts (such as test validation reports, logs, and performance metrics) are uploaded and available at the bottom of the workflow run page once completed.
