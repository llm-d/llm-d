# Observability integration across the llm-d stack

## Summary

llm-d observability — the Prometheus/Grafana stack, scrape manifests, `PrometheusRule` alerts, Grafana dashboards, and the docs that tell users how to enable all of it — should have **one user-facing home: `llm-d/llm-d`**. Component repos (llm-d-router, llm-d-workload-variant-autoscaler, llm-d-kv-cache, …) keep the metric **definitions** their code emits, but they are not where users go to deploy or read observability. This proposal defines that single-home model, a **catalog** of where each asset lives, and **checklists** so new work lands in the right place instead of being scattered across repos.

**Scope:** metrics, Prometheus/Grafana, alerts, dashboards, and tracing only — not general install, architecture, or non-observability guide content.

## Motivation

Observability is currently spread across repos: component repos ship `deploy/` monitoring manifests, dashboard JSON, and `PrometheusRule` alerts, while llm-d ships the stack scripts and a subset of dashboards. That split causes three concrete problems, raised by maintainers on [llm-d#1685](https://github.com/llm-d/llm-d/pull/1685#discussion_r3409243282) and [llm-d-router#1668](https://github.com/llm-d/llm-d-router/pull/1668#issuecomment-4735527648):

1. **Only `llm-d/llm-d` syncs to llm-d.ai.** Observability docs and metric references that live in component repos never render on the website. To document the stack on llm-d.ai, the docs have to live in `llm-d/llm-d`.
2. **Users only clone `llm-d/llm-d` to apply guides.** Asking them to also clone component repos to apply scrape manifests, alerts, or dashboards is a friction point. A component repo's `deploy/` folder is for that repo's own dev/CI — it is **not** a set of manifests we point users to.
3. **Observability is horizontal.** Cross-cutting dashboards and a coherent "enable monitoring" experience are best assembled in one central place, not stitched together from many repos.

The well-lit paths already deploy model servers from llm-d recipes (there is no separate "vLLM component repo"), and their `PodMonitor` manifests already live in `llm-d/llm-d`. This proposal generalizes that same rule to router, WVA, and the rest of the stack.

### Goals

* **Single user-facing home:** Every manifest a user applies, every dashboard a user imports, and every doc a user reads lives in `llm-d/llm-d`.
* **Website coverage:** Observability docs and metric references render on llm-d.ai because they live in `llm-d/llm-d`.
* **One clone:** Following any observability guide requires cloning only `llm-d/llm-d`.
* **Clear source of truth for metrics:** A metric's name/type/labels are defined by the component that emits it; the user-facing reference in `llm-d/llm-d` is kept in sync in the same PR.
* **Review clarity:** Reviewers can tell at a glance whether an asset belongs in `llm-d/llm-d` (user-facing) or a component repo (source/definition only).

### Non-Goals

* Implementing new metrics, dashboards, or tracing instrumentation (covered by other proposals and component work).
* Replacing platform monitoring (GKE Managed Prometheus, OpenShift user-workload monitoring, etc.); guides may skip llm-d stack install when the platform already scrapes workloads.
* Log aggregation, SLO enforcement, or alerting policy design.
* Removing component repos' ability to emit metrics or to keep dev/CI monitoring manifests for their own testing.

## Proposal

There are two homes, split by audience — **user-facing vs. source/definition** — not by component.

| Home | Repo | What lives here |
|------|------|-----------------|
| **User-facing** | **`llm-d/llm-d`** | Everything a user deploys, follows, or reads: Prometheus/Grafana install, **all** scrape manifests (`PodMonitor`/`ServiceMonitor`), **all** `PrometheusRule` alerts, **all** Grafana dashboards (single- and cross-component), tracing config, the metric reference, and every "enable monitoring" guide/runbook. |
| **Source / definition** | Each **component** repo | The metric names/types/labels the component's code emits (e.g. `docs/metrics.md`), plus monitoring manifests used only for that repo's own dev/CI. Not a user deployment surface. |

**Rules**

* **If a user deploys it or follows it, it lives in `llm-d/llm-d`.** Component `deploy/` folders are dev/CI scaffolding, not manifests we point users to.
* **All Grafana dashboards live in `llm-d/llm-d`** under [guides/recipes/observability/grafana/dashboards](../guides/recipes/observability/grafana/dashboards) — single-component and cross-component alike. A component repo may keep a working copy for its own development, but the llm-d copy is the one users import and the one rendered on llm-d.ai.
* **All deployable monitoring manifests live in `llm-d/llm-d`** — `PodMonitor`/`ServiceMonitor` and `PrometheusRule` alerts that users apply ship from llm-d recipes, regardless of which component they target.
* **Observability docs and runbooks live in `llm-d/llm-d`** — enable scrape, import dashboards, TLS, "no data" troubleshooting — under `guides/` and `docs/operations/observability/`, so they render on llm-d.ai.
* **Metric definitions stay with the code, the reference renders in llm-d.** Each component defines the metrics it emits (e.g. router/EPP → [llm-d-router docs/metrics.md](https://github.com/llm-d/llm-d-router/blob/main/docs/metrics.md)), updated in the **same PR** that adds or renames a metric. `llm-d/llm-d` carries the user-facing metric reference (so it shows on llm-d.ai) and is kept in sync with the component definition (see [llm-d-router#1636 discussion](https://github.com/llm-d/llm-d-router/pull/1636#issuecomment-4696133599)).

```text
llm-d/llm-d (user-facing home)
  guides/*/README.md ............. "enable monitoring" sections (the one place users follow)
  guides/recipes/observability/ .. stack scripts, grafana/ dashboards, tracing yamls
  guides/recipes/.../monitoring/ . PodMonitor / ServiceMonitor / PrometheusRule users apply
  docs/operations/observability/ .. metric reference + runbooks rendered on llm-d.ai
        ↑ defines / syncs metric names, dashboards
llm-d-router, WVA, llm-d-kv-cache, … (source / definition only)
  code emits metrics; docs/metrics.md is the definitional source; dev/CI manifests stay local
```

### User Stories

#### Story 1 — Operator following a well-lit path

As an operator following a guide (e.g. optimized baseline), I clone only `llm-d/llm-d`, read one monitoring section, and apply the stack, scrape manifests, alerts, and dashboards from that single repo without hunting across other repos.

#### Story 2 — Component maintainer adding a router or WVA metric

As a maintainer of **llm-d-router** or WVA, I define the metric my code emits in my repo's `docs/metrics.md` (the source of truth), and in the same change I update the user-facing scrape/alert/dashboard/reference assets in `llm-d/llm-d` so operators and llm-d.ai stay in sync.

#### Story 3 — Contributor adding any Grafana dashboard

As a contributor building Grafana panels — for one component or several — I open a PR against `llm-d/llm-d` `guides/recipes/observability/grafana/dashboards/`, never a component repo.

## Design Details

### Where observability lives in `llm-d/llm-d`

| What | Path |
|------|------|
| Install Prometheus + Grafana | [guides/recipes/observability/install-prometheus-grafana.sh](../guides/recipes/observability/install-prometheus-grafana.sh) |
| Load dashboards | [guides/recipes/observability/load-llm-d-dashboards.sh](../guides/recipes/observability/load-llm-d-dashboards.sh) |
| Grafana JSON (all components) | [guides/recipes/observability/grafana/dashboards/](../guides/recipes/observability/grafana/dashboards) |
| Tracing (OTel + Jaeger) | [guides/recipes/observability/tracing/](../guides/recipes/observability/tracing) |
| Model server `PodMonitor` | [guides/recipes/modelserver/components/monitoring/](../guides/recipes/modelserver/components/monitoring), [monitoring-pd/](../guides/recipes/modelserver/components/monitoring-pd) |
| Router/EPP scrape, alerts, Helm values | [guides/recipes/router/features/monitoring.values.yaml](../guides/recipes/router/features/monitoring.values.yaml), [tracing.values.yaml](../guides/recipes/router/features/tracing.values.yaml) + scrape/`PrometheusRule` recipes |
| Metric reference + runbooks (llm-d.ai) | [docs/operations/observability/](../docs/operations/observability/README.md) |

**Why model server scrape sets the precedent:** well-lit paths deploy model servers from llm-d recipes, so their `PodMonitor` manifests already live in `llm-d/llm-d` rather than in a separate engine repo. Router, WVA, and KV-cache user-facing manifests follow the same rule.

### Catalog

Update this table when adding components or moving assets. "User-facing assets" always point at `llm-d/llm-d`; "metric source" points at the component repo that defines them.

| Component | Metric source (definition) | User-facing assets (deploy / dashboards / docs) — all in `llm-d/llm-d` |
|-----------|----------------------------|------------------------------------------------------------------------|
| Model servers (well-lit paths) | vLLM/SGLang upstream | `PodMonitor` under modelserver `components/monitoring`; dashboards under observability `grafana/dashboards/`; docs under [docs/operations/observability/](../docs/operations/observability/README.md) |
| llm-d Router / EPP | [llm-d-router docs/metrics.md](https://github.com/llm-d/llm-d-router/blob/main/docs/metrics.md) | scrape, `PrometheusRule` alerts, dashboards, and runbook in `llm-d/llm-d` recipes/docs |
| WVA | [llm-d-workload-variant-autoscaler docs](https://github.com/llm-d/llm-d-workload-variant-autoscaler/blob/main/docs/developer-guide/monitoring.md) | scrape, dashboards, and enable-monitoring docs in `llm-d/llm-d` |
| KV cache | metric definitions in llm-d-kv-cache | scrape, dashboards, and docs in `llm-d/llm-d` |

### Component repo checklist (definition only)

Keep in **your** repository:

* `docs/metrics.md` — the metric names, types, and labels your code emits; the **definitional source of truth**, updated in the **same PR** that adds, renames, or removes a metric.
* Dev/CI monitoring manifests used only for your repo's own testing (clearly not a user deployment surface).

Do **not** treat your `deploy/` folder as the place users install monitoring from, and do not be the home for user-facing dashboards or runbooks.

### llm-d checklist (everything user-facing)

When a component's metrics, alerts, or dashboards change, land the user-facing side in `llm-d/llm-d`:

* Scrape manifests (`PodMonitor`/`ServiceMonitor`) and `PrometheusRule` alerts under the relevant recipe.
* Dashboard JSON under [guides/recipes/observability/grafana/dashboards/](../guides/recipes/observability/grafana/dashboards) — versioned, with a documented import path and datasource convention.
* The metric reference and runbook under [docs/operations/observability/](../docs/operations/observability/README.md), kept in sync with the component's `docs/metrics.md`.
* The guide's "enable monitoring" section (below).

### llm-d guide checklist (monitoring section in the guide README)

Use these **four subsections in this order** (same flow as [optimized-baseline — Enable monitoring](../guides/optimized-baseline/README.md#3-optional-enable-monitoring)):

| Step | Subsection | What the reader does |
|------|------------|----------------------|
| 1 | **Prerequisites** | Decide: need llm-d Prometheus/Grafana or use GKE/OCP monitoring? WVA needs TLS? |
| 2 | **Stack** | Install shared stack ([observability setup](../docs/operations/observability/setup.md)) **or** skip if platform already scrapes metrics |
| 3 | **Scrape** | Apply `PodMonitor` / `ServiceMonitor` (or Helm `monitoring.enabled`) from llm-d recipes so Prometheus collects workload metrics |
| 4 | **Dashboards** | Load llm-d Grafana JSON ([load-llm-d-dashboards.sh](../guides/recipes/observability/load-llm-d-dashboards.sh)) — all dashboards ship from llm-d |

**Order rationale:** decide environment → ensure a metrics backend → configure scrape → open dashboards when data can already flow.

### Conventions

* **Namespaces:**
  - **Workload namespace (per guide):** Model servers, router/EPP, and WVA run where the guide deploys them (e.g. `${NAMESPACE}` from the guide README). This is not the same as the monitoring stack namespace.
  - **Monitoring stack namespace:** [install-prometheus-grafana.sh](../guides/recipes/observability/install-prometheus-grafana.sh) **central mode** (default) installs Prometheus, Grafana, and the Prometheus Operator into **`llm-d-monitoring`** (override with `-n` or `MONITORING_NAMESPACE`). That Prometheus discovers `PodMonitor` / `ServiceMonitor` objects **across all namespaces**. **Individual mode** (`-i`) installs the stack into a namespace you specify; use when you do not want a cluster-wide central monitoring namespace.
  - **`PodMonitor` / `ServiceMonitor` placement:** Create scrape CRs in the **same namespace as the pods they select** (apply with the guide's `kubectl apply -n ${NAMESPACE}`). Central Prometheus in `llm-d-monitoring` still scrapes monitors that live in workload namespaces.
* **Source vs. user-facing:** component repos define metrics; `llm-d/llm-d` carries the deployable manifests, dashboards, and rendered docs. Keep the metric reference in `llm-d/llm-d` in sync with the component's `docs/metrics.md` rather than letting them diverge.

### Follow-up

* **Move component user-facing assets into `llm-d/llm-d`.** Migrate user-deployable scrape manifests, `PrometheusRule` alerts, and dashboards currently in component `deploy/` folders (e.g. [llm-d-router#1668](https://github.com/llm-d/llm-d-router/pull/1668)) into llm-d recipes; leave only definitional/dev-CI artifacts behind.
* **Render metric references on llm-d.ai.** Ensure each component's metric reference is present under `docs/operations/observability/` and kept in sync with the component's `docs/metrics.md` (per [llm-d-router#1636](https://github.com/llm-d/llm-d-router/pull/1636#issuecomment-4696434898)).
* **Optional automation (non-blocking):** a CI sync job that pulls released `docs/metrics.md` / dashboards from component repos into `llm-d/llm-d` so the reference stays current without manual copying.

### Review expectations

| PR type | Expect |
|---------|--------|
| **Component** (WVA, router, …) | Defines the metrics the code emits in `docs/metrics.md` in the same PR; user-facing scrape/alerts/dashboards/docs go to `llm-d/llm-d`, not the component `deploy/` folder. |
| **llm-d guide** | Monitoring section with four steps (prerequisites → stack → scrape → dashboards). |
| **llm-d dashboard** | Any component dashboard (single- or cross-component) lives here. |
| **llm-d manifest/doc** | Deployable scrape/alert manifests and the rendered metric reference live here, kept in sync with the component definition. |

Discuss changes in [#sig-observability](https://llm-d.slack.com/archives/C09305NHZ45).
