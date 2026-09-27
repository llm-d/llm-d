import http.server
import json
import pathlib
import subprocess
import threading


SCRIPT = pathlib.Path(__file__).parents[1] / "healthcheck.sh"


class UnavailableHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(503)
        self.end_headers()

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(503)
        self.end_headers()

    def log_message(self, *_args):
        pass


class CompletionsFallbackHandler(http.server.BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
        elif self.path == "/v1/models":
            self._send_json(200, {"data": [{"id": "test-model"}]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        content_length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(content_length))
        self.requests.append((self.path, payload))

        if self.path == "/v1/completions":
            self.send_response(404)
            self.end_headers()
        elif self.path == "/v1/chat/completions":
            if "messages" in payload and "prompt" not in payload:
                self._send_json(200, {"choices": [{"message": {"content": "ok"}}]})
            else:
                self._send_json(400, {"error": "messages is required"})
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def test_unhealthy_endpoint_sets_failure_exit_code():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UnavailableHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [str(SCRIPT), "--endpoint", f"http://127.0.0.1:{server.server_port}", "--timeout", "2"],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Status:    UNHEALTHY" in result.stdout
    assert "failed" in result.stdout


def test_auto_fallback_rebuilds_chat_payload():
    CompletionsFallbackHandler.requests = []
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), CompletionsFallbackHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                str(SCRIPT),
                "--endpoint",
                f"http://127.0.0.1:{server.server_port}",
                "--model",
                "test-model",
                "--timeout",
                "2",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "/v1/chat/completions" in result.stdout
    assert CompletionsFallbackHandler.requests == [
        (
            "/v1/completions",
            {"model": "test-model", "prompt": "San Francisco is a", "max_tokens": 8},
        ),
        (
            "/v1/chat/completions",
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "San Francisco is a"}],
                "max_tokens": 8,
            },
        ),
    ]
