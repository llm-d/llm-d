# Query Logs by Trace ID

This guide shows how to collect llm-d component logs into **Loki** so you can find every log line of one request by its trace ID, instead of running `kubectl logs` pod by pod.

> [!NOTE]
> Log lines carry a trace ID only when tracing is enabled on the component that wrote them. Set up tracing first with [Trace Requests](./tracing.md).

Commands in this guide use `${NAMESPACE}` for the namespace where your llm-d workload runs:

```bash
export NAMESPACE=<your-llm-d-namespace>
```

## What Gets Logged

The llm-d Go components (llm-d-router v0.11.0 and later) write one JSON object per line to stdout, using the [OpenTelemetry Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/) field names: `body`, `severity_text`, `service.name`. When a request is traced, its log lines also carry `trace_id` and `span_id`.

| Component | `trace_id` on request log lines | Notes |
| --- | --- | --- |
| **EPP** | Yes | |
| **Routing proxy** (P/D sidecar) | Yes | Its per-request lines are logged at verbosity 4 and 5. The P/D guides run it with `--zap-log-level=1`, so set `--zap-log-level=4` to see them. |
| **vLLM / SGLang** | No | Plain-text logs. They are collected and can be filtered by pod and container, but not by trace ID. |

The `span_id` on a log line is the request's entry span, not the span that was active when the line was written.

## How It Works

```text
 every node, including GPU and TPU nodes
 /var/log/pods/<namespace>_<pod>_<uid>/<container>/*.log
        │  read-only hostPath
        ▼
 otel-logs-agent (DaemonSet) ──OTLP/HTTP──► Loki
 parses the JSON, sets trace_id/span_id        stream labels: namespace, pod, container, service.name
 on the log record                             trace_id, span_id: structured metadata
```

- **Log agent**: an OpenTelemetry Collector DaemonSet ([`otel-collector-logs.yaml`](../../../guides/recipes/observability/logging/otel-collector-logs.yaml)) that tails the pod log files of the namespaces you choose. It keeps each file's read offset and its export queue on the node, so a restart resumes where it stopped, and while Loki is unreachable it stops reading and leaves the unread lines in the files.
- **Loki**: a single-binary Loki on local disk ([`loki.yaml`](../../../guides/recipes/observability/logging/loki.yaml)) that keeps logs for 24 hours. Like the in-memory Jaeger in the tracing recipe, it is for getting started: its logs are lost when the pod is replaced.

