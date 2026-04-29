# coordinator

**Authors**: Nili Guy (_IBM_)

## Summary

As llm-d evolves toward disaggregated inference serving, a new coordination
layer is needed to orchestrate interactions between prefill and decode workers,
manage scheduling decisions, and experiment with novel disaggregation
strategies.

This proposal creates a new `llm-d-incubation/coordinator` repository to
experiment with coordinator components for disaggregated inference serving.

## Motivation

The evolution of disaggregated serving in llm-d requires a dedicated
coordinator component that sits above individual inference workers and makes
higher-level decisions about request routing, encode/prefill/decode orchestration, and
request processing. An incubation repository provides the right space to
iterate on this architecture without blocking core llm-d development.

Background and design discussion:
- [Evolution of Disaggregated Serving](https://docs.google.com/document/d/1J7i_J1PwgqstIrMudnZMQf10zgP7ql8k9vgpHEJs9is/edit?tab=t.0#heading=h.o1lcfhplntik)
- [Coordinator Design](https://docs.google.com/document/d/1Bdwdyh5ULnV_0bYMXlW2Em4BJISqqAJ9kjb9F3dv3vQ/edit?tab=t.0#heading=h.tmmlv95uti06)

### Goals

- Create the `llm-d-incubation/coordinator` repository
- Provide a dedicated space to experiment with coordinator architecture for
  disaggregated inference serving
- Establish ownership and contribution process for the new repository

### Non-Goals

- Replacing or modifying existing EPP scheduling logic as part of this proposal

## Proposal

We propose creating a new `llm-d-incubation/coordinator` repository. The
incubation organization is the right home for this work given that the
coordinator design is still being actively explored and iterated on.

The repository will serve as a sandbox for experimenting with coordination
strategies between encode, prefill and decode workers in disaggregated inference
serving scenarios.
