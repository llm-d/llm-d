# llm-d-inference-payload-processor

**Authors**: Nili Guy (_IBM_), Nir Rozenbaum (_Red Hat_)

## Summary

BBR (Body Based Routing) was the original name of a component in the
[Gateway API Inference Extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension).
Since its inception, the component has evolved well beyond simple body-based
routing. In its current state, it is a pluggable framework for processing the
payload of inference requests and responses — including both headers and body —
on both the request path (before the scheduler) and the response path (after
the inference engine responds).

This proposal migrates the BBR code into a new
`llm-d/llm-d-inference-payload-processor` repository under the llm-d
organization.

## Motivation

As the BBR component matured, it grew into a general-purpose payload processing
framework no longer accurately described by its original name. The framework
supports pluggable logic on both the request and response paths. Examples
include request and response guardrails based on Nemo Guardrails, already
implemented and expected to be contributed upstream.

Hosting this in its own repository:
- Accurately reflects the scope and purpose of the project under a clear,
  descriptive name
- Allows the component to evolve independently with its own release cycle,
  ownership, and contribution process
- Makes it easier for external contributors to discover and adopt the framework

### Goals

- Create the `llm-d/llm-d-inference-payload-processor` repository
- Migrate BBR code from the Gateway API Inference Extension into the new
  repository using the automation established in
  [llm-d-inference-scheduler#804](https://github.com/llm-d/llm-d-inference-scheduler/pull/804)
- Establish ownership and contribution process for the new repository

### Non-Goals

- Rewriting or redesigning the existing BBR framework
- Adding new payload processing plugins as part of this migration

## Proposal

We propose creating a new `llm-d/llm-d-inference-payload-processor` repository.
The name accurately describes the component's current capabilities: pluggable
processing of the payload of inference requests and responses.

The repository will live in the main `llm-d` organization given the component's
role as a core part of the llm-d inference serving stack. The code migration
will follow the process established in
[llm-d-inference-scheduler#804](https://github.com/llm-d/llm-d-inference-scheduler/pull/804).
