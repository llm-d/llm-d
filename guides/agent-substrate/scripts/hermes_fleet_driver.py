#!/usr/bin/env python3
"""Multi-tier traffic driver for 1,000 Hermes Agent sandboxes on GKE Agent Substrate.

Wakes and drives `agent-0001..agent-NNNN` through `atenet-router`, partitioning
agents across `premium-traffic` (100), `standard-traffic` (0), and
`best-effort-traffic` (-10) while passing `X-Hermes-Session-Id` and
`x-llm-d-inference-objective` end-to-end through Hermes (`sitecustomize.py`) to
the llm-d Router. Turn 1 assigns a deterministic codename; Turns 2+ verify
stateful SQLite session recall across gVisor snapshot pause/resume cycles.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

CODE_ADJ = (
    "tensor", "gradient", "sparse", "fused", "quantized", "eager", "sharded", "cached",
    "async", "dense", "strided", "batched", "compiled", "pinned", "scaled", "tiered",
)
CODE_NOUN = (
    "otter", "falcon", "panda", "gecko", "badger", "heron", "lynx", "koala",
    "marmot", "puffin", "walrus", "ibis", "quokka", "narwhal", "ocelot", "orca",
)
TIERS = ("premium-traffic", "standard-traffic", "best-effort-traffic")


def _validate_url(raw: str) -> str:
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"expected http(s) URL, got {raw!r}")
    return raw.rstrip("/")


def _codename(idx: int, gen: int) -> str:
    h = zlib.crc32(f"{idx}/{gen}".encode("utf-8")) & 0xFFFFFFFF
    return f"{CODE_ADJ[h % len(CODE_ADJ)]}-{CODE_NOUN[(h // len(CODE_ADJ)) % len(CODE_NOUN)]}"


def _assign_tier(idx: int, total: int, premium_pct: int, standard_pct: int) -> str:
    p_cut = max(1, (total * premium_pct) // 100) if premium_pct > 0 else 0
    if idx <= p_cut:
        return "premium-traffic"
    if idx <= p_cut + ((total * standard_pct) // 100):
        return "standard-traffic"
    return "best-effort-traffic"


def _p95(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(0.95 * (len(s) - 1)))))]


def _print_report(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n=== {title} ===")
    hdr = f"{'Tier (InferenceObjective)':<24} {'Calls':>7} {'Err':>5} {'E2E p50':>10} {'E2E p95':>10} {'Recall':>10}"
    print(hdr + "\n" + "-" * len(hdr))
    for tier in TIERS:
        t_rows = [r for r in rows if r["objective"] == tier]
        if not t_rows:
            continue
        ok = [r for r in t_rows if r["ok"]]
        e2es = [r["e2e_ms"] for r in ok]
        checked = [r for r in ok if r["recall_checked"]]
        rec_str = f"{sum(1 for r in checked if r['recall_ok'])}/{len(checked)}" if checked else "n/a"
        p50 = statistics.median(e2es) if e2es else 0.0
        print(f"{tier:<24} {len(t_rows):>7d} {len(t_rows) - len(ok):>5d} {p50:>8.1f}ms {_p95(e2es):>8.1f}ms {rec_str:>10}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive Hermes Agent sandboxes on GKE Agent Substrate -> llm-d")
    ap.add_argument("--agents", type=int, default=1000, help="number of Hermes agent actors (agent-0001..N)")
    ap.add_argument("--turns", type=int, default=2, help="turns per agent (turn 1 teaches codename, turns 2+ check recall)")
    ap.add_argument("--concurrency", type=int, default=200, help="concurrent agent turns in flight")
    ap.add_argument("--premium-pct", type=int, default=10, help="percentage of agents assigned to premium-traffic")
    ap.add_argument("--standard-pct", type=int, default=30, help="percentage of agents assigned to standard-traffic")
    ap.add_argument("--generation", type=int, default=1, help="session generation suffix (agent-NNNN-g<gen>)")
    ap.add_argument("--atenet-url", default="", help="atenet-router HTTP base URL")
    ap.add_argument("--atespace", default="", help="Agent Substrate atespace (default: agent-hermes)")
    ap.add_argument("--hermes-key-file", default="", help="path to HERMES_API_KEY file (>= 16 chars)")
    args = ap.parse_args()

    key_path = args.hermes_key_file or os.environ.get("HERMES_KEY_FILE", "")
    if not key_path or not pathlib.Path(key_path).is_file():
        print("error: --hermes-key-file (or HERMES_KEY_FILE) is required", file=sys.stderr)
        return 2
    api_key = pathlib.Path(key_path).read_text(encoding="utf-8").strip()
    if len(api_key) < 16:
        print("error: HERMES_API_KEY must be at least 16 characters", file=sys.stderr)
        return 2

    atenet_url = _validate_url(
        args.atenet_url or f"http://{os.environ.get('ATENET_ADDR', 'atenet-router.ate-system.svc.cluster.local:80')}"
    )
    atespace = args.atespace or os.environ.get("ATESPACE", "agent-hermes")

    def run_turn(idx: int, turn_num: int) -> dict[str, Any]:
        name = f"agent-{idx:04d}"
        session_id = f"{name}-g{args.generation}"
        objective = _assign_tier(idx, args.agents, args.premium_pct, args.standard_pct)
        code = _codename(idx, args.generation)
        prompt = (
            f"hello from {name}. Your codename is {code}; remember it in your session history. "
            f"Reply with a one-sentence quip about Kubernetes and KV caches."
            if turn_num == 1
            else f"hello again from {name} (turn {turn_num}). What is your codename? "
            f"State your codename first, then one short sentence on prefix caching."
        )
        body = json.dumps({"model": "hermes-agent", "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Host": f"{name}.{atespace}.actors.resources.substrate.ate.dev",
            "ate-target-actor": f"{atespace}/{name}",
            "X-Hermes-Session-Id": session_id,
            "x-llm-d-inference-objective": objective,
        }
        t0 = time.monotonic()
        for attempt in range(1, 5):
            req = urllib.request.Request(f"{atenet_url}/v1/chat/completions", data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    parsed = json.loads(resp.read().decode("utf-8"))
                reply = parsed.get("choices", [{}])[0].get("message", {}).get("content", "")
                norm = lambda s: "".join(ch for ch in s.lower() if "a" <= ch <= "z")
                return {
                    "objective": objective,
                    "ok": True,
                    "e2e_ms": (time.monotonic() - t0) * 1000.0,
                    "recall_checked": turn_num > 1,
                    "recall_ok": (norm(code) in norm(reply)) if turn_num > 1 else True,
                }
            except urllib.error.HTTPError as err:
                # Never retry 4xx or 504 (Hermes may still append the turn to SQLite on a 504).
                if err.code < 500 or err.code == 504:
                    break
                time.sleep(0.2 * attempt)
            except Exception:
                time.sleep(0.2 * attempt)
        return {"objective": objective, "ok": False, "e2e_ms": (time.monotonic() - t0) * 1000.0, "recall_checked": False, "recall_ok": False}

    for turn_num in range(1, args.turns + 1):
        t_start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(lambda i: run_turn(i, turn_num), range(1, args.agents + 1)))
        _print_report(
            f"Turn {turn_num}/{args.turns} across {args.agents} Hermes sandboxes "
            f"(wall={time.monotonic() - t_start:.2f}s, concurrency={args.concurrency})",
            results,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
