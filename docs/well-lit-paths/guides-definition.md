# llm-d Guides: Definition and Policy

## Contents

- [1. Terminology](#1-terminology)
- [2. What is a guide](#2-what-is-a-guide)
- [3. What is *not* a guide](#3-what-is-not-a-guide)
- [4. Baseline requirements for every guide](#4-baseline-requirements-for-every-guide)
- [5. Production-readiness tiers](#5-production-readiness-tiers)
- [6. Placing and promoting a guide](#6-placing-and-promoting-a-guide)
- [7. Foundation guides vs workload guides](#7-foundation-guides-vs-workload-guides)
- [8. Contribution standards](#8-contribution-standards)
- [9. Lifecycle: demotion, archival, graduation](#9-lifecycle-demotion-archival-graduation)

- - -

## 1. Terminology

| Term | Meaning |
| ---- | ------- |
| **Well-lit path** | The *concept*: an opinionated, curated way to run a given inference pattern/pipeline on llm-d, chosen by the project as working examples with specific infrastructure. |
| **Guide** | The *artifact* that makes a well-lit path executable: a directory under `guides/` containing documentation and, at higher tiers, deployable manifests and machine-readable steps. |
| **Foundation guide** | A guide covering one capability or one physical execution path (routing algorithm, KV-cache tier, disaggregation topology, queuing engine). |
| **Workload guide** | A guide that composes foundation guides into one cohesive stack for a use case (agentic, multimodal, RL, batch). |
| **Supporting guide** | Operational tooling documentation that is not a deployment path in itself (e.g. benchmarking automation, weight-transfer helpers). |
| **Recipe** | Reusable, non-standalone kustomize building blocks under `guides/recipes/`. Recipes are *ingredients*, not guides. |
| **Reference environment** | The single platform / accelerator / model-server / model combination against which a guide's tier claim is evidenced. See [§5.2](#52-tiers-are-claimed-against-a-reference-environment). |

- - -

## 2. What is a guide

A guide is the answer to: *"I want to serve LLM traffic with llm-d — what exactly do I deploy, and how do I know it worked?"*

A directory under `guides/` is a guide if and only if all of the following hold:

1. **It describes a deployment topology or a composition**, not a parameter value.
   A guide changes *what runs and how the pieces are wired* — the number and role
   of model-server groups, which router/EPP plugins are active, which cache tiers
   exist, which services sit in the request path. If the delta from an existing
   guide can be expressed as "set this field to that value", it is not a guide
   (see [§3](#3-what-is-not-a-guide)).
2. **It is end-to-end.** A reader who follows it from top to bottom reaches a
   working, verifiable serving endpoint — starting from prerequisites and ending
   with a verification step and a cleanup step. Fragments that only make sense
   inside another guide are recipes or components.
3. **It is opinionated.** A guide states *the* recommended configuration for its
   pattern and explains the reasoning. It is not a catalogue of every possible
   option. Alternatives belong in a tuning section, not in the main flow.
4. **It has a named owner.** An `OWNERS` file identifies who is accountable for
   keeping it correct.
5. **It is reproducible by a stranger.** No undocumented cluster assumptions, no
   internal-only images, no steps that exist only in someone's shell history.

Guides are a starting point for a user's own configuration, not a supported
product surface. That framing is already stated in
[`guides/README.md`](../../../guides/README.md) and this document does not change it.

- - -

## 3. What is *not* a guide

### 3.1 The decision rule

If the answer is a parameter, it is a knob, not a guide.

A knob is documented in the tuning section of the guide it belongs to, or as a
kustomize component, or in the accelerator/platform documentation. It never
justifies a new directory under `guides/`.

### 3.2 Explicit non-goals

| Not a guide | Where it belongs |
| ----------- | ---------------- |
| **A different model name:** Serving `model-A` instead of `model-B` with the same topology. | The `MODEL` variable of an existing guide; a per-model note in that guide. |
| **A small tweak:** One flag, one env var, one replica count, one scheduler weight. | The tuning section of the relevant guide, or a kustomize component. |
| **Per-model tuning tables:** "Best flags for model X on hardware Y." | Model or accelerator documentation, referenced from the guide. |
| **Hardware/accelerator enablement:** Making a device work at all. | `docs/` accelerator support pages; guides then declare which accelerators they cover. |
| **Benchmark methodology:** How we measure, which harness, how to read results. | Supporting guide / `helpers/`. |
| **A one-off demo or conference script:** | A blog post or a demo repository. |
| **A vendor-specific fork or image:** | An image component override with a tracking issue, per `guides/README.md`. |
| **Composable manifests with no standalone deployment:** | `guides/recipes/`. |

### 3.3 Borderline cases

When it is unclear, ask in order:

1. Does it change topology or composition? If no → knob.
2. Can it be deployed and verified on its own? If no → recipe.
3. Does it merely re-parameterize an existing guide? If yes → extend that guide.
4. Would a reader plausibly choose *between* it and an existing guide? If no →
   it is a section of that guide, not a sibling of it.

- - -

## 4. Baseline requirements for every guide

These apply to **every** guide at Tier 1 and above, independently of tier. Tier
requirements in [§5](#5-production-readiness-tiers) are *additional*.

1. **`OWNERS`** file, with at least two owners.
2. **`README.md`** with, at minimum, these sections in this order:
   - Overview — what problem this solves and when to choose it
   - What you get — the topology, ideally with a diagram
   - Prerequisites
   - Deploy
   - Verify
   - Cleanup
   - Troubleshooting (optional; may be short)
   - Tuning / alternatives (optional; may link to a separate `tuning.md`)
3. **Declared reference environment** — platform, accelerator, model server, and
   model that the instructions are known to work on.
4. **Declared status** — the guide states its maturity in prose, so a reader is
   never misled about how tested it is.
5. **Cleanup instructions** that actually remove what the guide created,
   including cluster-scoped objects.
6. **Indexed** — linked from `guides/README.md` under the right grouping.
7. **No hardcoded image versions** — images come from the shared kustomize
   components; any override carries a `TODO` with a tracking issue.
8. **Shared configuration reuse** — sources `guides/env.sh` rather than
   redefining chart versions and URLs.

- - -

## 5. Production-readiness tiers

### 5.1 The ladder

Tiers express **how much evidence exists that the guide works**, from an idea
(Tier 0) to a pattern demonstrated to deliver a benefit (Tier 5).

The ladder is **strict and cumulative**: a guide at Tier N satisfies every
requirement of Tiers 0…N-1. There is no skipping. A guide that has, say, nightly
testing but no kustomize overlay is **Tier 1 with a declared gap**, not Tier 3 —
see [§5.3](#53-declared-gaps).

| Tier | Label | The claim it makes | Evidence required |
| ---- | ----- | ------------------ | ----------------- |
| **0** | Planned | "We intend to build this, and we agree on what it should achieve." | Accepted goals/proposal, an owner, a target release. May be referenced publicly as *coming soon*. **No directory under `guides/` yet**. |
| **1** | Experimental | "Here is how this works and how to do it; you are on your own for the details." | Documentation only — prose, diagrams, snippets. Meets all baseline requirements of [§4](#4-baseline-requirements-for-every-guide). May be called a guide, but is labelled experimental. |
| **2** | Deployable | "You can apply this and it will install cleanly." | Full kustomize overlays and manifest integration; the overlay is covered by the kustomize dry-run CI job (i.e. it is **not** listed in `guides/.ci-dry-run-guide-exclusions`). |
| **3** | Validated | "This is exercised end-to-end every night, and its steps are machine-checked." | A `guide.yaml` conforming to the schema in `guides/templates/` and validating under `scripts/guide.py`, **and** an end-to-end nightly job producing a status badge in the release testing matrix. |
| **4** | Benchmarked | "It has been run under load and the numbers are sane." | A benchmark workload runs against the guide, and the results are **validated** — coherent, explainable, and free of visible bugs or pathologies. Any harness qualifies (`inference-perf`, `nop`, …); the requirement is a real run with reviewed results, not a specific tool. Baseline-only numbers are sufficient for this tier. |
| **5** | Proven | "This delivers a measurable benefit, and we have published why." | A blog post (or equivalent published narrative) that explains the benefits, presents results that are coherent and consistent, **and shows an improvement** over the relevant baseline. A post that only presents a baseline stays at Tier 4. |

### 5.2 Tiers are claimed against a reference environment

A tier claim is scoped to the guide's declared reference
environment. A guide validated nightly on one provider/accelerator/model-server
combination is Tier 3 *for that combination*; coverage of other combinations is
reported by the badge matrix, not by the tier.

Without this scoping, a single tier number is inaccurate for most readers, since
guides differ widely in how many accelerators and providers they cover.

### 5.3 Declared gaps

A guide may — and should — record what is missing for the **next** tier. Format:

```markdown
Tier: 1 (Experimental)
Gaps to Tier 2: no kustomize overlay; router configuration is a values file
applied by hand.
```

This keeps the ladder strict while making progress legible: the gap list is the
to-do list for promotion, and it prevents the "we are almost Tier 3" claim from
living only in someone's head.

### 5.4 Where the tier is recorded

The tier and its reference environment live in **one machine-readable
place** — `guide.yaml` for guides that have one, and a small metadata file
otherwise — so that:

- a lint job can verify the claim against the evidence (overlay in CI, badge
  present, benchmark workload defined), and
- the index and any future badges can be generated rather than hand-maintained.

Duplicating the tier in prose across several files is how it rots.

- - -

## 6. Placing and promoting a guide

### 6.1 How a guide is placed in a tier

A guide's tier is the **highest tier whose evidence exists today**. Because the
evidence for Tiers 2–4 is mostly mechanical (CI coverage, schema validation,
nightly badge, benchmark run), placement is largely a matter of checking
artefacts rather than forming an opinion:

1. Does an accepted set of goals and an owner exist? → at least Tier 0.
2. Does the documentation meet [§4](#4-baseline-requirements-for-every-guide)? → Tier 1.
3. Are there kustomize overlays covered by the dry-run CI job? → Tier 2.
4. Is there a validating `guide.yaml` **and** a nightly end-to-end job with a badge? → Tier 3.
5. Has a benchmark workload been run with reviewed, sane results? → Tier 4.
6. Is there a published narrative showing a benefit over baseline? → Tier 5.

Tier 4 and Tier 5 involve judgement — "the results make sense", "the benefit is
real" — and that judgement is exercised by the approvers below, not by the
guide's author.

### 6.2 Who approves a promotion

Promotion is **not** self-service by the guide owners.

- **Proposer:** the guide owner opens a promotion request, listing the evidence
  for the target tier and closing out the declared gaps.
- **Advisor:** the **release / maintainers** group reviews the evidence and
  issues a recommendation. They are the ones who can judge whether nightly
  coverage is real, whether benchmark results are trustworthy, and what the
  promotion costs in shared infrastructure.
- **Approver:** the **leading team** approves. The release/maintainers group is
  itself represented in the leading team; it advises the remaining leaders, and
  the promotion is approved when there is **consensus** among them.

Promotions into Tier 3 and above should be batched around a
release, since they commit shared nightly capacity.

- - -

## 7. Foundation guides vs workload guides

Guides come in two shapes. This is a distinction of *role*, not of version — one
does not supersede the other, and both are first-class.

| | **Foundation guides** | **Workload guides** |
| --- | --- | --- |
| Answers | "How does capability X work and how do I turn it on?" | "How do I serve workload Y well?" |
| Content | One capability, routing algorithm, or execution topology | A cohesive composition of several foundations |
| Examples | intelligent routing, KV-cache tiering, P/D disaggregation, wide expert-parallelism, flow control | agentic serving, multimodal serving, RL rollout, batch serving |
| New machinery | May introduce it | **Must not** introduce it — it composes what exists |
| Reader | An operator evaluating a capability | An operator with a use case, who wants one recommended stack |

Rules for workload guides:

1. **Compose, do not invent.** A workload guide must state which foundation
   guides it composes and link to them. If it needs machinery that no foundation
   guide covers, that machinery must first become (or extend) a foundation guide.
2. **No duplicated instructions.** Deployment steps live in the foundation guide
   or the shared recipes; the workload guide references them and adds only the
   composition, the tuning choices for the workload, and the workload-specific
   verification.

- - -

## 8. Contribution standards

### 8.1 Entry path for a new guide

```
proposal / accepted goals        →  Tier 0
        ↓
documentation PR                 →  Tier 1
        ↓
manifests + CI dry-run coverage  →  Tier 2
        ↓
guide.yaml + nightly onboarding  →  Tier 3
        ↓
benchmark run with sane results  →  Tier 4
        ↓
published narrative w/ benefit   →  Tier 5
```

Each arrow is a promotion and follows [§6.2](#62-who-approves-a-promotion).

### 8.2 Before opening a PR

- **Check it is a guide at all** against [§2](#2-what-is-a-guide) and
  [§3](#3-what-is-not-a-guide). Extending an existing guide is usually the right
  answer and is not a lesser contribution.
- **Discuss first for Tier 0.** New guides start as an agreed set of goals.
- **Use the templates.** `guides/templates/` is the single source of authoring
  instructions.

### 8.3 Requirements for the PR

1. Baseline requirements of [§4](#4-baseline-requirements-for-every-guide) are met.
2. The claimed tier is stated, with its gaps to the next tier.
3. `OWNERS` is added or updated.
4. `guides/README.md` is updated with the index entry.
5. All CI checks pass, including the kustomize dry-run job for Tier 2+ and
   `scripts/guide.py` validation for Tier 3+.
6. Any addition to `guides/.ci-dry-run-guide-exclusions` carries a comment
   explaining why the path is not a deployable overlay.
7. Images come from components; every override has a `TODO` with an issue.
8. The instructions were executed on a real cluster, and the PR says which one
   (this is the reference environment).

### 8.4 Maintenance duties of an owner

- Keep the guide working across releases.
- Respond to nightly failures for Tier 3+ guides.
- Keep the declared gaps honest.
- Hand over or hand back ownership explicitly rather than going quiet.

- - -

## 9. Lifecycle: demotion, archival, graduation

### 9.1 Demotion

A tier is a claim about the present, not a badge earned once.

For Tier 3+: after **N consecutive nightly failures** on the
reference environment, an issue is filed against the guide's owners; if the guide
is still failing after **a further grace period**, it is demoted to Tier 2 and
the failing nightly job is muted or removed. Values for N and the grace period
are an [open question](#10-open-questions).

Demotion is a mechanical health signal, not a punishment, and re-promotion uses
the ordinary path.

### 9.2 Archival

A guide that has no owner, does not work, and nobody intends to fix is archived:
removed from the index, and either moved to the incubation organization or
deleted with a note pointing at the replacement. Leaving a broken guide in place
is worse than not having it.