> [!IMPORTANT]
> The container runtime writes pod log files as root with no read access for other users, so the agent runs as root (UID 0), unlike Loki and the tracing components. Nothing else is elevated: no privilege escalation, no capabilities, a read-only root filesystem, and `/var/log/pods` mounted read-only. Because the [Pod Security](https://kubernetes.io/docs/concepts/security/pod-security-standards/) baseline and restricted levels forbid `hostPath` volumes, the agent and Loki run in their own namespace, `llm-d-logging`, labeled `pod-security.kubernetes.io/enforce=privileged`.

## Step 1: Deploy Loki and the Log Agent

<!-- tabs:start group=logging -->
<details open>
<summary><b>Kubernetes</b></summary>

Deploy Loki and the log agent, and collect the logs of your llm-d namespace:

```bash
./guides/recipes/observability/install-otel-collector-loki.sh -n ${NAMESPACE}
```

To also collect another guide's namespace, run it again with that namespace. The agent keeps collecting the namespaces it already has.

Verify the components are running:

```bash
kubectl get pods -n llm-d-logging
```

Expected output, with one `otel-logs-agent` pod per node:

```text
NAME                     READY   STATUS    RESTARTS   AGE
loki-xxxxxxxxx-xxxxx     1/1     Running   0          1m
otel-logs-agent-xxxxx    1/1     Running   0          1m
```

If the agent pods do not appear, an admission policy rejected them. The reason is in the DaemonSet events:

```bash
kubectl describe daemonset otel-logs-agent -n llm-d-logging
```

### Use Your Own Loki

To send the logs to an existing Loki instead of deploying one, pass its OTLP endpoint:

```bash
./guides/recipes/observability/install-otel-collector-loki.sh -n ${NAMESPACE} \
  --otlp-endpoint http://<loki-host>:3100/otlp
```

That Loki needs version 3.x with [OTLP ingestion](https://grafana.com/docs/loki/latest/send-data/otel/) and structured metadata enabled (`limits_config.allow_structured_metadata: true`, the default since Loki 3.0, which requires the `tsdb` store with schema `v13`). The trace ID is stored as structured metadata. To get the same stream labels as the bundled Loki, copy the `otlp_config` block from [`loki.yaml`](../../../guides/recipes/observability/logging/loki.yaml). It keeps `k8s.pod.uid` and other high-cardinality fields out of the labels.

The endpoint is the agent's only backend setting, so you can also point it at a gateway OpenTelemetry Collector.

### Manual Deployment

If you prefer to apply the manifests directly:

```bash
kubectl create namespace llm-d-logging
kubectl label namespace llm-d-logging pod-security.kubernetes.io/enforce=privileged
kubectl apply -n llm-d-logging -f guides/recipes/observability/logging/loki.yaml \
  -f guides/recipes/observability/logging/otel-collector-logs.yaml
# The agent collects nothing until it is told which namespaces to read
kubectl set env daemonset/otel-logs-agent -n llm-d-logging LOG_NAMESPACES=${NAMESPACE}
```

`LOG_NAMESPACES` takes a comma-separated list.

</details>
<details>
<summary><b>GKE</b></summary>

GKE already sends container stdout to [Cloud Logging](https://cloud.google.com/kubernetes-engine/docs/concepts/about-logs) and parses JSON lines into `jsonPayload`, so the trace ID is searchable without deploying anything. In the [Logs Explorer](https://docs.cloud.google.com/logging/docs/view/logs-explorer-interface), query:

```text
resource.type="k8s_container"
resource.labels.namespace_name="<your-llm-d-namespace>"
jsonPayload.trace_id="<trace-id>"
```

On GKE Standard clusters you can also use the Kubernetes tab. GKE Autopilot does not allow the `hostPath` volumes the agent needs.

</details>
<details>
<summary><b>OpenShift</b></summary>

The log agent is not supported on OpenShift: reading `/var/log/pods` there needs a privileged SecurityContextConstraints grant. Use [OpenShift Logging](https://docs.redhat.com/en/documentation/red_hat_openshift_logging/) instead. Its `ClusterLogForwarder` already collects pod logs, and can forward them to a LokiStack, or to any Loki 3.x OTLP endpoint if your OpenShift Logging version has the OTLP output.

</details>
<!-- tabs:end -->

## Step 2: Query Logs by Trace ID

Send a few requests through the router and take a trace ID from Jaeger (see [Trace Requests](./tracing.md#step-4-view-traces)). Then forward the Loki port:

```bash
kubectl port-forward -n llm-d-logging svc/loki 3100:3100
```

Query the log lines of that request across every pod in the namespace:

```bash
curl -sG http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode "query={k8s_namespace_name=\"${NAMESPACE}\"} | trace_id=\"<trace-id>\"" \
  | jq -r '.data.result[].values[][1]'
```

To list recent request log lines with their trace IDs instead:

```bash
curl -sG http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode "query={k8s_namespace_name=\"${NAMESPACE}\"} | trace_id!=\"\"" \
  --data-urlencode 'limit=20' \
  | jq -r '.data.result[] | .stream.trace_id + "  " + (.values[][1])'
```

To browse the logs in Grafana, add a Loki data source with the URL `http://loki.llm-d-logging.svc.cluster.local:3100` and run the same queries in **Explore**.

Each stream is labeled with `k8s_namespace_name`, `k8s_pod_name`, `k8s_container_name`, and `service_name`. The `trace_id`, `span_id`, and `k8s_pod_uid` values, and the other JSON fields, are stored as structured metadata, so filter on them with `| field="value"` after the stream selector.

## Limitations

- **Delivery**: while Loki is unreachable the agent stops reading and resumes when it is back. The kubelet rotates each container's log once it reaches 10 MiB and keeps five files by default, so during a long outage the oldest unread lines of a busy container can be rotated away. Lines that a node had not yet sent are lost if the node is deleted.
- **Bundled Loki**: one replica on local disk with 24-hour retention. For production, use your own Loki with object storage.
- **Sampling**: every traced request writes its trace ID to the logs, but with a sampling ratio below 100% most of those traces are not kept in Jaeger.

## Cleanup

```bash
./guides/recipes/observability/install-otel-collector-loki.sh -u
```
