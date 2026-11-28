#!/usr/bin/env bash
# Fails when a routing sidecar in a rendered manifest doesn't serve its
# /metrics where the PodMonitors look for it (llm-d#2988).
#
# The sidecar only serves llm_d_disagg_sidecar_* when started with
# --metrics-port, and the decode PodMonitors scrape it through the port named
# sc-metrics. Each overlay patches the sidecar on its own, so a new decode base
# that copies an older one loses the metrics without any error.
#
# Usage: check-routing-sidecar-metrics.sh <rendered.yaml>
# Prints each sidecar that is missing either piece and exits 1 if there are any.

set -euo pipefail

rendered="${1:?usage: $0 <rendered.yaml>}"

# A sidecar is the routing-proxy container, or any container running the
# sidecar image, so one under a different container name is still checked.
# shellcheck disable=SC2016 # $resource is a yq variable, not a shell one.
missing=$(yq -N '
  (.kind + "/" + (.metadata.name // "")) as $resource
  | .. | select(tag == "!!map" and has("image") and
      (.name == "routing-proxy" or
        ((.image | tag) == "!!str" and (.image | test("disagg-sidecar|ROUTING_SIDECAR")))))
  | select(
      ((.args // []) | any_c(tag == "!!str" and (. == "--metrics-port" or test("^--metrics-port="))) | not)
      or ((.ports // []) | any_c(.name == "sc-metrics") | not))
  | "  " + $resource + " container " + .name
' "${rendered}")

if [[ -n "${missing}" ]]; then
  echo "routing sidecar without --metrics-port or an sc-metrics port:"
  echo "${missing}"
  exit 1
fi
