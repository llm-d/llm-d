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
from unittest.mock import MagicMock, patch

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


class TestPrometheusCounter(unittest.TestCase):
    def test_sums_label_series(self):
        text = (
            '# HELP lmcache_mp_l1_write_chunks_total Stored chunks\n'
            'lmcache_mp_l1_write_chunks_total{cache_salt="first"} 2\n'
            'lmcache_mp_l1_write_chunks_total{cache_salt="second tenant"} 3e1\n'
            'lmcache_mp_l1_read_chunks_total{cache_salt=""} 99\n'
        )
        self.assertEqual(
            verify.prometheus_counter(text, "lmcache_mp_l1_write_chunks_total"), 32,
        )

    def test_unlabeled_counter(self):
        self.assertEqual(verify.prometheus_counter("counter_total 7", "counter_total"), 7)

    def test_zero_is_present(self):
        self.assertEqual(verify.prometheus_counter("counter_total 0", "counter_total"), 0)

    def test_missing_is_none(self):
        self.assertIsNone(verify.prometheus_counter("other_counter_total 1", "counter_total"))

    def test_rejects_invalid_values(self):
        for value in ("NaN", "+Inf", "-1", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify.prometheus_counter(f"counter_total {value}", "counter_total")


class TestCheckLMCacheMPStores(unittest.TestCase):
    def test_missing_cache_servers_fails(self):
        checks = verify.check_lmcache_mp_stores("ns", [], False)
        self.assertFalse(checks[0].passed)
        self.assertIn("no cache-server pods", checks[0].detail)

    @patch("verify.v.kubectl")
    def test_any_server_can_have_stores(self, mock_kubectl):
        mock_kubectl.side_effect = [
            "lmcache_mp_l1_write_chunks_total{cache_salt=\"\"} 5",
            "# An idle server has not emitted the store counter yet",
        ]
        checks = verify.check_lmcache_mp_stores("ns", ["cache-a", "cache-b"], False)
        self.assertTrue(checks[0].passed)
        self.assertIn("stored chunks=5", checks[0].detail)
        self.assertIn("cache-a:8080/proxy/metrics", mock_kubectl.call_args_list[0].args[0][-1])

    @patch("verify.v.kubectl")
    def test_no_stores_fails(self, mock_kubectl):
        for text in ("lmcache_mp_l1_write_chunks_total 0", "# No store samples"):
            with self.subTest(text=text):
                mock_kubectl.return_value = text
                checks = verify.check_lmcache_mp_stores("ns", ["cache"], False)
                self.assertFalse(checks[0].passed)

    @patch("verify.v.kubectl")
    def test_failed_scrape_is_not_hidden_by_other_server(self, mock_kubectl):
        mock_kubectl.side_effect = ["lmcache_mp_l1_write_chunks_total 5", ""]
        checks = verify.check_lmcache_mp_stores("ns", ["cache-a", "cache-b"], False)
        self.assertFalse(checks[0].passed)
        self.assertIn("cache-b: no metrics", checks[0].detail)

    @patch("verify.v.kubectl", return_value="lmcache_mp_l1_write_chunks_total NaN")
    def test_invalid_counter_fails(self, _mock_kubectl):
        checks = verify.check_lmcache_mp_stores("ns", ["cache"], False)
        self.assertFalse(checks[0].passed)

    @patch("verify.v.kubectl")
    def test_filesystem_requires_completed_l2_stores(self, mock_kubectl):
        for l2_count in (0, 3):
            with self.subTest(l2_count=l2_count):
                mock_kubectl.return_value = (
                    "lmcache_mp_l1_write_chunks_total 5\n"
                    f"lmcache_mp_l2_store_completed_objects_chunks_total {l2_count}\n"
                )
                checks = verify.check_lmcache_mp_stores("ns", ["cache"], True)
                self.assertEqual(len(checks), 2)
                self.assertTrue(checks[0].passed)
                self.assertEqual(checks[1].passed, l2_count > 0)


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

    @patch("verify.v.kubectl", return_value="42\n1.2G")
    def test_mp_uses_separate_cache_directory(self, mock_kubectl):
        c = verify.check_pvc_has_data("ns", "cache", verify.MP_KV_CACHE_DIR)
        self.assertTrue(c.passed)
        self.assertIn("/kv-cache/mp", c.detail)
        self.assertIn("find /mnt/files-storage/kv-cache/mp", mock_kubectl.call_args.args[0][-1])


class TestConnectorDispatch(unittest.TestCase):
    def run_main(self, *, mp: bool, storage: bool):
        metrics = MagicMock()
        metrics.check_aggregated.return_value = verify.v.Check("Requests observed", True)
        metrics.check_per_pod.return_value = verify.v.Check("Native stores", True)
        with (
            patch.dict(os.environ, {
                "LLMDBENCH_CICD_NS": "ns",
                "LLMDBENCH_CICD_OFFLOADING_TARGET": "fs" if storage else "cpu",
            }, clear=True),
            patch("verify.shutil.which", return_value="/usr/bin/kubectl"),
            patch("verify.v.find_results_dirs", return_value=[Path("/results")]),
            patch("verify.v.MetricsSummary.load", return_value=metrics),
            patch("verify.v.get_model_pods", return_value=["model"]),
            patch("verify.is_lmcache_mp", return_value=mp),
            patch("verify.v.kubectl", return_value="pod/cache"),
            patch("verify.v.get_vllm_version", return_value=(0, 30, 0)),
            patch("verify.check_lmcache_mp_stores", return_value=[
                verify.v.Check("MP stores", True),
            ]) as mp_stores,
            patch("verify.check_pvc_is_bound", return_value=verify.v.Check("PVC bound", True)) as pvc_bound,
            patch("verify.check_pvc_has_data", return_value=verify.v.Check("PVC data", True)) as pvc_data,
            patch("verify.v.verify_checks", side_effect=lambda env, metrics, checks: all(c.passed for c in checks)),
            patch("sys.stdout", io.StringIO()),
        ):
            result = verify.main()
        return result, metrics, mp_stores, pvc_bound, pvc_data

    def test_mp_cpu_uses_server_metrics(self):
        result, metrics, mp_stores, pvc_bound, pvc_data = self.run_main(mp=True, storage=False)
        self.assertEqual(result, 0)
        mp_stores.assert_called_once_with("ns", ["cache"], False)
        metrics.check_per_pod.assert_not_called()
        pvc_bound.assert_not_called()
        pvc_data.assert_not_called()

    def test_mp_filesystem_inspects_cache_server(self):
        result, metrics, mp_stores, pvc_bound, pvc_data = self.run_main(mp=True, storage=True)
        self.assertEqual(result, 0)
        mp_stores.assert_called_once_with("ns", ["cache"], True)
        metrics.check_per_pod.assert_not_called()
        pvc_bound.assert_called_once_with("ns", "cache")
        pvc_data.assert_called_once_with("ns", "cache", verify.MP_KV_CACHE_DIR)

    def test_native_cpu_retains_vllm_metrics(self):
        result, metrics, mp_stores, pvc_bound, pvc_data = self.run_main(mp=False, storage=False)
        self.assertEqual(result, 0)
        mp_stores.assert_not_called()
        metrics.check_per_pod.assert_called_once_with(
            "vllm:kv_offload_store_bytes_total", "max", ">", 0.0,
        )
        pvc_bound.assert_not_called()
        pvc_data.assert_not_called()

    def test_native_filesystem_inspects_model_pod(self):
        result, metrics, mp_stores, pvc_bound, pvc_data = self.run_main(mp=False, storage=True)
        self.assertEqual(result, 0)
        mp_stores.assert_not_called()
        pvc_bound.assert_called_once_with("ns", "model")
        pvc_data.assert_called_once_with("ns", "model")


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
