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


def test_decode_calibration_empty_terminal_and_usage_events() -> None:
    """Verify that empty terminal events (finish_reason: stop) and usage events are not counted as extra tokens."""
    script = get_embedded_script()

    class StreamWithTerminalAndUsageHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # 3 actual generation chunks
            chunks = [
                {"choices": [{"text": "First", "finish_reason": None}]},
                {"choices": [{"text": " second", "finish_reason": None}]},
                {"choices": [{"text": " third", "finish_reason": None}]},
            ]
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                time.sleep(0.002)
            # Empty terminal event
            empty_terminal = {"choices": [{"text": "", "finish_reason": "stop"}]}
            self.wfile.write(f"data: {json.dumps(empty_terminal)}\n\n".encode())
            # Usage-only event
            usage_event = {
                "choices": [],
                "usage": {"prompt_tokens": 16, "completion_tokens": 3, "total_tokens": 19},
            }
            self.wfile.write(f"data: {json.dumps(usage_event)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), StreamWithTerminalAndUsageHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    env = {
        **os.environ,
        "VLLM_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
        "MODEL_NAME": "synthetic-decode-model",
        "DECODE_REPLICAS": "1",
        "ISL": "16",
        "OSL": "3",
        "TARGET_TPOT_MS": "100.0",
        "CONCURRENCY_LADDER": "1",
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

    assert result.returncode == 0, result.stderr
    # Must report exactly 3 tokens generated during warmup, not 5
    assert "generated 3 tokens" in result.stdout
    assert "PEAK_DECODE_THROUGHPUT=" in result.stdout


def test_decode_calibration_multi_token_chunks() -> None:
    """Verify that multi-token chunks are correctly reflected via backend usage reporting."""
    script = get_embedded_script()

    class MultiTokenHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # 2 chunks emitted, but represents 8 tokens total
            chunks = [
                {"choices": [{"text": "alpha beta gamma delta", "finish_reason": None}]},
                {"choices": [{"text": " epsilon zeta eta theta", "finish_reason": "stop"}]},
            ]
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
                time.sleep(0.005)
            usage_event = {
                "choices": [],
                "usage": {"prompt_tokens": 16, "completion_tokens": 8, "total_tokens": 24},
            }
            self.wfile.write(f"data: {json.dumps(usage_event)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), MultiTokenHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    env = {
        **os.environ,
        "VLLM_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
        "MODEL_NAME": "synthetic-decode-model",
        "DECODE_REPLICAS": "1",
        "ISL": "16",
        "OSL": "8",
        "TARGET_TPOT_MS": "100.0",
        "CONCURRENCY_LADDER": "1",
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

    assert result.returncode == 0, result.stderr
    assert "generated 8 tokens" in result.stdout
    assert "PEAK_DECODE_THROUGHPUT=" in result.stdout


def test_decode_calibration_premature_eof() -> None:
    """Verify that an incomplete SSE stream ending before [DONE] is rejected."""
    script = get_embedded_script()

    class IncompleteStreamHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"choices": [{"text": "Incomplete output", "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            # Close connection immediately without [DONE]

    server = ThreadingHTTPServer(("127.0.0.1", 0), IncompleteStreamHandler)
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
        "CONCURRENCY_LADDER": "1",
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

    assert result.returncode != 0
    assert "completions stream ended prematurely before [DONE]" in result.stderr
    assert "PEAK_DECODE_THROUGHPUT=" not in result.stdout


def test_decode_calibration_all_failing_concurrency_ladder() -> None:
    """Verify that when no concurrency level meets the target TPOT SLO, calibration fails and outputs are suppressed."""
    script = get_embedded_script()

    class SlowHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in TOKEN_CHUNKS:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                time.sleep(0.015)  # 15 ms per token -> p90 TPOT will be ~15 ms
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Target TPOT is 2.0 ms, which is impossible since sleep is 15 ms
    env = {
        **os.environ,
        "VLLM_ENDPOINT": f"http://127.0.0.1:{server.server_port}",
        "MODEL_NAME": "synthetic-decode-model",
        "DECODE_REPLICAS": "1",
        "ISL": "16",
        "OSL": "4",
        "TARGET_TPOT_MS": "2.0",
        "CONCURRENCY_LADDER": "1,2",
        "REQUESTS_PER_WORKER": "1",
        "NUM_WARMUP": "1",
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

    assert result.returncode != 0
    assert "No concurrency level satisfied target p90 TPOT" in result.stderr
    assert "PEAK_DECODE_THROUGHPUT=" not in result.stdout
    assert "RECOMMENDED_P_D_RATIO=" not in result.stdout
