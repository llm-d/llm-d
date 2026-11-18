"""Unit tests for tiered-prefix-cache verify.py.

Run from this directory:
    python3 -m unittest test_verify.py -v
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Make sibling `verify.py` importable when tests are run from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify  # noqa: E402


class TestIsStorageMode(unittest.TestCase):
    def test_fs_lowercase(self):
        with patch.dict(os.environ, {"LLMDBENCH_CICD_OFFLOADING_TARGET": "fs"}):
            self.assertTrue(verify.is_storage_mode())

    def test_fs_uppercase(self):
        # The env value is lowercased before comparison, so `FS` still matches.
        with patch.dict(os.environ, {"LLMDBENCH_CICD_OFFLOADING_TARGET": "FS"}):
            self.assertTrue(verify.is_storage_mode())

    def test_cpu(self):
        with patch.dict(os.environ, {"LLMDBENCH_CICD_OFFLOADING_TARGET": "cpu"}):
            self.assertFalse(verify.is_storage_mode())

    def test_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(verify.is_storage_mode())

    def test_other_value(self):
        with patch.dict(os.environ, {"LLMDBENCH_CICD_OFFLOADING_TARGET": "nvme"}):
            self.assertFalse(verify.is_storage_mode())


class TestFindOffloadPvc(unittest.TestCase):
    @patch("verify.v.kubectl")
    def test_returns_claim_name(self, mock_kubectl):
        # First call: volume name at mount path. Second: claim name for that volume.
        mock_kubectl.side_effect = ["vol-name", "my-pvc"]
        self.assertEqual(verify.find_offload_pvc("ns", "pod"), "my-pvc")
        self.assertEqual(mock_kubectl.call_count, 2)

    @patch("verify.v.kubectl")
    def test_no_volume_at_mount(self, mock_kubectl):
        mock_kubectl.return_value = ""
        self.assertIsNone(verify.find_offload_pvc("ns", "pod"))
        self.assertEqual(mock_kubectl.call_count, 1)

    @patch("verify.v.kubectl")
    def test_volume_without_pvc_claim(self, mock_kubectl):
        mock_kubectl.side_effect = ["vol-name", ""]
        self.assertIsNone(verify.find_offload_pvc("ns", "pod"))


class TestCheckPvcIsBound(unittest.TestCase):
    @patch("verify.find_offload_pvc", return_value="my-pvc")
    @patch("verify.v.kubectl", return_value="Bound")
    def test_bound(self, _mock_kubectl, _mock_find):
        c = verify.check_pvc_is_bound("ns", "pod")
        self.assertTrue(c.passed)
        self.assertIn("my-pvc", c.detail)
        self.assertIn("Bound", c.detail)

    @patch("verify.find_offload_pvc", return_value="my-pvc")
    @patch("verify.v.kubectl", return_value="Pending")
    def test_not_bound(self, _mock_kubectl, _mock_find):
        c = verify.check_pvc_is_bound("ns", "pod")
        self.assertFalse(c.passed)
        self.assertIn("Pending", c.detail)

    @patch("verify.find_offload_pvc", return_value=None)
    def test_no_mount(self, _mock_find):
        c = verify.check_pvc_is_bound("ns", "pod")
        self.assertFalse(c.passed)
        self.assertIn("no PVC mounted", c.detail)


class TestCheckPvcHasData(unittest.TestCase):
    @patch("verify.v.kubectl", return_value="")
    def test_kubectl_failed(self, _mock_kubectl):
        c = verify.check_pvc_has_data("ns", "pod")
        self.assertFalse(c.passed)
        self.assertIn("kubectl exec failed", c.detail)

    @patch("verify.v.kubectl", return_value="MISSING")
    def test_missing_dir(self, _mock_kubectl):
        c = verify.check_pvc_has_data("ns", "pod")
        self.assertFalse(c.passed)
        self.assertIn("does not exist", c.detail)

    @patch("verify.v.kubectl", return_value="42\n1.2G")
    def test_has_files(self, _mock_kubectl):
        c = verify.check_pvc_has_data("ns", "pod")
        self.assertTrue(c.passed)
        self.assertIn("42", c.detail)
        self.assertIn("1.2G", c.detail)

    @patch("verify.v.kubectl", return_value="0\n0")
    def test_no_files(self, _mock_kubectl):
        c = verify.check_pvc_has_data("ns", "pod")
        self.assertFalse(c.passed)
        self.assertIn("0 files", c.detail)

    @patch("verify.v.kubectl", return_value="notanumber\n1.2G")
    def test_unparseable_count(self, _mock_kubectl):
        c = verify.check_pvc_has_data("ns", "pod")
        self.assertFalse(c.passed)
        self.assertIn("unparseable", c.detail)


class TestMain(unittest.TestCase):
    def _run_main(self):
        # Silence output; we only care about the return code here.
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            return verify.main()

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_namespace(self):
        self.assertEqual(self._run_main(), 1)

    @patch.dict(os.environ, {"LLMDBENCH_CICD_NS": "ns"}, clear=True)
    @patch("verify.shutil.which", return_value=None)
    def test_kubectl_not_on_path(self, _mock_which):
        self.assertEqual(self._run_main(), 1)

    @patch.dict(
        os.environ,
        {"LLMDBENCH_CICD_NS": "ns", "LLMDBENCH_WORKSPACE": "/no/such/dir"},
        clear=True,
    )
    @patch("verify.shutil.which", return_value="/usr/bin/kubectl")
    @patch("verify.v.find_results_dirs", return_value=None)
    def test_no_results(self, _mock_find, _mock_which):
        self.assertEqual(self._run_main(), 1)


if __name__ == "__main__":
    unittest.main()
