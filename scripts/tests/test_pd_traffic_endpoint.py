"""Run the P/D traffic generator against local HTTP endpoints."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
from threading import Event, Thread

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "guides/recipes/observability/generate-traffic-pd.sh"


@pytest.mark.parametrize("source", ["environment", "argument", "argument-only", "empty-argument"])
def test_requests_reach_selected_endpoint(tmp_path: Path, source: str) -> None:
    received = []
    request_arrived = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            assert self.path == "/v1/models"
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.server.server_port, self.path, payload))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"choices": []}')
            request_arrived.set()

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
    threads = [Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    args = ["bash", str(SCRIPT), "1", "1"]
    selected = servers[int(source in ("argument", "argument-only"))]
    if source in ("argument", "argument-only"):
        args.append(f"http://127.0.0.1:{selected.server_port}/v1")
    elif source == "empty-argument":
        args.append("")
    env = {
        **os.environ,
        "ENDPOINT": f"http://127.0.0.1:{servers[0].server_port}/v1",
        "MODEL_NAME": "test-model",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    if source == "argument-only":
        env.pop("ENDPOINT")
    with (tmp_path / "traffic.log").open("w+") as log:
        process = subprocess.Popen(
            args, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            assert request_arrived.wait(5), "No inference request received"
            port, path, payload = received[0]
            assert port == selected.server_port
            assert path == "/v1/chat/completions"
            assert payload["model"] == "test-model"
            assert payload["messages"][0]["role"] == "user"
        finally:
            # Stop workers and sleep processes after the first real request.
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
            for server, thread in zip(servers, threads):
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            # The existing script only removes its stats directory on normal exit.
            stats = Path(f"/tmp/load_gen_stats_{process.pid}")
            if stats.exists():
                for counter in stats.iterdir():
                    counter.unlink()
                stats.rmdir()
