"""Warm the Hermes gateway before the golden gVisor snapshot is captured.

Hermes initializes its agent pipeline lazily on the first chat completion
(~3 s of imports, prompt assembly, and SQLite setup). Because all 1,000 agents
restore from a single golden snapshot, taking that snapshot cold would force
every restored agent to burn 3 s of CPU simultaneously during a wake burst.

This helper waits for the local Hermes gateway, executes three synthetic turns
carrying the `agent-warmup` marker (which `sitecustomize.py` answers in-process
inside `httpx` so no GPU/TPU capacity is consumed and zero TCP sockets are
frozen into the snapshot), and only then returns HTTP 200 on `:8081/ready`.
"""

from __future__ import annotations

import http.server
import json
import os
import signal
import sys
import threading
import time
import urllib.request

GATEWAY = f"http://127.0.0.1:{int(os.environ.get('API_SERVER_PORT', '80'))}"
READY_PORT = int(os.environ.get("READY_PORT", "8081"))
KEY = os.environ["API_SERVER_KEY"]
MARKER = "agent-warmup"

READY = threading.Event()


def _log(msg: str) -> None:
    print(f"[agent-warmup] {msg}", file=sys.stderr, flush=True)


def _wait_health(deadline: float) -> bool:
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{GATEWAY}/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def _turn(session_id: str, prompt: str) -> float:
    body = json.dumps(
        {
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": f"{MARKER}: {prompt}"}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KEY}",
            "X-Hermes-Session-Id": session_id,
        },
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()
    return (time.time() - t0) * 1000.0


def _warm() -> None:
    if not _wait_health(time.time() + 300):
        _log("gateway did not pass /health before deadline; opening /ready fallback")
        READY.set()
        return

    turns = (
        (f"{MARKER}-a", "say OK"),
        (f"{MARKER}-a", "say OK again"),
        (f"{MARKER}-b", "say OK"),
    )
    for session_id, prompt in turns:
        for attempt in range(1, 4):
            try:
                elapsed_ms = _turn(session_id, prompt)
                _log(f"{session_id} turn completed in {elapsed_ms:.0f} ms")
                break
            except Exception as exc:
                _log(f"{session_id} turn failed (attempt {attempt}): {exc}")
                time.sleep(1)

    READY.set()
    _log(f"golden-snapshot warmup complete; serving 200 on :{READY_PORT}/ready")


class _ReadyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200 if READY.is_set() else 503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args) -> None:
        return


def main() -> None:
    gateway_pid = int(sys.argv[1])
    signal.signal(signal.SIGTERM, lambda *_: os.kill(gateway_pid, signal.SIGTERM))
    threading.Thread(target=_warm, daemon=True).start()
    # Inside the isolated gVisor actor container, GKE Agent Substrate probes
    # container port 8081 from the worker network namespace.
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", READY_PORT), _ReadyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _, status = os.waitpid(gateway_pid, 0)
    _log(f"gateway process exited with status {status}")
    sys.exit(1)


if __name__ == "__main__":
    main()
