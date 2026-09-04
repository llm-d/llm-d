"""Run with: python3 -m unittest discover -s .github/scripts/e2e -v"""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("e2e-validate-flow-control.sh")


class TestBurstCompletion(unittest.TestCase):
    def test_final_metrics_wait_for_all_concurrent_bands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kubectl = root / "kubectl"
            kubectl.write_text(f"#!{sys.executable}\n" + '''
import os
from pathlib import Path
import sys
import time

root = Path(os.environ["TEST_STATE_DIR"])
args = sys.argv[1:]
if args[0] == "delete":
    with (root / "deletes").open("a") as log:
        log.write(str(len(list(root.glob("finished-*")))) + "\\n")
if args[0] in ("delete", "run", "wait"):
    raise SystemExit(0)
assert args[0] == "exec", args
command = args[args.index("--") + 1:]
if command[0] == "tee":
    sys.stdin.read()
elif command[:2] == ["sh", "-c"]:
    request = command[2]
    priority = ("100" if "premium-traffic" in request else
                "-10" if "best-effort-traffic" in request else "0")
    (root / ("started-" + priority)).touch()
    # Require concurrent dispatch, then keep all bands active beyond polling.
    deadline = time.monotonic() + 5
    while len(list(root.glob("started-*"))) != 3:
        if time.monotonic() > deadline:
            raise SystemExit("bands were not started concurrently")
        time.sleep(0.01)
    while not (root / "scrapes").exists():
        if time.monotonic() > deadline:
            raise SystemExit("no metrics poll observed")
        time.sleep(0.01)
    time.sleep({"100": 0.1, "0": 0.2, "-10": 0.3}[priority])
    (root / ("finished-" + priority)).touch()
    print("200")
elif command[0] == "curl" and command[-1].endswith("/metrics"):
    complete = len(list(root.glob("finished-*"))) == 3
    with (root / "scrapes").open("a") as log:
        log.write(str(complete) + "\\n")
    for priority, duration in [("100", 0.1), ("0", 0.2), ("-10", 0.4)]:
        series = "llm_d_epp_flow_control_request_queue_duration_seconds"
        labels = '{priority="' + priority + '"}'
        print(f"{series}_count{labels} {1 if complete else 0}")
        print(f"{series}_sum{labels} {duration if complete else 0}")
else:
    raise SystemExit(f"unexpected command: {command}")
''')
            kubectl.chmod(0o755)
            # Isolate the script's fixed /tmp burst logs from other test runs.
            script = root / SCRIPT.name
            script.write_text(SCRIPT.read_text().replace("/tmp/burst-", str(root / "burst-")))
            env = os.environ.copy()
            env.update(PATH=directory + os.pathsep + env["PATH"],
                       GATEWAY_HOST="fixture", TEST_STATE_DIR=directory,
                       POLL_ATTEMPTS="1", POLL_INTERVAL_SECONDS="0")
            process = subprocess.Popen(
                ["bash", str(script), "-m", "fixture"], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                self.assertEqual(len(list(root.glob("finished-*"))), 3)
                # Polling sees pending requests; the final scrape sees all done.
                self.assertEqual((root / "scrapes").read_text().splitlines(), ["False", "True"])
                self.assertEqual((root / "deletes").read_text().splitlines(), ["0", "3"])
                self.assertIn("Flow control verified", stdout)
            finally:
                # Also stop orphaned fixture requests when testing the old script.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                process.wait()


if __name__ == "__main__":
    unittest.main()
