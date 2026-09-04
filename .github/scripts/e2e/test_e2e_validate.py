"""Run with: python3 -m unittest discover -s .github/scripts/e2e -v"""

import http.server
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest


SCRIPT = Path(__file__).with_name("e2e-validate.sh")


@unittest.skipUnless(shutil.which("curl"), "curl is required")
class TestHTTPResponses(unittest.TestCase):
    def test_completion_statuses(self):
        chat = "/v1/chat/completions"
        completion = "/v1/completions"
        for failing_path, status in [(chat, 200), (chat, 400), (completion, 400),
                                     (chat, 500), (completion, 500)]:
            with self.subTest(path=failing_path, status=status):
                self.check_response(failing_path, status)

    def check_response(self, failing_path, status):
        requests = []
        error = b'{"error":{"message":"fixture inference failure"}}'

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                requests.append(self.path)
                response_status = status if self.path == failing_path else 200
                body = error if response_status >= 400 else b'{"choices":[{"text":"hello"}]}'
                self.send_response(response_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as directory:
                    kubectl = Path(directory) / "kubectl"
                    kubectl.write_text(f"#!{sys.executable}\n" + '''
import os
import subprocess
import sys

args = sys.argv[1:]
if args[0] == "delete":
    with open(os.environ["TEST_DELETE_LOG"], "a") as log:
        log.write(" ".join(args) + "\\n")
if args[0] in ("delete", "run", "wait"):
    raise SystemExit(0)
assert args[0] == "exec", args
command = args[args.index("--") + 1:]
command = [arg.replace("http://fixture:80", os.environ["TEST_ENDPOINT"])
           for arg in command]
# Exercise real curl HTTP handling; skip retry delays for permanent errors.
raise SystemExit(subprocess.call(command + ["--retry", "0", "--noproxy", "*"]))
''')
                    kubectl.chmod(0o755)
                    env = os.environ.copy()
                    env.update(PATH=directory + os.pathsep + env["PATH"],
                               GATEWAY_HOST="fixture",
                               TEST_DELETE_LOG=str(Path(directory) / "deletes"),
                               TEST_ENDPOINT=f"http://127.0.0.1:{server.server_port}")
                    result = subprocess.run(
                        ["bash", str(SCRIPT), "-m", "fixture"], env=env,
                        capture_output=True, text=True, timeout=15,
                    )
                    deletions = (Path(directory) / "deletes").read_text().splitlines()
            finally:
                server.shutdown()
                thread.join()

        self.assertEqual(len(deletions), 2)  # Initial deletion and EXIT cleanup.
        self.assertEqual(deletions[0], deletions[1])
        if status == 200:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(requests, ["/v1/chat/completions", "/v1/completions"] * 10)
            self.assertIn("All 10 iterations succeeded", result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(requests, ["/v1/chat/completions", "/v1/completions"])
            self.assertIn(error.decode(), result.stdout)
            self.assertIn(f"POST {failing_path} failed", result.stderr)
            self.assertNotIn("All 10 iterations succeeded", result.stdout)


if __name__ == "__main__":
    unittest.main()
