"""Regression test for reusing a NIXL source checkout during rebuilds."""

import importlib.util
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("install_nixl.py")
SPEC = importlib.util.spec_from_file_location("install_nixl", MODULE_PATH)
assert SPEC and SPEC.loader
install_nixl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_nixl)


class NixlCheckoutTest(unittest.TestCase):
    def test_existing_release_branch_does_not_block_rebuild(self):
        with tempfile.TemporaryDirectory() as source_dir:
            def git(*args):
                return subprocess.run(
                    ["git", "-C", source_dir, *args],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()

            git("init", "-q")
            git("-c", "user.name=Test", "-c", "user.email=test@example.com",
                "commit", "--allow-empty", "-qm", "initial")
            git("tag", "0.6.1")
            git("checkout", "-q", "-b", "release-0.6.1", "tags/0.6.1")

            with redirect_stdout(io.StringIO()):
                install_nixl.checkout_nixl_release(source_dir)
                install_nixl.checkout_nixl_release(source_dir)

            self.assertEqual(git("rev-parse", "HEAD"), git("rev-list", "-n", "1", "0.6.1"))
            self.assertEqual(git("branch", "--show-current"), "")


if __name__ == "__main__":
    unittest.main()
