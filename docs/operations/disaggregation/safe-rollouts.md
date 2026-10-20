# Safe Rollouts of Disaggregated Deployments with DisaggregatedSet

This guide shows how to replace one version of a prefill/decode (P/D) deployment with another under live traffic, without failing requests and without pairing a prefill pod of one version with a decode pod of another. It applies to deployments that run prefill and decode as a [`DisaggregatedSet`](./disaggregatedset.md), such as the [P/D disaggregation](../../../guides/pd-disaggregation/README.md) and [wide-ep](../../../guides/wide-ep/README.md) guides.

A version change is anything that changes a role's pod template: the model server image, engine flags, the KV connector, resources, or node placement.

## Why P/D Rollouts Need Care

Two things make P/D rollouts harder than rolling a plain `Deployment`:

* **A request spans two pods.** The decode pod pulls the KV cache that a prefill pod produced. If the two run different versions, the transfer can fail (vLLM rejects [incompatible peers](./vllm.md#rollouts) at the NIXL handshake), or worse, succeed with a mismatched KV layout and return a `200` with wrong output. The llm-d Router schedules the prefill and decode steps independently, so without extra configuration it pairs any prefill pod with any decode pod in the `InferencePool`.
* **Draining a pod can break another pod's request.** A prefill pod holds the KV cache of a finished prefill until a decode pod pulls it. Deleting the prefill pod too early fails the decode side of that request.

Three building blocks handle this:

| Building block | What it does |
| --- | --- |
| DisaggregatedSet revisions | Every pod of one version, of every role, carries the same `disaggregatedset.x-k8s.io/revision` label. Changing either role's template creates a new revision for both. |
| Router rollout screener | The `disaggregatedset-rollout-screener` plugin picks one revision per request, weighted by Ready pods, and hides every other revision from both the prefill and the decode profile. A revision gets no traffic until each required role has a Ready pod. |
| Graceful drain | `preStop`, `--shutdown-timeout` and `terminationGracePeriodSeconds` keep a deleted pod serving until its in-flight requests, and the KV pulls that depend on it, are done. |

## Choose a Rollout Strategy

| | Automatic rolling update | Manual canary |
| --- | --- | --- |
| Objects | One DisaggregatedSet | One DisaggregatedSet per version (for example `-v0` and `-v1`) |
| How a step happens | You change the template; the controller adds new groups and removes old ones within each role's `maxSurge` / `maxUnavailable` | You patch each set's role `replicas` |
| Pause and hold a canary | No: the set rejects `partition`, so a rollout runs to completion | Yes: hold at any step for as long as you need |
| Roll back | Revert the template, which starts a new rollout | Scale the new set to zero and the old one back up |
| Extra capacity | At most each role's `maxSurge` | The new version's canary shape, for as long as you hold it |
| Router scope | The set's name label | A label shared by both sets |

Use the automatic rolling update for routine changes. Use the manual canary when you want to bake a new version on a slice of traffic before committing to it, for example a new vLLM version, KV connector or driver.

## Prerequisites

* A running P/D deployment on a DisaggregatedSet, with the LeaderWorkerSet controller `v0.11.1` or newer and `enableDisaggregatedSet=true` (see the [P/D disaggregation prerequisites](../../../guides/pd-disaggregation/README.md#prerequisites)).
* The llm-d Router installed for that deployment. The rollout screener is an Alpha plugin, so the EPP needs `allow-experimental-plugins: true`.
* Model server images pinned by tag or digest. NIXL's compatibility check includes the vLLM version, so two nodes that pulled different `nightly` images cannot transfer KV to each other.

## Step 1: Keep P/D Pairs Inside One Revision

Add the rollout screener to the top-level `plugins` list of your EPP configuration. It runs once per request before every scheduling profile, so do not add it to a profile. For the P/D guide, edit [`pd-disaggregation.values.yaml`](../../../guides/pd-disaggregation/router/pd-disaggregation.values.yaml) and upgrade the router release:

```yaml
router:
  epp:
    flags:
      # disaggregatedset-rollout-screener is Alpha stability.
      allow-experimental-plugins: true
    pluginsCustomConfig:
      pd-config.yaml: |
        apiVersion: llm-d.ai/v1
        kind: EndpointPickerConfig
        plugins:
        - type: disaggregatedset-rollout-screener
          parameters:
            scope:
              # Automatic rolling update: the set's name.
              # Manual canary: a label both sets carry, e.g. "llm-d.ai/guide=pd-disaggregation".
              labelSelector: "disaggregatedset.x-k8s.io/name=pd-disagg-vllm"
            revisionGating:
              mode: max-role
              requiredRoles: [prefill, decode]
        # ...the existing plugins, unchanged
```

The wide-ep and model guides ([DeepSeek-V4](../../../guides/models/deepseek-v4/README.md), [GLM-5.2](../../../guides/models/glm-5-2/README.md)) already ship this plugin.

Settings to decide:

* **`scope.labelSelector`** selects the pods the screener considers. It must cover every revision that can serve traffic. With one set, the set's name label does this. With one set per version, use a label that both sets carry and that the `InferencePool` also selects on.
* **`revisionGating.mode`** sets how traffic is shared between revisions while both serve:
  * `max-role` weights each revision by its Ready pods of the globally most numerous role. Use it for a stable, asymmetric ratio such as 2P:10D.
  * `sum` weights each revision by all of its Ready pods. Use it when role ratios change during the rollout.
* **`revisionGating.disableCoordination`**: leave it unset unless you run one EPP, or separate prefill and decode EPPs with sequential P/D. Multi-replica EPPs with parallel E/P/D requests need coordination through a shared syncer. See the [screener README](https://github.com/llm-d/llm-d-router/blob/main/pkg/epp/framework/plugins/requestcontrol/screener/disaggregatedsetrollout/README.md#request-lifecycle).

> [!IMPORTANT]
> The screener fails closed. If no revision has a Ready pod of every required role, the EPP returns `503` instead of pairing across revisions or sending traffic to a half-started revision. Keep at least one complete revision Ready throughout a rollout.

The screener stamps the chosen revision on each response in the `x-llm-d-disagg-revision` header, and exports the current traffic share per revision as the `llm_d_epp_disaggregatedset_revision_gating_share` gauge (see [Verify a Rollout](#verify-a-rollout)).

## Step 2: Drain Pods Gracefully

When a pod is deleted, it leaves the `InferencePool` and the EPP stops scheduling to it. Configure the model server so the pod keeps serving its in-flight requests after that:

```yaml
spec:
  # Must exceed the preStop sleep plus --shutdown-timeout.
  terminationGracePeriodSeconds: 150
  initContainers:
  - name: routing-proxy           # decode pods only
    restartPolicy: Always         # native sidecar: stops after the model server exits
  containers:
  - name: modelserver
    args:
    - ...
    - --shutdown-timeout=90       # on SIGTERM, finish in-flight requests
    lifecycle:
      preStop:
        sleep:
          seconds: 20             # the engine stays fully up while the pod leaves the pool
```

Each setting covers a different part of the drain:

1. The `preStop` sleep keeps the engine fully up while requests the EPP had already scheduled reach the pod. For a prefill pod, it also gives decode pods time to pull the KV cache of prefills that just finished. vLLM cannot yet hold a prefill pod open until its blocks are fetched (see [Scaling Down Prefill Replicas](./vllm.md#scaling-down-prefill-replicas)), so this sleep is the mitigation.
2. `--shutdown-timeout` makes vLLM finish in-flight requests on `SIGTERM` instead of aborting them.
3. Because the routing proxy is a native sidecar (an `initContainer` with `restartPolicy: Always`), Kubernetes stops it only after the model server exits, so draining decode requests keep their proxy.

Size the sleep and the timeout to your p99 request duration. [Graceful Shutdown & Draining](../lifecycle/graceful-shutdown.md) explains the termination sequence for aggregated serving.

> [!NOTE]
> With `kv_load_failure_policy: fail`, a decode pod that cannot pull KV returns an error, which makes a broken drain visible while you validate your settings. With `recompute`, the decode pod recomputes the prefill instead, which hides drain gaps from clients at the cost of extra decode load. See [Fault Tolerance](./vllm.md#fault-tolerance).

## Set Up the Rollout Commands

Both strategies use these variables and a helper that waits for a set to settle. The helper first waits for the controller to observe the latest spec, so it does not return on an `Available` condition left over from before the change:

```bash
export NAMESPACE=<your-namespace>
export SET=pd-disagg-vllm   # your DisaggregatedSet; the manual canary uses ${SET}-v0 and ${SET}-v1

# Wait until the controller has seen the latest spec and every role is Ready at its target.
wait_ds() {
  local gen
  gen=$(kubectl get disaggregatedset "$1" -n ${NAMESPACE} -o jsonpath='{.metadata.generation}')
  kubectl wait disaggregatedset/"$1" -n ${NAMESPACE} \
    --for=jsonpath='{.status.observedGeneration}'="${gen}" --timeout=120s
  kubectl wait disaggregatedset/"$1" -n ${NAMESPACE} --for=condition=Available --timeout=1800s
}
```

## Strategy A: Automatic Rolling Update

With the screener in place, you roll out a new version by changing the set's template. The controller plans the steps across roles and keeps each role within its own budget, set per role:

```yaml
roles:
- name: decode
  spec:
    rolloutStrategy:
      rollingUpdateConfiguration:
        maxSurge: 1        # bring one new decode up before an old one drains
        maxUnavailable: 0
```

The default is `maxSurge: 0`, `maxUnavailable: 1`, which needs no spare accelerators but removes capacity during the rollout. The P/D recipe surges the decode role (`maxSurge: 1`, `maxUnavailable: 0`) so a slice never loses its only decode.

1. Change the template (for example the image) in your overlay and apply it:

   ```bash
   kubectl apply -n ${NAMESPACE} -k <your-overlay>
   ```

2. List both revisions' LeaderWorkerSets, then wait for the rollout to finish:

   ```bash
   kubectl get leaderworkerset -n ${NAMESPACE} -l disaggregatedset.x-k8s.io/name=${SET}
   wait_ds ${SET}
   ```

3. To roll back, revert the template and apply it again. This starts a new rollout toward the old template.

A rollout with `maxSurge > 0` waits for spare accelerators. On a fully allocated cluster, see [Rolling Updates Can Stall on Fully Allocated Clusters](./disaggregatedset.md#known-issue-rolling-updates-can-stall-on-fully-allocated-clusters).

## Strategy B: Manual Canary with Two DisaggregatedSets

A single set cannot pause between steps. To drive a rollout step by step, run each version as its own set, stamped from one base with a Kustomize overlay per version. Nothing changes a live set's template, so the controller never starts a rollout of its own; each step is a patch of one set's `replicas`.

```text
base/                 # the DisaggregatedSet, with the shared label on every pod
v0/kustomization.yaml # nameSuffix: -v0, the incumbent's template and admission replicas
v1/kustomization.yaml # nameSuffix: -v1, the candidate's template and admission replicas
```

Both sets must carry the label that the `InferencePool` and the screener's `scope.labelSelector` select on (for the P/D guide, `llm-d.ai/guide: pd-disaggregation`). Their templates differ, so the controller gives them different revisions.

The procedure below takes a 2P+2D fleet from `v0` to `v1`:

| Step | Action | Fleet afterwards |
| --- | --- | --- |
| 1 | Run `v0` | `v0` 2P+2D |
| 2 | Admit `v1` at 1P+1D | `v0` 2P+2D, `v1` 1P+1D |
| 3 | Hold and verify the canary | |
| 4 | Scale `v0` to 1P+1D | `v0` 1P+1D, `v1` 1P+1D |
| 5 | Scale `v1` to 2P+2D | `v0` 1P+1D, `v1` 2P+2D |
| 6 | Retire `v0` | `v1` 2P+2D |

At its peak the fleet runs both versions at once (6 groups in this example), so plan for the candidate's canary shape in spare capacity.

Add a helper that sets both roles of one set to the same number of groups:

```bash
# Set both roles of one set to N groups. The test ops guard the role order.
scale() {
  kubectl patch disaggregatedset "${SET}-$1" -n ${NAMESPACE} --type=json -p "[
    {\"op\": \"test\", \"path\": \"/spec/roles/0/name\", \"value\": \"prefill\"},
    {\"op\": \"test\", \"path\": \"/spec/roles/1/name\", \"value\": \"decode\"},
    {\"op\": \"replace\", \"path\": \"/spec/roles/0/spec/replicas\", \"value\": $2},
    {\"op\": \"replace\", \"path\": \"/spec/roles/1/spec/replicas\", \"value\": $2}]"
  wait_ds "${SET}-$1"
}
```

1. **Run `v0`** with the screener configured as in [Step 1](#step-1-keep-pd-pairs-inside-one-revision), scoped to the shared label:

   ```bash
   kubectl apply -n ${NAMESPACE} -k v0/
   wait_ds ${SET}-v0
   ```

2. **Admit `v1`.** No traffic gate is needed while it loads: the screener offers `v1` only once it has a Ready prefill and a Ready decode pod.

   ```bash
   kubectl apply -n ${NAMESPACE} -k v1/
   wait_ds ${SET}-v1
   ```

3. **Hold and verify.** With 2P+2D against 1P+1D, `max-role` sends about 67% of requests to `v0` and 33% to `v1`. Check errors, latency and output quality for the `v1` share before going on (see [Verify a Rollout](#verify-a-rollout)).

4. **Shrink `v0`.** The controller deletes the highest-index group of each role, and those pods drain as in [Step 2](#step-2-drain-pods-gracefully):

   ```bash
   scale v0 1
   ```

5. **Grow `v1`.** Traffic moves to about 33% `v0` and 67% `v1`:

   ```bash
   scale v1 2
   ```

6. **Retire `v0`.** Scale it to zero, wait for the drain, then delete it:

   ```bash
   scale v0 0
   kubectl delete disaggregatedset ${SET}-v0 -n ${NAMESPACE}
   ```

**To abort at any step**, restore `v0` before removing `v1`, so one complete revision stays Ready throughout:

```bash
scale v0 2
scale v1 0
kubectl delete disaggregatedset ${SET}-v1 -n ${NAMESPACE}
```

> [!WARNING]
> Do not re-apply an overlay in the middle of a rollout. The overlay's `replicas` are only the admission shape; applying it again resets that set to them.

## Verify a Rollout

**Which revision served a request.** The response header carries the revision that the screener chose:

```bash
curl -si http://${ENDPOINT}/v1/completions -H 'Content-Type: application/json' \
  -d '{"model": "'${MODEL}'", "prompt": "The capital of France is", "max_tokens": 8}' \
  | grep -i -e '^HTTP' -e x-llm-d-disagg-revision
```

**Pods per revision and role:**

```bash
kubectl get pods -n ${NAMESPACE} -l llm-d.ai/guide=pd-disaggregation \
  -L disaggregatedset.x-k8s.io/revision,llm-d.ai/role
```

**Traffic share per revision.** `llm_d_epp_disaggregatedset_revision_gating_share` reports each revision's current share from `0` to `1`, and `0` for a revision that is not yet complete. `llm_d_epp_disaggregatedset_strict_revision_no_match_total` counts requests that failed closed. See [Collect Metrics](../observability/metrics.md) to scrape the EPP.

**Prefill/decode pairing.** The response header shows only the decode side. The routing proxy on each decode pod logs the prefill endpoint that the EPP chose for every request at `--zap-log-level=4`. Join those addresses against the pods' revision labels to confirm that no request crossed revisions. Collect these logs before a scale-down, because deleted pods take their logs with them.

**Failures.** A `503` with `screeners eliminated all endpoint candidates` means no revision currently has a Ready pod of every required role.

## Reference Run

The procedure in [Strategy B](#strategy-b-manual-canary-with-two-disaggregatedsets) was validated end to end in the [DisaggregatedSet rollout proof of concept](https://github.com/robertgshaw2-redhat/disagg-set-rollout-poc). It used DeepSeek-V2-Lite with DP=2 per pod on H200, `v0` and `v1` differing in `--max-num-seqs`, and 32 closed-loop clients with requests of about 330 prompt tokens and 400 output tokens:

* **No failed requests.** 16,718 requests completed over the whole run with no errors, and every greedy canary prompt returned the expected answer. Each drain took 32-42 seconds: the 20-second `preStop` sleep, then vLLM exited once its in-flight requests finished.
* **Traffic followed the fleet.** 2P+2D against 1P+1D served 65% / 35%; 1P+1D against 1P+1D served 50% / 50%; and 1P+1D against 2P+2D served 35% / 65%. While `v1` loaded it received no traffic until both of its roles were Ready.
* **No cross-revision pairs.** Of 10,315 prefill-to-decode pairs checked before `v0` was retired, none crossed revisions.
* **The screener is what prevents crossing.** The same EPP without the screener paired 61% of requests across revisions. All of them still returned `200`, because the two versions happened to be KV-compatible: nothing else stops cross-revision pairing, and nothing reports it.

## Troubleshooting

* **Requests fail during a scale-down.** Check that `terminationGracePeriodSeconds` exceeds the `preStop` sleep plus `--shutdown-timeout`, and that the routing proxy is a native sidecar. Raise the `preStop` sleep if decode pods log KV transfer failures right after a prefill pod is deleted.
* **A new revision never receives traffic.** It is missing a Ready pod of one of the `requiredRoles`. Check the pods of that revision, and that the screener's `scope.labelSelector` and the `InferencePool` both select them.
* **KV transfers fail between pods of the same version.** The pods may run different image builds. Pin the image by digest and avoid `imagePullPolicy: IfNotPresent` with moving tags.
* **A crashed pod's logs are gone.** LeaderWorkerSet `restartPolicy: RecreateGroupOnPodRestart` replaces the whole group with new pods. Stream logs to a collector while you roll out.
