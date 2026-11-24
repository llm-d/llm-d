#!/usr/bin/env python3
"""Multi-tier traffic driver for 1,000 Hermes Agent sandboxes on Agent Substrate.

Wakes and drives `agent-0001..agent-NNNN` through `atenet-router`, partitioning
agents across `premium-traffic` (100), `standard-traffic` (0), and
`best-effort-traffic` (-10) while passing `X-Hermes-Session-Id` and
`x-llm-d-inference-objective` end-to-end through Hermes (`sitecustomize.py`) to
the llm-d Router. Turn 1 assigns a deterministic codename; Turns 2+ verify
stateful SQLite session recall. With `--recall-only` no turn teaches the
codename, so a run after the agents were suspended passes only if each agent's
session state survived its snapshot.

The Agent Substrate router serves only TLS and requires a client certificate:
the driver presents the pod certificate mounted at `ROUTER_CLIENT_BUNDLE` and
verifies the router against `ROUTER_CA_FILE`.

Exits 1 when the error rate or the recall rate of any turn misses its bound.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import random
import ssl
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
NOTE_WORDS = (
    "router", "replica", "prefill", "decode", "token", "queue", "batch", "kernel", "socket", "thread",
    "buffer", "cursor", "shard", "index", "vector", "matrix", "packet", "frame", "block", "page",
    "node", "pool", "probe", "trace", "span", "metric", "gauge", "label", "volume", "mount",
    "image", "layer", "digest", "secret", "policy", "tunnel", "proxy", "header", "stream", "chunk",
)


def _notes(idx: int, gen: int, words: int) -> str:
    """Deterministic filler that is unique per agent session, so each session carries its own long prefix."""
    rng = random.Random(f"{idx}/{gen}")
    return " ".join(rng.choice(NOTE_WORDS) for _ in range(words))


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


def teach_expected(turn_num: int, recall_only: bool) -> bool:
    return turn_num == 1 and not recall_only


def _post_turn(url: str, body: bytes, headers: dict[str, str], tls, *, opener=urllib.request.urlopen, sleep=time.sleep, attempts: int = 4) -> str | None:
    """POST one agent turn and return the body of the HTTP 200 response, or None.

    Only failures that cannot have completed the turn are retried: connection
    errors and 5xx other than 504. A 4xx, a 504 or a timeout is not retried
    (Hermes may still append the turn to SQLite), and once a 200 response has
    arrived the turn is never sent again, whatever its body contains.
    """
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            resp = opener(req, timeout=90, context=tls)
        except urllib.error.HTTPError as err:
            if err.code < 500 or err.code == 504:
                break
            sleep(0.2 * attempt)
            continue
        except Exception as err:
            if isinstance(err, TimeoutError) or isinstance(getattr(err, "reason", None), TimeoutError):
                break
            sleep(0.2 * attempt)
            continue
        try:
            with resp:
                return resp.read().decode("utf-8")
        except Exception:
            return None
    return None


def _reply_text(raw: str) -> str | None:
    """The assistant reply in an OpenAI chat-completion body.

    Returns "" for a valid response without text (`"choices": []` or
    `"content": null`) and None when the body is not a JSON object.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    choices = parsed.get("choices") or [{}]
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else ""


