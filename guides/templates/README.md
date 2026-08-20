# Authoring a Guide

Each guide is two files:

- **`guide.yaml`** — machine-readable source of truth for env vars and shell commands.
- **`README.md`** — human-readable prose with bash code blocks that are *filled from the YAML*.

One script — [`scripts/guide.py`](../../scripts/guide.py) — validates and renders between them.

**Reference implementation**: [`guides/optimized-baseline/`](../optimized-baseline/) is the fully-adopted example. Compare its [`guide.yaml`](../optimized-baseline/guide.yaml) with its rendered [`README.md`](../optimized-baseline/README.md) any time these docs describe a pattern — every rule below is exercised there.

- - -

## Contents

- [Quickstart — new guide from template](#quickstart--new-guide-from-template)
- [The marker system](#the-marker-system)
- [Adding markers to an existing guide](#adding-markers-to-an-existing-guide)
- [Writing steps and filters](#writing-steps-and-filters)
- [The script](#the-script)
- [Schema quick reference](#schema-quick-reference)

- - -

## Quickstart — new guide from template

```bash
mkdir guides/my-guide
cp guides/templates/guide.template.yaml   guides/my-guide/guide.yaml
cp guides/templates/README.template.md    guides/my-guide/README.md

# edit both files: name, commands, prose
$EDITOR guides/my-guide/guide.yaml guides/my-guide/README.md

# validate + render (render validates first and refuses on error)
scripts/guide.py render guides/my-guide
```

- - -

## The marker system

Bash code blocks in the README are filled from the YAML. Each block is bounded by a **paired HTML comment** naming a YAML path.

Example — from the reference guide's [`README.md`](../optimized-baseline/README.md):

````markdown
<!-- guide:env.static start -->
```bash
export BRANCH=main
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
export GUIDE_NAME=optimized-baseline
export NAMESPACE=llm-d-optimized-baseline
export HF_TOKEN=HF_TOKEN_PLACEHOLDER
…
```
<!-- guide:env.static end -->
````

The body was rendered from [`guide.yaml`](../optimized-baseline/guide.yaml)'s `env: static:` map — every variable there becomes an `export` line here.

Rules:

- The `start` marker names a YAML path — `env.static`, `deploy.standalone`, `verify.tests`, etc.
- The `end` marker must match the same path.
- Everything **between** the markers is replaced on render — leave empty ```` ```bash ```` fences or old content; the renderer overwrites it.
- Everything **outside** the markers is preserved byte-for-byte. Write prose freely.
- Paths use dot notation: `env.static` → `env: static:`, `verify.endpoint.gateway` → `verify: endpoint: gateway:`.
- Rendering is **idempotent** — running the renderer twice yields the same file.

Valid marker targets in the current schema (with live examples from optimized-baseline):

| Path | Comes from |
| ---- | ---------- |
| `env.static` | `env: static:` — rendered as `export VAR=…` lines |
| `env.source` | `env: source:` — rendered as `source <path>` lines |
| `prerequisites.<sub-group>` | `prerequisites:` — optimized-baseline uses `clone`, `gaie`, `namespace`, `secrets` |
| `deploy.<sub-group>` | `deploy:` — optimized-baseline uses `router_values`, `monitoring_values`, `standalone`, `gateway`, `modelserver`, `monitoring` |
| `verify.endpoint.<mode>` | one entry per mode (`standalone`, `gateway`) |
| `verify.tests` | `verify: tests:` |
| `benchmark.setup` / `benchmark.endpoint.<mode>` / `benchmark.execute` | (optional section) |
| `cleanup` (or `cleanup.<sub-group>`) | `cleanup:` |

Any dot-path that resolves to a step list (or step-list sub-group) is a valid marker target.

- - -

## Adding markers to an existing guide

Guides that pre-date this system have hand-authored bash fences in their README. Convert them a block at a time — every un-marked fence stays untouched, so partial adoption is safe.

Concrete example — how the `helm install` block in optimized-baseline got converted:

**Before** — bare bash fence in `README.md`:

````markdown
### Standalone Mode

```bash
helm install ${GUIDE_NAME} \
  ${ROUTER_STANDALONE_CHART} \
  -f ${ROUTER_BASE_VALUES} \
  ${MONITORING_VALUES} \
  -f ${ROUTER_VALUES} \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```
````

**Step 1** — declare the step in `guide.yaml`:

```yaml
deploy:
  standalone:
    - run: |
        helm install ${GUIDE_NAME} \
          ${ROUTER_STANDALONE_CHART} \
          -f ${ROUTER_BASE_VALUES} \
          ${MONITORING_VALUES} \
          -f ${ROUTER_VALUES} \
          -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
```

**Step 2** — wrap the README fence with paired markers naming the path:

````markdown
### Standalone Mode

<!-- guide:deploy.standalone start -->
```bash
```
<!-- guide:deploy.standalone end -->
````

Leave the fence empty — the renderer fills it.

**Step 3** — validate and render, in one command:

```bash
scripts/guide.py render guides/optimized-baseline
```

**Step 4** — verify the result (also what CI runs):

```bash
scripts/guide.py render guides/optimized-baseline --check
```

The rendered fence now contains the exact bash from the YAML, and further edits go in the YAML — never touch the marker body directly.

Tip: work section by section (`prerequisites`, then `deploy`, etc.). Each pass keeps everything else authored the old way.

- - -

## Writing steps and filters

A step is a map with a `run:` string plus optional filters. From optimized-baseline's `prerequisites`:

```yaml
prerequisites:
  clone:
    - skip_in: [ci]
      run: git clone https://github.com/llm-d/llm-d.git && cd llm-d && git checkout ${BRANCH}
  namespace:
    - run: kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
  secrets:
    - skip_in: [ci]
      run: |
        kubectl create secret generic llm-d-hf-token \
          --from-literal="HF_TOKEN=${HF_TOKEN}" \
          --namespace "${NAMESPACE}"
```

- **`when:`** — a map of `VAR: [allowed-values]`. Variable must be declared in `env.static:`.
- **`skip_in:`** — a list of context tags naming consumers that must skip this step. Any tag is valid; `ci` is the only one with *rendering* behaviour — it wraps the step's fence in `<!-- llm-d-cicd:skip start --> … <!-- llm-d-cicd:skip end -->` so the README-parsing CI runner skips it (used above for `clone` and `secrets`, which CI already handles out-of-band). Other tags (e.g. `skip_in: [kind]`) render normally and are honoured by runners reading `guide.yaml` directly.
- **`run:`** — the bash. Use `|` for multi-line. `#` inside a `|` block is a bash comment (not a YAML comment).

> [!IMPORTANT]
> `skip_in:` selects on **who is running** the guide. To select on **what is being deployed** (accelerator, platform, router mode), declare a variable in `env.static:` and use `when:` — see [Target environments](#target-environments) below.

`when:` in action — from optimized-baseline's `cleanup:`:

```yaml
cleanup:
  - run: helm uninstall ${GUIDE_NAME} -n ${NAMESPACE}
  - when: { ACCELERATOR_TYPE: [gpu] }
    run: kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/…/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}
  - when: { ACCELERATOR_TYPE: *non_gpu }
    run: kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/…/${ACCELERATOR_TYPE}/${MODEL_SERVER}
```

Renders to:

```bash
helm uninstall optimized-baseline -n llm-d-optimized-baseline

# only when ACCELERATOR_TYPE=gpu:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/…/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}

# only when ACCELERATOR_TYPE=amd or xpu or hpu or tpu/v6 or tpu/v7 or cpu:
kubectl delete -n ${NAMESPACE} -k ${REPO_ROOT}/…/${ACCELERATOR_TYPE}/${MODEL_SERVER}
```

The reader picks the line matching their config.

> [!WARNING]
> **`when:` is lossy in the rendered README — automation must consume `guide.yaml`, not the markdown.**
>
> Mutually exclusive steps flatten into a single fence, annotated with `# only when …` comments. A human reads the annotations and picks one. A runner scraping the rendered fence would execute *every* branch, which for some guides is destructive (workload-autoscaling explicitly warns against applying both its KEDA and HPA overlays).
>
> `guide.yaml` is the machine interface: `when:` is still structured there, so a runner resolves it against its own variable values and gets exactly one branch. The `<!-- llm-d-cicd:skip -->` markers exist for the *legacy* README-scraping path and should not be read as an endorsement of it.

### Sensitive variables

A variable marked `sensitive: true` must declare a `default:` — that value is the
placeholder shown in the README. It renders into **its own fence, wrapped in
`<!-- llm-d-cicd:skip -->` markers**:

````markdown
<!-- guide:env.static start -->
```bash
export NAMESPACE=llm-d-my-guide
```
<!-- llm-d-cicd:skip start -->
```bash
export HF_TOKEN=HF_TOKEN_PLACEHOLDER
```
<!-- llm-d-cicd:skip end -->
```bash
export MODEL=Qwen/Qwen3-32B
```
<!-- guide:env.static end -->
````

A human reads it in place, exactly as before. A README-parsing runner harvests
neither a command nor a variable from a skip region, so a real credential
supplied out-of-band is never shadowed by the placeholder — without this, a
runner resolving `${HF_TOKEN}` would substitute the literal string
`HF_TOKEN_PLACEHOLDER`.

Contiguous variables share a fence, so declaration order is preserved and a
sensitive entry mid-list simply splits the block in three. Order matters because
a later variable may reference an earlier one.

`skip_in: [ci]` renders structurally — the step's ```bash fence gets wrapped. From optimized-baseline's rendered `prerequisites.secrets`:

````markdown
<!-- guide:prerequisites.secrets start -->
<!-- llm-d-cicd:skip start -->
```bash
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}"
```
<!-- llm-d-cicd:skip end -->
<!-- guide:prerequisites.secrets end -->
````

Contiguous `skip_in: [ci]` steps in the same marker section share one wrapper (one fence, both steps inside). The wrapper is HTML-comment-based, so it's invisible to human readers of the rendered markdown — it's a signal for the CI extraction tool.

### Target environments

`when:` filters on any variable declared in `env.static:` — including ones that describe the **target environment** rather than the workload. A guide with steps that differ between a local Kind cluster, a pre-provisioned cluster, and OpenShift declares the axis once:

```yaml
env:
  static:
    ENV: { default: existing, values: [kind, existing, ocp] }
```

and gates the steps that differ:

```yaml
prerequisites:
  keda:
    - when: { ENV: [kind] }
      run: helm install keda kedacore/keda -n keda --create-namespace
    - when: { ENV: [ocp] }
      run: |
        # OpenShift ships KEDA as the Custom Metrics Autoscaler operator
        kubectl get sub openshift-custom-metrics-autoscaler-operator -n openshift-keda
```

This needs no new schema — `ENV` is an ordinary categorical variable. Name the axis `ENV` across guides so runners can set it uniformly.

The same applies to the model server. A guide that ships a GPU-free path — so a pre-merge runner can execute it on Kind against [`llm-d-inference-sim`](https://github.com/llm-d/llm-d-inference-sim) — declares `sim` alongside the real engines:

```yaml
env:
  static:
    MODEL_SERVER: { default: vllm, values: [vllm, sglang, trtllm, sim] }

deploy:
  modelserver:
    - when: { MODEL_SERVER: [sim] }
      run: kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/sim/
```

Declare a value only when the guide has an overlay behind it. `values:` is what the rendered README advertises to readers, so an unbacked value reads as a supported option that silently deploys nothing.

- - -

## The script

[`scripts/guide.py`](../../scripts/guide.py) validates and renders. Targets are guide
directories or `guide.yaml` paths, absolute or relative — the companion file is
discovered alongside.

### `guide.py render` — fill the README from the YAML

```bash
# validate, then update in place
scripts/guide.py render guides/optimized-baseline

# preview to stdout without writing
scripts/guide.py render guides/optimized-baseline --dry-run

# CI check — non-zero if the README is out of date
scripts/guide.py render guides/optimized-baseline --check
```

`render` **validates first and refuses to render a guide that fails its own
schema**, so the normal authoring loop is this one command. Only content between
paired markers is touched, and rendering is idempotent.

### `guide.py check` — validate without writing

```bash
scripts/guide.py check guides/optimized-baseline

# every guide in the repo
scripts/guide.py check guides/*/

# pass the files explicitly instead of a directory
scripts/guide.py check --yaml guides/optimized-baseline/guide.yaml \
                       --md   guides/optimized-baseline/README.md

# validate one half on its own
scripts/guide.py check --yaml guides/optimized-baseline/guide.yaml   # schema only
scripts/guide.py check --md   guides/optimized-baseline/README.md    # structure only
```

A *guide* is a directory containing a `guide.yaml`, so `guides/*/` matches only
real guides. Use `--md` to check a lone markdown file that has no YAML behind it.

Marker-path resolution is the only check that spans both files. With `--md`
alone it cannot run, so the tool reports what it actually covered rather than a
bare "OK":

```
README.md: OK  (structure only — pass --yaml to resolve marker paths)
```

On `guide.yaml`:

- All top-level keys are known (`name`, `env`, `prerequisites`, `deploy`, `verify`, `benchmark`, `cleanup`)
- No duplicate keys in any mapping — YAML silently keeps only the last value, which has already hidden one real bug
- Every step is a map with a `run:` string
- Every `when:` filter references a variable declared in `env.static:`
- Sensitive vars have a `default:` (used as the README placeholder)
- Categorical vars with `values:` have their `default:` in-list

On `README.md`:

- Markers are properly paired, nested marker pairs are rejected
- Every `guide:<path>` resolves to a real YAML node
- The body between each marker pair is a fenced ```` ```bash ```` block

Exits non-zero on any error. With multiple targets every guide is reported before
exiting, so one run surfaces every problem in the repo.

### Recommended CI

One command covers both files across every guide:

```bash
scripts/guide.py render guides/*/ --check
```

`--check` catches out-of-date READMEs — someone edited `guide.yaml` without
re-rendering — and because `render` validates first, a schema error fails the
same job.

### Using it as a library

`guide.py` is importable. Everything except `Guide.load()` and `Guide.write()` is
pure, so callers can validate and render content that never touches disk:

```python
from guide import Guide

g = Guide.load("guides/optimized-baseline")       # dir, or a guide.yaml path
g = Guide.load(yaml="g.yaml", md="README.md")     # explicit; either may be omitted
g = Guide.from_text(yaml_text=..., md_text=...)   # no filesystem at all

if g.check().ok():
    g.write()                                     # True if the file changed

```

Both halves are optional, and the checks adapt to what is loaded:

| loaded | `check()` covers |
| --- | --- |
| yaml only | schema |
| md only | marker pairing + body well-formedness |
| both | the above, plus `guide:<path>` resolution |

`g.resolves_paths` tells you whether the cross-file check ran, so a partial pass
is never mistaken for a full one. `g.check_yaml()` and `g.check_md()` isolate one
half explicitly.

`check()` returns `Findings` — iterate it for structured `Finding` objects
(`message`, `source` (`"yaml"` or `"md"`), `line`, `severity`) rather than
parsing stderr.

- - -

## Schema quick reference

```yaml
name: <guide-name>              # required

env:                            # required
  static:                       # required — variable declarations
    VAR:                        # constant (scalar form)
    VAR: { default: <v> }       # overridable default
    VAR: { default: <v>, values: [<v1>, <v2>] }   # categorical (values gates when:)
    VAR: { default: PLACEHOLDER, sensitive: true } # secret — see "Sensitive variables"
  source: ["<path>", …]         # optional — `source <path>` lines (verbatim)

prerequisites:                  # optional — flat list or map of named sub-groups
  - run: <bash>
  # or:
  namespace:
    - run: <bash>
  secrets:
    - run: <bash>

deploy:                         # required — sub-groups (mode-named or common)
  standalone:                   # mode names should match verify.endpoint keys
    - run: <bash>
  gateway:
    - run: <bash>
  <sub-group>:                  # optional named sub-groups
    - run: <bash>

verify:                         # optional
  endpoint:                     # optional — one entry per mode. Omit for guides
    <mode>:                     #   with no endpoint to talk to (e.g. autoscaling)
      - run: <bash>
  tests:                        # optional
    - run: <bash>

benchmark:                      # optional
  setup:                        # optional
    - run: <bash>
  endpoint:                     # optional — keys must match verify.endpoint keys
    <mode>:
      - run: <bash>
  execute:                      # optional
    - run: <bash>

cleanup:                        # optional — flat list or map of named sub-groups
  - run: <bash>
```

**Step shape**, valid in any step list:

```yaml
- when:    { VAR: [<allowed-values>] }    # optional include filter
  skip_in: [<context>]                    # optional exclude filter
  run: <string>                           # required
```

**Sub-groups** — any step-list slot (`prerequisites:`, `deploy.<x>:`, `cleanup:`) can be either a flat list or a map of named lists. Named sub-groups can be targeted by finer README markers (e.g. `<!-- guide:cleanup.namespace start -->`).

**YAML anchors** (`_lists:`) — declare reusable value lists at the top:

```yaml
_lists:
  non_gpu: &non_gpu [amd, xpu, hpu, cpu]

deploy:
  standalone:
    - when: { ACCELERATOR_TYPE: *non_gpu }
      run: <bash>
```

The `_lists:` key is a schema anchor slot — the checker ignores its contents. optimized-baseline uses this to define the set of non-GPU accelerators once, then reference it in filters throughout `deploy:` and `cleanup:`.

- - -

## See it end-to-end

Everything above is exercised in [`guides/optimized-baseline/`](../optimized-baseline/):

- Sub-groups under `prerequisites:` and `deploy:` → [`guide.yaml`](../optimized-baseline/guide.yaml)
- YAML anchors (`_lists.non_gpu`) → top of the same file
- Sensitive vars (`HF_TOKEN` with `sensitive: true`) → in `env.static:`
- `skip_in: [ci]` renders → `prerequisites.clone`, `prerequisites.secrets` in the [rendered README](../optimized-baseline/README.md)
- `when:` filter annotations → the cleanup section of the same rendered README

Copy patterns from there rather than reinventing them.
