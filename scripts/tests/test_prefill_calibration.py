"""Exercise the calibration Job's embedded Python against local SSE responses."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "guides/recipes/router/calibration/calibration-peak-throughput.yaml"
TOKEN = {"choices": [{"text": "hello", "finish_reason": None}]}
ERROR = {"error": {"message": "synthetic backend failure", "code": 500}}


@pytest.mark.parametrize(
    "events, succeeds, diagnostic",
    [
        ([TOKEN], True, None),
        ([ERROR], False, "synthetic backend failure"),
        ([TOKEN, ERROR], False, "synthetic backend failure"),
        ([], False, "no streamed chunk received"),
    ],
    ids=["token", "error-first", "error-after-token", "done-only"],
)
def test_calibration_stream(events, succeeds, diagnostic) -> None:
    configmap = next(
        doc for doc in yaml.safe_load_all(MANIFEST.read_text())
        if doc["kind"] == "ConfigMap"
    )
    script = configmap["data"]["calibrate.py"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in events:
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        **os.environ,
        "VLLM_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
        "MODEL_NAME": "synthetic-model",
        "CHUNK_SIZE": "16",
        "T_MAX_SECONDS": "18",
        "NUM_WARMUP": "0",
        "NUM_MEASUREMENTS": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", script], env=env,
            capture_output=True, text=True, timeout=15,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if succeeds:
        assert result.returncode == 0, result.stderr
        throughput = next(
            line.split("=", 1)[1] for line in result.stdout.splitlines()
            if line.startswith("PEAK_PREFILL_THROUGHPUT=")
        )
        assert float(throughput) > 0
    else:
        assert result.returncode != 0
        assert diagnostic in result.stderr
        assert "PEAK_PREFILL_THROUGHPUT=" not in result.stdout
