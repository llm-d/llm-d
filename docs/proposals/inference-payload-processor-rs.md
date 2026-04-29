# inference-payload-processor-rs

**Authors**: Nili Guy (_IBM_), Nir Rozenbaum (_Red Hat_)

## Summary

The [llm-d-inference-payload-processor](https://github.com/llm-d/llm-d-inference-payload-processor)
is a pluggable framework for processing the payload of inference requests and
responses on both the request path and the response path.

This proposal creates a new `llm-d-incubation/inference-payload-processor-rs`
repository to experiment with a Rust-based implementation of the payload
processor, targeting improved performance and lower resource overhead.

## Motivation

The current payload processor is implemented in Go. A Rust-based implementation
offers the potential for lower latency, reduced memory footprint, and better
throughput for high-volume inference serving workloads. The incubation
organization is the right home to explore this without impacting the stability
of the main payload processor repository.

Background:
- [llm-d-inference-payload-processor proposal](https://github.com/llm-d/llm-d/pull/1184)

### Goals

- Create the `llm-d-incubation/inference-payload-processor-rs` repository
- Experiment with a Rust-based implementation of the inference payload processor
- Evaluate performance characteristics compared to the Go implementation
- Establish ownership and contribution process for the new repository

### Non-Goals

- Replacing the existing Go-based payload processor
- Reimplementing all existing plugins as part of this proposal

## Proposal

We propose creating a new `llm-d-incubation/inference-payload-processor-rs`
repository. The incubation organization is the right home for this experimental
work, allowing the team to iterate on a Rust implementation independently while
the Go-based payload processor continues to evolve in its own repository.

The repository will serve as a sandbox for experimenting with Rust-based payload
processing, with the goal of evaluating whether a Rust implementation can
deliver meaningful performance improvements for llm-d inference serving
workloads.
