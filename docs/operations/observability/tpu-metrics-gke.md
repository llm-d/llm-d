# GKE TPU Observability

Use this reference to interpret GKE TPU device-plugin metrics and troubleshoot
missing data. Follow the [TPU recipe](../../../guides/recipes/observability/tpu/)
to enable scraping and load the dashboard. The reference targets the exporter
interface below. Check the exporter output before relying on a hardware panel.

## Metric interface

This reference targets the GKE device-plugin `/metrics` interface, not Ray's
`ray_tpu_*` metrics or a separate libtpu exporter. It uses raw gauges without
recording rules, model-specific capacity assumptions, or missing-value defaults.

| Metric | Unit | Interpretation | Availability evidence |
| --- | --- | --- | --- |
| `tensorcore_utilization` | Percent, 0–100 | TensorCore utilization | Published GKE v6e sample |
| `memory_bandwidth_utilization` | Percent, 0–100 | HBM bandwidth utilization, not capacity usage | Published GKE v6e sample |
| `duty_cycle` | Percent, 0–100 | Time actively processing | Ray parser; optional runtime metric |
| `memory_used` | Bytes | Accelerator memory used | Ray parser; optional runtime metric |
| `memory_total` | Bytes | Accelerator memory allocatable | Ray parser; optional runtime metric |

Hardware queries select `make="cloud-tpu"` and use `model`, `tpu_topology`, and
`accelerator_id` labels. The `model` label is the TPU type, not the served LLM;
the dashboard variable is named `tpu_model` to make this distinction explicit.

### Label scope

| Label | Source and use |
| --- | --- |
| `job` | Prometheus scrape identity; defaults to `kube-system/tpu-metrics-exporter` because the PodMonitor does not set `jobLabel`. The dashboard selects one job with an exact match |
| `instance` | Prometheus scrape target; keeps exporters separate even when they report the same accelerator ID |
| `make` | Exporter hardware label; queries require `cloud-tpu`, which excludes other accelerator makes within the selected job |
| `model`, `tpu_topology`, `accelerator_id` | Exporter hardware labels, preserved without renaming or joining them to model-server metrics |
| `namespace`, `pod`, `container` | With the recipe's default `honorLabels: false` behavior, conflicting exporter labels become `exported_namespace`, `exported_pod`, and `exported_container`; the original names identify the scrape target |

The PodMonitor's Kubernetes metadata labels are used for monitor discovery; they
are not automatically copied onto metric samples. Workload attribution must use
the labels actually present on the scraped series. In particular, the scrape
target's `namespace="kube-system"` does not identify the inference workload.
This dashboard does not filter by workload namespace or aggregate across workloads.

The job dropdown includes all scrape jobs so failed exporters remain selectable
even when they emit no hardware metrics. Select the TPU exporter job. Hardware
queries also require `make="cloud-tpu"`; the scrape-health query uses only job and
instance because `up` does not carry the exporter's hardware labels.

### Interface sources

Sources for this interface are [Ray's GKE TPU parser](https://github.com/ray-project/ray/blob/be4889928d3a396d7a1476d1bac3288dac72f9ab/python/ray/dashboard/modules/reporter/reporter_agent.py)
and a [published GKE v6e exporter sample](https://github.com/ray-project/ray/issues/57829).
The sample demonstrates utilization metrics; the parser also handles the runtime
duty-cycle and memory metrics, explicitly noting that some TPU types omit runtime
metrics. These sources do not establish support for every GKE or TPU version.
No new GKE/TPU hardware validation was performed for this reference.

TPU model and topology are filters, not a compatibility matrix. An exporter that
provides the listed interface can use the same queries across TPU models; changing
the vLLM or llm-d image does not establish which hardware metrics the GKE exporter
provides. Validate the interface again after a GKE runtime or exporter upgrade.

The separate `tensorcore_utilization_node` and
`memory_bandwidth_utilization_node` series are not added to the workload-associated
series: doing so could double-count the same hardware. Host and runtime metrics
may also use different chip numbering, so panels display original accelerator
IDs without joining them across metric families.

## Check the exporter

Inspect the actual exporter before interpreting hardware panels:

```bash
kubectl get pods -n kube-system -l k8s-app=tpu-device-plugin -o wide
# Replace the pod name with one from the output above; keep this command running.
kubectl port-forward -n kube-system pod/<exporter-pod> 2112:2112
```

In another terminal:

```bash
curl --fail --silent --show-error http://localhost:2112/metrics \
  | grep -E '^(# (HELP|TYPE) )?(tensorcore_utilization|memory_bandwidth_utilization|duty_cycle|memory_used|memory_total)([ {]|$)'
```

Compare metric names, units, and labels with the table. Use an active TPU workload
when validating workload-associated metrics. In Prometheus, run the following
checks, substituting your actual scrape job:

```promql
up{job="kube-system/tpu-metrics-exporter"}
```

A value of 1 confirms a successful scrape. To see which dashboard metrics are
available on each instance, without applying hardware-label filters:

```promql
count by (__name__, instance) (
  {job="kube-system/tpu-metrics-exporter",__name__=~"tensorcore_utilization|memory_bandwidth_utilization|duty_cycle|memory_used|memory_total"}
)
```

The counts represent exported series, not devices or utilization. A missing
metric has no row. Then inspect a raw series to check its labels and values:

```promql
tensorcore_utilization{job="kube-system/tpu-metrics-exporter"}
```

If this returns data but the dashboard does not, check `make="cloud-tpu"` and reset
the instance, model, and topology filters to **All**. With **All** selected,
missing model/topology labels do not exclude a series.

| Symptom | What to check |
| --- | --- |
| No scrape-health series | Confirm Prometheus discovered the PodMonitor and its namespace; check the selected data source, job, and instance |
| Scrape health is 0 | Inspect the target error, exporter availability, port, and network access |
| Scrape health is 1, but hardware panels are empty | Compare `/metrics` with the interface above; reset model/topology filters to All and check whether the runtime emits metrics for the active workload |
| Only duty-cycle or memory panels are empty | The runtime may omit these metrics; successful scraping does not imply all hardware metrics are available |
| Different names, labels, or units | Adapt a copy of the dashboard after confirming equivalent semantics; changing only a name is insufficient if the unit or scope differs |

Keep missing metrics as **No data**. Do not replace them with zero or infer that
the device is idle. ICI, PCIe, and VPU panels are not included because this
reference does not establish their exporter interface. When reporting results,
include the GKE version, TPU type, exporter image (if available), and a sanitized
metric sample so compatibility can be assessed.
