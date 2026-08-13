#!/usr/bin/env bash
# Shared environment variables for all llm-d guides.
# Source this file in your shell before running guide commands:
#   source ${REPO_ROOT}/guides/env.sh

export REPO_ROOT=${REPO_ROOT:-$(realpath "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null)}

### Release Versions for grabbing CRDs
# Controls which release of Gateway API Inference Extension to grab CRDs from
export GAIE_VERSION=latest
# Controls which release of llm-d/llm-router to grab CRDs from. Used in flowcontrol guide
export ROUTER_RELEASE_VERSION=latest

### Chart versions and OCI coordinates for router chart
export ROUTER_CHART_VERSION=v0
export ROUTER_STANDALONE_CHART=oci://ghcr.io/llm-d/charts/llm-d-router-standalone
export ROUTER_GATEWAY_CHART=oci://ghcr.io/llm-d/charts/llm-d-router-gateway

### Container Image coordinates and tag for router chart
export ROUTER_EPP_VERSION=main
export ROUTER_EPP_IMAGE=ghcr.io/llm-d/llm-d-router-endpoint-picker
