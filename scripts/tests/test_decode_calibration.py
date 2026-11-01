"""Exercise the decode calibration Job's embedded Python against local SSE responses."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
import time

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "guides/recipes/router/calibration/calibration-peak-decode.yaml"
TOKEN_CHUNKS = [
    {"choices": [{"text": "Hello", "finish_reason": None}]},
    {"choices": [{"text": " world", "finish_reason": None}]},
    {"choices": [{"text": " from", "finish_reason": None}]},
    {"choices": [{"text": " test", "finish_reason": None}]},
]
ERROR = {"error": {"message": "synthetic decode failure", "code": 500}}


def get_embedded_script() -> str:
    configmap = next(
        doc for doc in yaml.safe_load_all(MANIFEST.read_text())
        if doc["kind"] == "ConfigMap"
    )
    return configmap["data"]["calibrate_decode.py"]


@pytest.mark.parametrize(
    "events, succeeds, diagnostic",
    [
        (TOKEN_CHUNKS, True, None),
        ([ERROR], False, "synthetic decode failure"),
        (TOKEN_CHUNKS[:2] + [ERROR], False, "synthetic decode failure"),
        ([], False, "no streamed tokens received"),
    ],
    ids=["valid-stream", "error-first", "error-mid-stream", "empty-stream"],
)
def test_decode_calibration_stream(events, succeeds, diagnostic) -> None:
    script = get_embedded_script()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in events:
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                time.sleep(0.005)
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    env = {
        **os.environ,
        "VLLM_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
        "MODEL_NAME": "synthetic-decode-model",
        "DECODE_REPLICAS": "1",
        "ISL": "16",
        "OSL": "4",
        "TARGET_TPOT_MS": "100.0",
        "CONCURRENCY_LADDER": "1,2",
        "REQUESTS_PER_WORKER": "1",
        "NUM_WARMUP": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", script], env=env,
            capture_output=True, text=True, timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if succeeds:
        assert result.returncode == 0, result.stderr
        throughput = next(
            line.split("=", 1)[1] for line in result.stdout.splitlines()
            if line.startswith("PEAK_DECODE_THROUGHPUT=")
        )
        assert float(throughput) > 0
        assert "SATISFIED_CONCURRENCY=" in result.stdout
        assert "MEASURED_P90_TPOT_MS=" in result.stdout
    else:
        assert result.returncode != 0
        assert diagnostic in result.stderr
        assert "PEAK_DECODE_THROUGHPUT=" not in result.stdout


def test_decode_calibration_p_d_ratio_calculation() -> None:
    """Verify that when PEAK_PREFILL_THROUGHPUT is provided, the P:D ratio is calculated."""
    script = get_embedded_script()

    class FastHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in TOKEN_CHUNKS:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                time.sleep(0.002)
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), FastHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    env = {
        **os.environ,
        "VLLM_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
        "MODEL_NAME": "synthetic-decode-model",
        "DECODE_REPLICAS": "2",
        "ISL": "64",
        "OSL": "256",
        "TARGET_TPOT_MS": "50.0",
        "CONCURRENCY_LADDER": "1,2",
        "REQUESTS_PER_WORKER": "1",
        "NUM_WARMUP": "0",
        "PEAK_PREFILL_THROUGHPUT": "10000",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", script], env=env,
            capture_output=True, text=True, timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert "P_D_RATIO=" in result.stdout
    assert "RECOMMENDED_P_D_RATIO=" in result.stdout
    ratio_line = next(line for line in result.stdout.splitlines() if line.startswith("RECOMMENDED_P_D_RATIO="))
    p, d = ratio_line.split("=")[1].split(":")
    assert int(p) >= 1
    assert int(d) >= 1
