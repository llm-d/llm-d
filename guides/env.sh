#!/usr/bin/env bash
# Shared environment variables for all llm-d guides.
# Source this file in your shell before running guide commands:
#   source ${REPO_ROOT}/guides/env.sh

export REPO_ROOT=${REPO_ROOT:-$(realpath "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null)}
export GAIE_VERSION=v1.5.0
# Pinned to a released chart tag rather than the mutable `v0` channel so guide
# walkthroughs and CI are reproducible. Bump deliberately on each router release.
export ROUTER_CHART_VERSION=v0.9.0
export ROUTER_EPP_VERSION=main
# GitHub release tag for CRD manifests. Distinct from ROUTER_CHART_VERSION:
# the chart channel (v0) floats on the OCI registry and has no matching
# GitHub release, so release-asset URLs must use a real release tag.
export ROUTER_RELEASE_VERSION=v0.9.0
export ROUTER_STANDALONE_CHART=oci://ghcr.io/llm-d/charts/llm-d-router-standalone
export ROUTER_GATEWAY_CHART=oci://ghcr.io/llm-d/charts/llm-d-router-gateway
export ROUTER_EPP_IMAGE=ghcr.io/llm-d/llm-d-router-endpoint-picker
