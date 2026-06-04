# Observability integration across the llm-d stack

## Summary

llm-d observability spans multiple repositories: component repos ship scrape configuration and component-specific dashboards; **llm-d** ships shared Prometheus/Grafana install scripts, cross-component Grafana JSON, model-server `PodMonitor` manifests, and website documentation; each repo maintains its own runbooks for import, TLS, and troubleshooting.

This proposal defines a **three-layer model** (component config, shared platform config, procedures), a **catalog** of where assets live today, and **checklists** for component maintainers and well-lit path guide authors so new work does not duplicate dashboard JSON or scatter monitoring steps.

**Scope:** metrics, Prometheus/Grafana, alerts, and tracing only, not general install, architecture, or non-observability guide content.

## Motivation

Without clear ownership, teams copy Grafana JSON into llm-d, embed WVA/router runbooks in guide READMEs, or omit scrape config, breaking link checks, release tagging, and “single source of truth” for component metrics.

Well-lit paths deploy model servers from llm-d recipes (there is no separate “vLLM component repo”), so shared `PodMonitor` labels must match [guides/recipes/modelserver/components/monitoring](https://github.com/llm-d/llm-d/tree/main/guides/recipes/modelserver/components/monitoring). Router and WVA observability remain in **llm-d-router** and **llm-d-workload-variant-autoscaler**; llm-d guides should **orchestrate and link**, not fork those runbooks.

### Goals

* **Predictable ownership:** Every scrape target, dashboard JSON, and observability doc has a defined home (Layer 1, 2, or 3).
* **Guide consistency:** Well-lit path READMEs use the same four-step monitoring section order (prerequisites → stack → scrape → dashboards).
* **Review clarity:** PR reviewers can tell whether assets belong in a component repo, llm-d shared recipes, or website docs.
* **Discoverability:** A maintained catalog table links components to repos, config paths, and Layer 3 documentation.

### Non-Goals

* Implementing new metrics, dashboards, or tracing instrumentation (covered by other proposals and component work).
* Replacing platform monitoring (GKE Managed Prometheus, OpenShift user-workload monitoring, etc.); guides may skip llm-d stack install when the platform already scrapes workloads.
* Log aggregation, SLO enforcement, or alerting policy design.
* Publishing full component runbooks on llm-d.ai (site links out to GitHub).

## Proposal

Adopt three layers for all observability assets:

| Layer | Repo | What |
|-------|------|------|
| **1 - Config** | Each **component** repo | That component’s scrape CRs, dashboard JSON, Helm values, `PrometheusRule` (e.g. router → **llm-d-router**, WVA → **llm-d-workload-variant-autoscaler**) |
| **2 — Shared config** | **llm-d** only | Platform-wide assets reused by many guides: Prometheus/Grafana install, cross-component Grafana JSON, model server `PodMonitor`, shared router monitoring Helm values |
| **3 — Procedures** | **Each repo** | Observability docs: enable scrape, import dashboards, TLS, troubleshooting (“no data”) |

**Rules**

* **Single-component dashboard** → that component’s repo (Layer 1). Do not copy JSON into llm-d.
* **Cross-component dashboard** (model server + EPP views) → llm-d [guides/recipes/observability/grafana/dashboards](https://github.com/llm-d/llm-d/tree/main/guides/recipes/observability/grafana/dashboards) (Layer 2).
* **Layer 3 is per repo.** WVA/router observability runbooks live in WVA and **llm-d-router**; llm-d `guides/` and `docs/resources/observability/` only **orchestrate** and **link** (optional “Enable monitoring” sections).
* **Metric reference is Layer 1.** Each component documents its metric names, types, and labels in its own repo (e.g. router/EPP → [llm-d-router docs/metrics.md](https://github.com/llm-d/llm-d-router/blob/main/docs/metrics.md)), updated in the **same PR** that adds or renames a metric. llm-d architecture and website docs **link** to these catalogs instead of maintaining copies of metric tables (see [llm-d-router#1636 discussion](https://github.com/llm-d/llm-d-router/pull/1636#issuecomment-4696133599)).

```text
Layer 3  llm-d:  guides/*/README.md (monitoring sections) + docs/resources/observability/
         others: WVA monitoring docs, router config/charts/README.md, …
              ↓ links
Layer 2  llm-d:  guides/recipes/observability/  (stack scripts, grafana/, tracing yamls)
              ↓ apply / helm -f
Layer 1  llm-d-router, WVA, llm-d-kv-cache, …  (each component’s own monitoring config)
```

### User Stories

#### Story 1 - Operator following a well-lit path

As an operator following a guide (e.g. optimized baseline), I read a monitoring section that tells me whether I need the llm-d Prometheus/Grafana stack, how to apply `PodMonitor` / Helm monitoring flags, and which Grafana dashboards to import without hunting across repos.

#### Story 2 — Component maintainer shipping WVA or router metrics

As a maintainer of **llm-d-router** or WVA, I add scrape CRs and dashboard JSON in my repo, document import and TLS in my Layer 3 doc, and tag a release when dashboard JSON changes. llm-d guides link to my doc instead of copying it.

#### Story 3 — Contributor adding a cross-stack Grafana dashboard

As a contributor building panels that combine vLLM and EPP metrics, I open a PR against llm-d `guides/recipes/observability/grafana/dashboards/`, not a component repo.

## Design Details

### Where shared config lives (llm-d Layer 2)

| What | Path |
|------|------|
| Install Prometheus + Grafana | [guides/recipes/observability/install-prometheus-grafana.sh](https://github.com/llm-d/llm-d/blob/main/guides/recipes/observability/install-prometheus-grafana.sh) |
| Load shared dashboards | [guides/recipes/observability/load-llm-d-dashboards.sh](https://github.com/llm-d/llm-d/blob/main/guides/recipes/observability/load-llm-d-dashboards.sh) |
| Shared Grafana JSON | [guides/recipes/observability/grafana/dashboards/](https://github.com/llm-d/llm-d/tree/main/guides/recipes/observability/grafana/dashboards) |
| Tracing (OTel + Jaeger) | [guides/recipes/observability/tracing/](https://github.com/llm-d/llm-d/tree/main/guides/recipes/observability/tracing) |
| vLLM PodMonitor | [guides/recipes/modelserver/components/monitoring/](https://github.com/llm-d/llm-d/tree/main/guides/recipes/modelserver/components/monitoring) |
| vLLM P/D PodMonitor | [guides/recipes/modelserver/components/monitoring-pd/](https://github.com/llm-d/llm-d/tree/main/guides/recipes/modelserver/components/monitoring-pd) |
| Router monitoring / tracing values | [guides/recipes/router/features/monitoring.values.yaml](https://github.com/llm-d/llm-d/blob/main/guides/recipes/router/features/monitoring.values.yaml), [tracing.values.yaml](https://github.com/llm-d/llm-d/blob/main/guides/recipes/router/features/tracing.values.yaml) |
| Website docs (Layer 3 on llm-d) | [docs/resources/observability/](../resources/observability/README.md) |

**Model server scrape is Layer 2, not Layer 1:** well-lit paths deploy model servers from llm-d recipes, so shared `PodMonitor` manifests live under modelserver `components/monitoring`, not in a separate inference-engine repo.

### Catalog

Update this table when adding components or moving assets.

| Component | Repo | Scrape / config | Dashboards | Layer 3 (observability docs) |
|-----------|------|-----------------|--------------|------------------------------|
| Model servers (well-lit paths) | llm-d **Layer 2** | `PodMonitor` — paths above | [guides/recipes/observability/grafana/dashboards/](https://github.com/llm-d/llm-d/tree/main/guides/recipes/observability/grafana/dashboards) | [metrics.md](../resources/observability/metrics.md) |
| llm-d Router | [llm-d-router](https://github.com/llm-d/llm-d-router) | [deploy/components/monitoring/](https://github.com/llm-d/llm-d-router/tree/main/deploy/components/monitoring) | **Layer 1:** router/EPP dashboard JSON in **llm-d-router** (see [DEVELOPMENT.md — Grafana](https://github.com/llm-d/llm-d-router/blob/main/DEVELOPMENT.md#grafana-dashboard)); **Layer 2 (optional):** [llm-d cross-stack dashboards](https://github.com/llm-d/llm-d/tree/main/guides/recipes/observability/grafana/dashboards) only when panels combine vLLM + EPP | [config/charts README — monitoring & tracing](https://github.com/llm-d/llm-d-router/tree/main/config/charts#4-monitoring--tracing-configuration); metric catalog: [docs/metrics.md](https://github.com/llm-d/llm-d-router/blob/main/docs/metrics.md) |
| WVA | [llm-d-workload-variant-autoscaler](https://github.com/llm-d/llm-d-workload-variant-autoscaler) | [config/base/monitoring/](https://github.com/llm-d/llm-d-workload-variant-autoscaler/tree/main/config/base/monitoring) | [deploy/grafana/](https://github.com/llm-d/llm-d-workload-variant-autoscaler/tree/main/deploy/grafana) | [docs/developer-guide/monitoring.md](https://github.com/llm-d/llm-d-workload-variant-autoscaler/blob/main/docs/developer-guide/monitoring.md) |
| KV cache | [llm-d-kv-cache](https://github.com/llm-d/llm-d-kv-cache) | deployment monitoring YAML in repo | same folder | deployment monitoring doc in repo |

**Grafana JSON placement:** 
- Panels using **one** component’s metrics only (e.g. only `wva_*`) → that **component repo** (Layer 1). 
- Panels combining **several** stack parts (e.g. vLLM + EPP) → llm-d [guides/recipes/observability/grafana/dashboards/](https://github.com/llm-d/llm-d/tree/main/guides/recipes/observability/grafana/dashboards) (Layer 2).

### Component repo checklist (Layer 1 + 3 in component repo)

Ship in **your** repository:

* `config/.../monitoring/` or `deploy/.../monitoring/` — scrape CRs
* `deploy/grafana/*.json` — component dashboards (optional Helm/ConfigMap install)
* `docs/**/monitoring.md` or `docs/metrics.md` — import steps, metric table (the **single source of truth**; llm-d docs link here), “no data” troubleshooting
* Update the metric catalog in the **same PR** that adds, renames, or removes a metric
* Tag releases when dashboard JSON changes

**Dashboard bar:** versioned JSON, documented import path, datasource convention, optional e2e/smoke that panels exist after kind install.

### llm-d guide checklist (Layer 3 orchestration in guide README)

Use these **four subsections in this order** in the guide README (same flow as [optimized-baseline — Enable monitoring](https://github.com/llm-d/llm-d/blob/main/guides/optimized-baseline/README.md#3-optional-enable-monitoring)):

| Step | Subsection | What the reader does |
|------|------------|----------------------|
| 1 | **Prerequisites** | Decide: need llm-d Prometheus/Grafana or use GKE/OCP monitoring? WVA needs TLS? |
| 2 | **Stack** | Install shared stack ([observability setup](../resources/observability/setup.md)) **or** skip if platform already scrapes metrics |
| 3 | **Scrape** | Apply `PodMonitor` / `ServiceMonitor` (or Helm `monitoring.enabled`) so Prometheus collects workload metrics |
| 4 | **Dashboards** | Load llm-d shared Grafana JSON ([load-llm-d-dashboards.sh](https://github.com/llm-d/llm-d/blob/main/guides/recipes/observability/load-llm-d-dashboards.sh)); **link** to component repos for WVA/router dashboards |

**Order rationale:** decide environment → ensure a metrics backend → configure scrape → open dashboards when data can already flow.

### Conventions

* **Namespaces:**
  - **Workload namespace (per guide):** Model servers, router/EPP, and WVA run where the guide deploys them (e.g. `${NAMESPACE}` from the guide README). This is not the same as the monitoring stack namespace.
  - **Monitoring stack namespace:** [install-prometheus-grafana.sh](https://github.com/llm-d/llm-d/blob/main/guides/recipes/observability/install-prometheus-grafana.sh) **central mode** (default) installs Prometheus, Grafana, and the Prometheus Operator into **`llm-d-monitoring`** (override with `-n` or `MONITORING_NAMESPACE`). That Prometheus is configured to discover `PodMonitor` / `ServiceMonitor` objects **across all namespaces**. **Individual mode** (`-i`) installs the stack into a namespace you specify (often the same as the workload); use when you do not want a cluster-wide central monitoring namespace.
  - **`PodMonitor` / `ServiceMonitor` placement:** Create scrape CRs in the **same namespace as the pods they select** (apply with the guide’s `kubectl apply -n ${NAMESPACE}`). Do not put workload `PodMonitor` objects only in `llm-d-monitoring` unless those pods also run there. Central Prometheus in `llm-d-monitoring` still scrapes monitors that live in workload namespaces.
* **Cross-repo:** prefer links and Helm flags; avoid copying dashboard JSON or per-component metric tables into llm-d.

### Follow-up: llm-d docs sweep

Sweep existing llm-d docs to replace duplicated per-component metric tables with links to the component catalogs (per [llm-d-router#1636](https://github.com/llm-d/llm-d-router/pull/1636#issuecomment-4696434898)). Known instances:

* [docs/architecture/core/router/epp/flow-control.md — Metrics & Observability](https://github.com/llm-d/llm-d/blob/main/docs/architecture/core/router/epp/flow-control.md#metrics--observability) duplicates the flow control metric table → link to [llm-d-router docs/metrics.md](https://github.com/llm-d/llm-d-router/blob/main/docs/metrics.md) once the flow control metrics are documented there (follow-up in llm-d-router).

### Review expectations

| PR type | Expect |
|---------|--------|
| **Component** (WVA, router, …) | Same PR/repo: deployable observability **config** (scrape CRs, dashboard JSON, Helm) **and** **how-to doc** (import, TLS, troubleshooting). New/renamed metrics update the component metric catalog in the same PR. |
| **llm-d guide** | Monitoring section with four steps (prerequisites → stack → scrape → dashboards); links only, no copied runbooks. |
| **llm-d dashboard** | Cross-component only; else redirect to component repo. |
| **llm-d architecture/website doc** | Link to the component metric catalog; do not inline per-component metric tables. |

Discuss changes in [#sig-observability](https://llm-d.slack.com/archives/C09305NHZ45).
