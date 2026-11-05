"""Run the multi-tenant GCP setup script against a fake gcloud executable."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guides/batch-serving/asynchronous-processing/multitenant/scripts/gcp-setup.sh"

FAKE_GCLOUD = """#!/bin/sh
printf '%s\\n' "$*" >> "$GCLOUD_TEST_LOG"
if [ "$3" = describe ]; then
  [ "$GCLOUD_TEST_MODE" = existing ]
  exit $?
fi
if [ "$3" = create ]; then
  case "$GCLOUD_TEST_MODE:$2" in
    fail_topic:topics|fail_subscription:subscriptions|fail_service_account:service-accounts)
      echo 'simulated creation failure' >&2
      exit 1 ;;
  esac
  [ "$GCLOUD_TEST_MODE" != existing ]
  exit $?
fi
exit 0
"""


class GcpSetupTest(unittest.TestCase):
    def run_setup(self, mode: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fake_gcloud = path / "gcloud"
            fake_gcloud.write_text(FAKE_GCLOUD)
            fake_gcloud.chmod(0o755)
            log = path / "calls.log"
            env = os.environ.copy()
            env.update(
                PATH=f"{directory}:{env['PATH']}",
                GCLOUD_TEST_MODE=mode,
                GCLOUD_TEST_LOG=str(log),
            )
            result = subprocess.run(
                ["bash", str(SCRIPT), "test-project"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            return result, log.read_text().splitlines()

    def test_creates_missing_resources(self):
        result, calls = self.run_setup("new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Done.", result.stdout)
        self.assertEqual(sum(" create " in f" {call} " for call in calls), 9)

    def test_skips_existing_resources(self):
        result, calls = self.run_setup("existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Done.", result.stdout)
        self.assertFalse(any(" create " in f" {call} " for call in calls))

    def test_creation_failure_stops_setup(self):
        for mode, resource in (
            ("fail_topic", "topic"),
            ("fail_subscription", "subscription"),
            ("fail_service_account", "SA"),
        ):
            with self.subTest(mode=mode):
                result, calls = self.run_setup(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Done.", result.stdout)
                self.assertIn("simulated creation failure", result.stderr)
                self.assertIn(f"failed to create or access {resource}", result.stderr)
                self.assertFalse(any("add-iam-policy-binding" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
