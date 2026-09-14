import http.server
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
