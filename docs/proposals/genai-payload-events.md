# Capture GenAI Prompts and Completions as OTel Events

## Summary

Extend llm-d's existing OpenTelemetry distributed tracing to optionally capture full
LLM prompts and completions as OTel span events, following the upstream
[open-telemetry/semantic-conventions#2010](https://github.com/open-telemetry/semantic-conventions/issues/2010)
specification. Payloads above a configurable size threshold are offloaded to an object
storage backend (GCS, S3, or local filesystem) with only a reference URI stored on the
span. The feature is disabled by default and requires explicit opt-in, preserving the
existing metadata-only privacy posture for operators who do not need payload visibility.

This proposal extends [distributed-tracing.md](distributed-tracing.md).

## Motivation

The existing tracing proposal deliberately excludes request/response payloads to protect
privacy and keep span records small. However, several production use-cases require payload
visibility that cannot be satisfied by token counts and latency metadata alone:

- **Offline quality evaluation** — eval pipelines need exact prompts and completions to
  run scoring models, compute BLEU/ROUGE, or detect prompt injection.
- **Prompt debugging** — operators correlating unexpected outputs need the verbatim input,
  not a summary.
- **RAG pipeline validation** — confirming that retrieved context was injected correctly
  requires inspecting the assembled prompt.
- **Cost attribution** — understanding *what* drove high token usage requires the content,
  not just the count.

The upstream OTel semantic-conventions community chose an **event-based model** (not span
attributes) specifically to decouple large payload data from the lightweight span record.
Implementing against that spec makes llm-d payload data consumable by any OTel-native
tooling without custom parsing.

### Goals

- Emit `gen_ai.content.prompt` and `gen_ai.content.completion` OTel events on the
  relevant span following the upstream semantic convention.
- Inline small payloads (≤ configurable threshold, default 4 KB) directly on the event
  attribute; offload larger payloads to object storage and record only a reference URI.
- Provide a pluggable storage interface with four built-in backends: `noop` (default),
  `inline`, `gcs`, `s3`, and `filesystem`.
- Expose an opt-in redaction pipeline (configurable regex patterns + optional external
  DLP webhook) that runs before any payload reaches a backend.
- Ensure zero overhead on the hot path when the feature is disabled.
- Surface configuration through the deployment overlay (Kustomize, once the in-flight
  migration of model server deployments away from Helm lands) and through standard
  environment variables.

### Non-Goals

- This proposal does not modify or replace the latency, token-count, or routing
  attributes defined in [distributed-tracing.md](distributed-tracing.md).
- Real-time PII detection or alerting is out of scope; the redaction pipeline is
  best-effort and advisory.
- Streaming token-by-token event emission is out of scope for v1; completions are
  buffered and emitted as a single event after the response is complete.
- Guaranteed delivery of payload events to object storage (best-effort with
  configurable timeout).
- Breaking changes to the existing tracing configuration schema.

## Proposal

Operators who need payload visibility enable `payloadCapture.enabled: true` in their
deployment overlay (or set `LLMD_PAYLOAD_CAPTURE_ENABLED=true`). All other operators
see no change in behaviour or performance.

When enabled, the relevant llm-d component:

1. Serialises the request messages / prompt to JSON.
2. Passes the JSON through the configured redaction pipeline.
3. Compares the byte length against `inlineSizeThresholdBytes` (default 4 096).
4. If within threshold → attaches the JSON as a `gen_ai.prompt` attribute on a
   `gen_ai.content.prompt` OTel event on the active span.
5. If above threshold → uploads to the configured `PayloadStore` backend and attaches
   only the returned URI as `gen_ai.content.storage_uri`.
6. Repeats steps 1–5 for the response completion after the full response is assembled.

Success is measured by: (a) `gen_ai.content.prompt` / `gen_ai.content.completion` events
appearing in Jaeger traces when enabled, (b) object-store objects appearing at the
expected path, (c) no measurable latency increase on sampled requests when disabled.

### User Stories

#### Story 1 — Offline eval engineer

As an eval engineer, I want to retrieve the exact prompt and completion for any sampled
inference request so that I can run my scoring pipeline without maintaining a separate
request-logging sidecar.

#### Story 2 — Platform operator with PII constraints

As a platform operator, I want to enable payload capture with built-in SSN and credit-card
redaction patterns so that my audit team can inspect prompts without exposing raw PII,
and without writing a custom logging proxy.

#### Story 3 — RAG pipeline debugger

As an ML engineer debugging a RAG pipeline, I want to open a Jaeger trace for a
low-quality response and immediately see the assembled prompt (with retrieved context
injected) so that I can confirm whether retrieval or generation was the failure point.

## Design Details

### OTel Event Specification

Per [semconv#2010](https://github.com/open-telemetry/semantic-conventions/issues/2010),
payload events are emitted on the **same span** that represents the LLM call.

**Prompt event**

| Field | Value |
|---|---|
| Event name | `gen_ai.content.prompt` |
| `gen_ai.prompt` | Serialised messages / prompt JSON (omitted when offloaded) |
| `gen_ai.content.storage_uri` | Object store URI (present only when offloaded) |

**Completion event**

| Field | Value |
|---|---|
| Event name | `gen_ai.content.completion` |
| `gen_ai.completion` | Serialised choices JSON (omitted when offloaded) |
| `gen_ai.content.storage_uri` | Object store URI (present only when offloaded) |

When the payload is truncated due to `maxCompletionBufferBytes` being exceeded, the event
additionally carries `gen_ai.content.truncated: true`.

### Capture Layers

Payload events are attached to spans at the following layers, with vLLM preferred when
available:

| Layer | Prompt capture | Completion capture | Span attached to |
|---|---|---|---|
| **vLLM** (preferred) | Decoded Python request object | `RequestOutput` choices | `vllm:llm_request` |
| **Gateway (EPP)** | Raw HTTP request body (JSON parse) | Not available | `gateway.request` |
| **P/D proxy sidecar** | Not available | Reassembled SSE stream | `llm_d.pd_proxy.request` |

To avoid double-emission when vLLM tracing is active, the gateway and sidecar skip
prompt/completion events when `payloadCapture.emitIfUpstreamTraced` is `false` (default).

### PayloadStore Interface

```go
// PayloadStore persists a payload and returns a retrieval URI.
// Implementations: NoopStore, InlineStore, GCSStore, S3Store, FilesystemStore.
type PayloadStore interface {
    Store(ctx context.Context, traceID, spanID string, kind PayloadKind, data []byte) (uri string, err error)
}

type PayloadKind string
const (
    KindPrompt     PayloadKind = "prompt"
    KindCompletion PayloadKind = "completion"
)
```

Backend selection via `payloadCapture.backend`:

| Value | Behaviour |
|---|---|
| `noop` (default) | Drop silently; no event emitted |
| `inline` | Attach to OTel event attribute; auto-offload if above threshold |
| `gcs` | Upload to GCS, emit `gs://bucket/path` URI |
| `s3` | Upload to S3-compatible store, emit `s3://bucket/path` URI |
| `filesystem` | Write to mounted volume, emit `file:///path` URI |

Object store path convention:
```
{prefix}/{YYYY}/{MM}/{DD}/{traceID}/{spanID}-{kind}.json
```

### Redaction Pipeline

Runs before any backend sees the payload:

```
raw JSON → regex replacements → [optional webhook POST] → PayloadStore
```

Built-in patterns (active when `redaction.builtinPatterns: true`): US SSN, credit card
numbers (Luhn-format), Bearer tokens in Authorization values. Custom patterns are a list
of `{pattern, replacement}` objects. The webhook client enforces a configurable timeout;
on error the payload is truncated rather than forwarded unredacted.

### Streaming Completion Buffering

SSE responses are buffered in memory up to `maxCompletionBufferBytes` (default 256 KB)
before the completion event is emitted. If the buffer limit is reached, the event is
emitted with the buffered content and `gen_ai.content.truncated: true`; the response
stream continues unaffected.

### Configuration

The configuration schema below is the canonical shape; the concrete operator surface
(currently Helm `values.yaml`, transitioning to Kustomize patches as the model server
deployments are migrated) will adopt the same field names and defaults. The example
shows the GAIE / EPP component:

```yaml
inferenceExtension:
  payloadCapture:
    enabled: false                      # master switch; opt-in
    backend: noop                       # noop | inline | gcs | s3 | filesystem
    inlineSizeThresholdBytes: 4096
    offloadBackend: ""                  # fallback when inline exceeds threshold
    emitIfUpstreamTraced: false         # skip if vLLM tracing is active
    maxCompletionBufferBytes: 262144
    gcs:
      bucket: ""
      prefix: "llm-d/payloads"
    s3:
      bucket: ""
      endpoint: ""
      prefix: "llm-d/payloads"
      region: "us-east-1"
    filesystem:
      mountPath: "/var/llm-d/payloads"
    redaction:
      enabled: false
      builtinPatterns: true
      patterns: []
      webhookURL: ""
```

Equivalent environment variables: `LLMD_PAYLOAD_CAPTURE_ENABLED`, `LLMD_PAYLOAD_BACKEND`,
`LLMD_PAYLOAD_INLINE_THRESHOLD`, `LLMD_PAYLOAD_GCS_BUCKET`, `LLMD_PAYLOAD_S3_BUCKET`,
`LLMD_PAYLOAD_FS_PATH`, `LLMD_PAYLOAD_REDACTION_ENABLED`.

### OTel Collector Pipeline

A new `traces/payloads` pipeline filters for `gen_ai.content.*` events and routes them
to a dedicated exporter (filesystem or OTLP bridge for GCS/S3). The existing
`traces` pipeline is unchanged. The redaction processor can additionally be placed in
the collector as a defence-in-depth layer.

### Security

Payload capture significantly raises the sensitivity of trace data. Operators must:

1. Enable TLS on all OTLP gRPC endpoints.
2. Restrict OTel collector access to the tracing namespace.
3. Apply object-store bucket policies limiting read access to authorised principals.
4. Treat captured payloads with the same classification as the underlying model's
   training data or any user PII present in requests.
5. Enable `redaction.enabled: true` when serving untrusted user inputs.

Credentials for GCS/S3 are never embedded in deployment values — they must come from
Workload Identity / IRSA bindings or Kubernetes Secrets mounted as environment variables.

### Phased Implementation

**Phase 1 — Interface and noop/inline backends (this proposal)**
- `PayloadStore` interface in `pkg/telemetry/payload_store.go` (Go)
- `NoopStore` and `InlineStore` implementations
- Wire into gateway `server.go` `Process()` method behind the `enabled` flag
- Operator-surface schema (Kustomize patch / overlay aligned with the in-flight
  migration; equivalent env-var aliases for components that retain Helm short-term)
  and documentation updates
- Unit tests for interface and inline/noop backends

**Phase 2 — Object store backends**
- `GCSStore` (Workload Identity), `S3Store` (IRSA), `FilesystemStore`
- Integration tests using fake-gcs-server / MinIO

**Phase 3 — Redaction pipeline**
- Regex replacement engine with built-in patterns
- Webhook client with timeout and truncate-on-error fallback

**Phase 4 — vLLM native integration**
- Python `PayloadExporter` class mirroring Go interface
- Hook into vLLM `AsyncLLMEngine` output processor
- OTel event emission on `vllm:llm_request` span

## Alternatives

### Log-based payload capture (structured logs → Loki)

Rejected. Requires correlating log lines with trace IDs out of band; no standard
consumer understands the payload-to-span relationship the way OTel event consumers do.
Adds a second observability pipeline with separate retention and access controls.

### Span attributes instead of events

Rejected. Large string attributes bloat the span record, degrade backend indexing
performance, and are not aligned with the upstream semantic convention, which
specifically chose the event model to keep span records lightweight.

### Envoy / Istio sidecar capture

Rejected. A service-mesh proxy captures raw TCP bytes without JSON context, making
structured redaction and typed event emission significantly harder. It also captures
all traffic regardless of sampling decisions, generating unbounded storage load.

### Separate logging sidecar per pod

Rejected. Duplicates the sampling and context-propagation work already done by the
OTel instrumentation; requires operators to run and maintain an additional process per
inference pod and correlate two separate data streams.

---

**Contributors and Reviewers:**

* Nick Aggarwal <nick.aggarwal@gmail.com>

Reviewers:
* JeffLuoo <jeffluoo@google.com>
* sallyom <somalley@redhat.com>
* damemi <mike@odigos.io>
* PierDipi <pdipilat@redhat.com>
* ploffay <ploffay@odigos.io>
