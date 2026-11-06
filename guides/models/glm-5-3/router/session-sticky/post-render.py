#!/usr/bin/env python3
"""Helm post-renderer for the glm-5-3 router's Envoy configuration.

1. Unless --dp-ranks is 0, inserts an Envoy Lua HTTP filter ahead of ext_proc that sets
   `x-data-parallel-rank`, unless the client already set it: a hash of `x-session-id` (or AIPerf's
   per-conversation `x-correlation-id`) modulo --dp-ranks, or a random rank for requests without a
   session ID. The decode sidecar forwards the header on both legs and vLLM sends each leg to
   that rank, so every turn of a session reuses the same rank's prefix cache on both roles. This
   is for prefix-cache locality: MoRI-IO READ pulls from whichever prefill rank kv_transfer_params
   names, so the ranks need not match.
2. Re-probes an unhealthy ext_proc cluster every 5 s even without traffic (Envoy's
   no_traffic_interval defaults to 60 s), so the router recovers within seconds of an EPP restart.

The chart's Envoy config is one templated string, so neither change can be made from values or
kustomize without restating it. Usage:

  helm install ... --post-renderer guides/models/glm-5-3/router/session-sticky/post-render.py \
      [--post-renderer-args=--dp-ranks=0]
"""

import argparse
import sys

EXT_PROC = "- name: envoy.filters.http.ext_proc"
HEALTH_CHECK = "reuse_connection: true"

LUA = """\
function envoy_on_request(h)
  local hd = h:headers()
  if hd:get("x-data-parallel-rank") ~= nil then return end
  local s = hd:get("x-session-id") or hd:get("x-correlation-id")
  if s == nil then
    hd:add("x-data-parallel-rank", tostring(math.random(0, {last})))
    return
  end
  local x = 0
  for i = 1, #s do x = (x * 31 + string.byte(s, i)) % 2147483647 end
  hd:add("x-data-parallel-rank", tostring(x % {ranks}))
end
"""


def find_one(lines: list[str], anchor: str) -> tuple[int, str]:
    hits = [i for i, line in enumerate(lines) if line.strip() == anchor]
    if len(hits) != 1:
        sys.exit(f"glm-5-3 post-render: expected one '{anchor}', found {len(hits)}")
    i = hits[0]
    return i, lines[i][: len(lines[i]) - len(lines[i].lstrip())]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dp-ranks", type=int, default=8,
                    help="DP ranks per P/D pod; 0 skips the Lua filter (colocated layouts)")
    args = ap.parse_args()
    lines = sys.stdin.read().split("\n")

    i, pad = find_one(lines, HEALTH_CHECK)
    lines[i:i] = [f"{pad}no_traffic_interval: 5s", f"{pad}unhealthy_interval: 5s"]

    if args.dp_ranks > 0:
        i, pad = find_one(lines, EXT_PROC)
        code = LUA.format(ranks=args.dp_ranks, last=args.dp_ranks - 1).rstrip("\n").split("\n")
        lines[i:i] = [
            f"{pad}- name: envoy.filters.http.lua",
            f"{pad}  typed_config:",
            f'{pad}    "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua',
            f"{pad}    default_source_code:",
            f"{pad}      inline_string: |",
            *[f"{pad}        {c}" for c in code],
        ]
    sys.stdout.write("\n".join(lines))


if __name__ == "__main__":
    main()
