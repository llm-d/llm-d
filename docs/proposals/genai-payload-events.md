# Capture GenAI Prompts and Completions as OTel Events

## Summary

Extend llm-d's existing OpenTelemetry distributed tracing to optionally capture full
LLM prompts and completions as OTel span events, following the upstream
[open-telemetry/semantic-conventions#2010](https://github.com/open-telemetry/semantic-conventions/issues/2010)
specification. Payloads above a configurable size threshold are offloaded to an object
storage backend (GCS, S3, or local filesystem) with only a reference URI stored on the
span. Non-text content parts (images, audio, and other binary media in multimodal
requests) are always offloaded — they cannot be carried as OTel attributes — and are
referenced from the span by URI regardless of the inline threshold. The feature is
disabled by default and requires explicit opt-in, preserving the existing metadata-only
privacy posture for operators who do not need payload visibility.

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
- **Multimodal request inspection** — image, audio, and other non-text parts in
  multimodal requests are increasingly common drivers of latency and quality issues,
  and cannot be reconstructed from token metadata alone.

The upstream OTel semantic-conventions community chose an **event-based model** (not span
attributes) specifically to decouple large payload data from the lightweight span record.
Even so, event attributes are typed as strings/numbers/bools/arrays and cannot carry
opaque bytes; non-text payloads must therefore be offloaded to object storage and
referenced by URI, never inlined in a span or a log record. Implementing against the
upstream spec makes llm-d payload data consumable by any OTel-native tooling without
custom parsing.

### Goals

- Emit `gen_ai.content.prompt` and `gen_ai.content.completion` OTel events on the
  relevant span following the upstream semantic convention.
- Inline small text payloads (≤ configurable threshold, default 4 KB) directly on the
  event attribute; offload larger text payloads to object storage and record only a
  reference URI.
- Always offload non-text content parts (image / audio / other binary media in
  multimodal requests) to object storage regardless of the inline threshold, and expose
  the resulting URIs via a dedicated event attribute so consumers can locate media
  without parsing the payload skeleton.
- Provide a pluggable storage interface with five built-in backends: `noop` (default),
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

Per [semconv#2010](https://github.com/open-telemetry/semantic-conventions/issues/2010)
(now closed; GenAI conventions have moved to the dedicated
[semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai)
repository), payload events are emitted on the **same span** that represents the LLM
call. Attribute names prefixed `gen_ai.content.*` that are **not yet defined** in the
upstream GenAI semantic conventions are marked below as *(llm-d extension)*; we will
track upstream as the GenAI conventions stabilise and rename these attributes to match
once standard names exist. This includes `gen_ai.content.media_uris`,
`gen_ai.content.storage_uri`, and `gen_ai.content.truncated`. The base event names
(`gen_ai.content.prompt`, `gen_ai.content.completion`) and the inlined-text payload
attributes (`gen_ai.prompt`, `gen_ai.completion`) follow the upstream spec.

**Prompt event**

| Field | Value |
|---|---|
| Event name | `gen_ai.content.prompt` |
| `gen_ai.prompt` | Serialised messages / prompt JSON, text-only skeleton with non-text parts replaced by `$ref` markers (omitted when the whole payload is offloaded) |
| `gen_ai.content.storage_uri` *(llm-d extension)* | Object store URI for the full text payload (present only when offloaded) |
| `gen_ai.content.media_uris` *(llm-d extension)* | Array of object store URIs for non-text content parts (present when the request contains any non-text media) |

**Completion event**

| Field | Value |
|---|---|
| Event name | `gen_ai.content.completion` |
| `gen_ai.completion` | Serialised choices JSON, text-only skeleton with non-text parts replaced by `$ref` markers (omitted when the whole payload is offloaded) |
| `gen_ai.content.storage_uri` *(llm-d extension)* | Object store URI for the full text payload (present only when offloaded) |
| `gen_ai.content.media_uris` *(llm-d extension)* | Array of object store URIs for non-text content parts (present when the completion contains any non-text media) |

When the payload is truncated due to `maxCompletionBufferBytes` being exceeded, or when
a non-text part is dropped because the configured offload target cannot return a
resolvable URI (see [Non-Text and Multimodal Payloads](#non-text-and-multimodal-payloads)
for the cases that produce this), the event additionally carries
`gen_ai.content.truncated: true`. `backend: noop` is distinct: it is the
no-event-at-all kill switch and emits no `gen_ai.content.*` events of any kind.

### Non-Text and Multimodal Payloads

Modern chat APIs accept multimodal content parts within a single request — OpenAI
Chat Completions `image_url` and `input_audio` parts, vLLM `multi_modal_data`, and
similar shapes from other engines. The OTel attribute type system permits strings,
numbers, bools, and arrays of those — it does **not** permit opaque bytes, and span
backends do not tolerate multi-megabyte attribute strings. Log records are unsuitable
for the same reason and lack a standard span-to-payload linking convention. Non-text
parts must therefore live in object storage and be referenced by URI; this section
specifies how.

The capture pipeline handles non-text content as follows:

1. The serialiser walks the request / response structure and identifies content parts
   whose `type` is not `text` (or, in engine-native shapes, parts that carry binary
   media regardless of declared type).
2. Each non-text part is **always offloaded** to the configured `PayloadStore`,
   regardless of `inlineSizeThresholdBytes`. The inline threshold continues to apply
   only to the text portion.
3. In the serialised payload that the span event carries, every non-text part is
   replaced by a `$ref` marker of the form
   `{"$ref": "<storage_uri>", "media_type": "image/png", "size_bytes": 12345}`.
   The resulting text-only skeleton is then subject to the normal inline-vs-offload
   decision against `inlineSizeThresholdBytes`.
4. The event additionally carries `gen_ai.content.media_uris`: an ordered array of the
   storage URIs produced for that request or completion. Consumers that only need to
   locate media can read this attribute without re-parsing the skeleton.
5. URL-reference parts (e.g. `image_url` pointing to a public CDN) are recorded in
   `gen_ai.content.media_uris` as-is and are **not** re-fetched by llm-d; the redaction
   pipeline still runs against the URL string itself.
6. `backend: noop` short-circuits the entire payload pipeline: no
   `gen_ai.content.*` events are emitted at all, and steps 1–5 above are skipped.
   Operators who want capture *disabled* should leave `payloadCapture.enabled: false`;
   `backend: noop` exists as a secondary kill switch for the case where `enabled` must
   stay `true` for other reasons (e.g. shared overlay) but this particular component
   should produce nothing.
7. When the primary `backend` is `inline` (which cannot store binary), the non-text
   part is routed to the backend named in `offloadBackend`. If `offloadBackend` is
   empty or unset, the part is dropped with `gen_ai.content.truncated: true` on the
   emitted event, the text skeleton is still emitted so text-based debugging continues
   to work, and the request itself is not failed. Operators selecting `backend: inline`
   while expecting multimodal capture must therefore configure a non-empty
   `offloadBackend` (one of `gcs`, `s3`, or `filesystem`); startup configuration
   validation logs a warning when this combination would silently drop media.

The object-store path convention is extended to encode part index and media type so
that text and non-text payloads for the same span do not collide:

```
{prefix}/{YYYY}/{MM}/{DD}/{traceID}/{spanID}-{kind}.json                 # full text payload
{prefix}/{YYYY}/{MM}/{DD}/{traceID}/{spanID}-{kind}-{partIndex}.{ext}    # non-text part
```

`{ext}` is derived from the part's declared media type (`png`, `jpg`, `webp`, `wav`,
`mp3`, `pcm`, …) with `bin` as the fallback when the type is unknown. The redaction
pipeline runs against the text skeleton only; binary parts are stored as received,
and operators relying on redaction for compliance should either disable multimodal
capture or apply DLP at the object-store layer.

**Per-part size cap (`maxNonTextPartBytes`).** Defaults to 8 MiB (`8388608`). The
value is chosen to sit comfortably above the per-image caps of common hosted APIs
that llm-d typically fronts (Anthropic Claude vision: 5 MiB inline; OpenAI and Google
Gemini: 20 MiB inline) while still bounding worst-case object-store write amplification
for high-volume inference traffic. The cap is per part, not per request, so a multi-image
request with several 4 MiB images is fully captured under the default. Operators
should tune this for their workload: lower it (e.g. 2 MiB) when the dominant media
type is photos at moderate resolution and storage cost matters, or raise it (up to
~25 MiB) when serving audio clips or large image inputs through Gemini/OpenAI
upstreams. Parts exceeding the cap are dropped with `gen_ai.content.truncated: true`
rather than truncated mid-stream, since binary truncation produces undecodable artifacts.

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
    Store(ctx context.Context, ref PayloadRef, data []byte) (uri string, err error)
}

// PayloadRef identifies one payload part. PartIndex is zero for the text payload
// itself and >= 1 for each non-text content part associated with the same span.
// MediaType is an IANA media type (e.g. "application/json", "image/png") and
// drives both the storage-path extension and Content-Type on uploads.
type PayloadRef struct {
    TraceID   string
    SpanID    string
    Kind      PayloadKind
    PartIndex int
    MediaType string
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
| `noop` (default) | Drop silently. No `gen_ai.content.*` events of any kind are emitted on the span — neither for text nor for non-text parts, and `gen_ai.content.truncated` is never set. This is the secondary kill switch and short-circuits the entire capture pipeline (see step 6 of [Non-Text and Multimodal Payloads](#non-text-and-multimodal-payloads)) |
| `inline` | Attach to OTel event attribute; auto-offload if above threshold |
| `gcs` | Upload to GCS, emit `gs://bucket/path` URI |
| `s3` | Upload to S3-compatible store, emit `s3://bucket/path` URI |
| `filesystem` | Write to mounted volume, emit `file:///path` URI |

Object store path convention (text payloads use `.json`; non-text parts append a
part index and a media-type-derived extension — see
[Non-Text and Multimodal Payloads](#non-text-and-multimodal-payloads)):
```
{prefix}/{YYYY}/{MM}/{DD}/{traceID}/{spanID}-{kind}.json
{prefix}/{YYYY}/{MM}/{DD}/{traceID}/{spanID}-{kind}-{partIndex}.{ext}
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
    captureNonTextParts: true           # set false to drop non-text media instead of
                                        # storing it (text skeleton + truncated:true)
    maxNonTextPartBytes: 8388608        # 8 MiB per-part cap; tune per deployment —
                                        # see "Non-Text and Multimodal Payloads" for
                                        # the rationale behind the default
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
6. Recognise that the redaction pipeline operates only on the text skeleton — non-text
   parts (images, audio) are written to the configured store as received. For
   workloads where multimodal content can carry sensitive data, either disable
   non-text capture (`captureNonTextParts: false`) or apply a DLP pass at the
   object-store layer (e.g. GCS DLP inspection, S3 Object Lambda).

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
