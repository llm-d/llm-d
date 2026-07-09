#!/usr/bin/env python3
"""Send short and long prompts at the router, to exercise both prefill pools.

Fires N requests in parallel per iteration, for M iterations. Every request
carries a unique prefix so no two requests share a prefix-cache block.

Assumes `kubectl port-forward svc/context-length-aware-epp 8000:80` is running.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

URL = "http://localhost:8000/v1/completions"
MODEL = "Qwen/Qwen3-0.6B"

# The `estimate` token backend counts 4 bytes per token, so "hi " * 10000 is
# ~7.5k estimated tokens: well over the 1000 boundary, still under max-model-len.
BODIES = {
    "short (-> prefill-short)": "word",
    "long (-> prefill-long)": "hi " * 10000,
}


def complete(prompt: str) -> str:
    body = json.dumps({"model": MODEL, "prompt": prompt, "max_tokens": 10})
    req = urllib.request.Request(
        URL, data=body.encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)["choices"][0]["text"]


def run_one(label: str, body: str) -> tuple[str, bool, str]:
    # A unique prefix at position 0 diverges the token sequence from the very
    # first block, so nothing downstream can hit the prefix cache either.
    prompt = f"{uuid.uuid4().hex} {body}"
    try:
        return label, True, complete(prompt).replace("\n", " ")
    except urllib.error.HTTPError as e:
        return label, False, f"HTTP {e.code}: {e.read().decode()}"
    except OSError as e:
        return label, False, str(e)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-n", "--parallel", type=int, default=8,
                   help="requests fired in parallel per iteration, per prompt size")
    p.add_argument("-m", "--iterations", type=int, default=5,
                   help="number of iterations")
    args = p.parse_args()

    calls = [(label, body) for label, body in BODIES.items()
             for _ in range(args.parallel)]
    failures = 0

    for i in range(args.iterations):
        print(f"=== iteration {i + 1}/{args.iterations}: {len(calls)} requests")
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            results = list(pool.map(lambda c: run_one(*c), calls))
        for label, ok, text in results:
            if not ok:
                failures += 1
                print(f"  {label}: {text}", file=sys.stderr)
            else:
                print(f"  {label}: {text}")

    if failures:
        print(f"{failures} request(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