def _recalled(code: str, reply: str) -> bool:
    """True when the reply contains the codename, ignoring case, spacing and punctuation."""
    norm = lambda s: "".join(ch for ch in s.lower() if "a" <= ch <= "z")
    return norm(code) in norm(reply)


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive Hermes Agent sandboxes on Agent Substrate -> llm-d")
    ap.add_argument("--agents", type=int, default=1000, help="number of Hermes agent actors (agent-0001..N)")
    ap.add_argument("--turns", type=int, default=2, help="turns per agent (turn 1 teaches codename, turns 2+ check recall)")
    ap.add_argument("--concurrency", type=int, default=200, help="concurrent agent turns in flight")
    ap.add_argument("--premium-pct", type=int, default=10, help="percentage of agents assigned to premium-traffic")
    ap.add_argument("--standard-pct", type=int, default=30, help="percentage of agents assigned to standard-traffic")
    ap.add_argument("--generation", type=int, default=1, help="session generation suffix (agent-NNNN-g<gen>)")
    ap.add_argument("--context-words", type=int, default=0, help="append this many words of per-session filler to the teach turn, to give every session a long private prefix (about 1.2 tokens per word)")
    ap.add_argument("--recall-only", action="store_true", help="never teach the codename: every turn asks for it (use after suspending agents)")
    ap.add_argument("--max-error-pct", type=float, default=1.0, help="exit 1 if more than this percentage of calls in a turn fail")
    ap.add_argument("--min-recall-pct", type=float, default=99.0, help="exit 1 if fewer than this percentage of checked calls in a turn recall the codename")
    ap.add_argument("--atenet-url", default="", help="atenet-router base URL (default: https://$ATENET_ADDR)")
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
        args.atenet_url or f"https://{os.environ.get('ATENET_ADDR', 'atenet-router.ate-system.svc:443')}"
    )
    tls = None
    if atenet_url.startswith("https://"):
        # The router's serving certificate is issued for atenet-router.ate-system.svc.
        tls = ssl.create_default_context(cafile=os.environ.get("ROUTER_CA_FILE", "/run/router-client/router-ca.pem"))
        tls.load_cert_chain(os.environ.get("ROUTER_CLIENT_BUNDLE", "/run/router-client/credential-bundle.pem"))
    atespace = args.atespace or os.environ.get("ATESPACE", "agent-hermes")

    def run_turn(idx: int, turn_num: int) -> dict[str, Any]:
        name = f"agent-{idx:04d}"
        session_id = f"{name}-g{args.generation}"
        objective = _assign_tier(idx, args.agents, args.premium_pct, args.standard_pct)
        code = _codename(idx, args.generation)
        teach = teach_expected(turn_num, args.recall_only)
        prompt = (
            f"hello from {name}. Your codename is {code}; remember it in your session history. "
            f"Reply with a one-sentence quip about Kubernetes and KV caches."
            + (f" Background notes for this session, no need to act on them: {_notes(idx, args.generation, args.context_words)}" if args.context_words > 0 else "")
            if teach
            else f"hello again from {name} (turn {turn_num}). What is your codename? "
            f"State your codename first, then one short sentence on prefix caching."
        )
        body = json.dumps({"model": "hermes-agent", "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "ate-target-actor": f"{atespace}/{name}",
            "X-Hermes-Session-Id": session_id,
            "x-llm-d-inference-objective": objective,
        }
        t0 = time.monotonic()
        raw = _post_turn(f"{atenet_url}/v1/chat/completions", body, headers, tls)
        reply = _reply_text(raw) if raw is not None else None
        if reply is None:
            return {"objective": objective, "ok": False, "e2e_ms": (time.monotonic() - t0) * 1000.0, "recall_checked": False, "recall_ok": False}
        return {
            "objective": objective,
            "ok": True,
            "e2e_ms": (time.monotonic() - t0) * 1000.0,
            "recall_checked": not teach,
            "recall_ok": _recalled(code, reply) if not teach else True,
        }

    failed = False
    for turn_num in range(1, args.turns + 1):
        t_start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(lambda i: run_turn(i, turn_num), range(1, args.agents + 1)))
        _print_report(
            f"Turn {turn_num}/{args.turns} across {args.agents} Hermes sandboxes "
            f"(wall={time.monotonic() - t_start:.2f}s, concurrency={args.concurrency})",
            results,
        )
        ok = [r for r in results if r["ok"]]
        checked = [r for r in ok if r["recall_checked"]]
        error_pct = 100.0 * (len(results) - len(ok)) / len(results) if results else 0.0
        recall_pct = 100.0 * sum(1 for r in checked if r["recall_ok"]) / len(checked) if checked else 100.0
        if error_pct > args.max_error_pct:
            print(f"FAIL: turn {turn_num}: {error_pct:.2f}% of calls failed (limit {args.max_error_pct}%)", file=sys.stderr)
            failed = True
        if recall_pct < args.min_recall_pct:
            print(f"FAIL: turn {turn_num}: recall {recall_pct:.2f}% (minimum {args.min_recall_pct}%)", file=sys.stderr)
            failed = True
        if not teach_expected(turn_num, args.recall_only) and not checked:
            print(f"FAIL: turn {turn_num}: no successful call to check recall on", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
