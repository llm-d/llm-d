"""Unit tests for verify_helpers.

Run from this directory:
    python3 -m unittest test_verify_helpers.py -v
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import verify_helpers as v


class TestOps(unittest.TestCase):
    def test_all_operators(self):
        self.assertTrue(v.OPS["<="](1, 2))
        self.assertTrue(v.OPS["<="](2, 2))
        self.assertFalse(v.OPS["<="](3, 2))
        self.assertTrue(v.OPS[">="](3, 2))
        self.assertTrue(v.OPS["<"](1, 2))
        self.assertFalse(v.OPS["<"](2, 2))
        self.assertTrue(v.OPS[">"](3, 2))
        self.assertTrue(v.OPS["=="](2, 2))
        self.assertFalse(v.OPS["=="](2, 3))


class TestCheck(unittest.TestCase):
    def test_default_detail(self):
        c = v.Check(name="x", passed=True)
        self.assertEqual(c.detail, "")

    def test_full(self):
        c = v.Check(name="x", passed=False, detail="1 > 0")
        self.assertEqual(c.name, "x")
        self.assertFalse(c.passed)
        self.assertEqual(c.detail, "1 > 0")


class TestWorkflowEnv(unittest.TestCase):
    def test_defaults_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            env = v.workflow_env()
        self.assertEqual(env["workspace"], "")
        self.assertEqual(env["namespace"], "")
        self.assertEqual(env["scenario"], "<unknown>")
        self.assertEqual(env["run_id"], "")

    def test_reads_all_vars(self):
        e = {
            "LLMDBENCH_WORKSPACE": "/ws",
            "LLMDBENCH_CICD_NS": "ns",
            "LLMDBENCH_CICD_SCENARIO": "sc",
            "LLMDBENCH_CICD_WORKLOAD": "wl",
            "LLMDBENCH_CICD_HARNESS": "h",
            "LLMDBENCH_CICD_DETECTED_MODEL": "m",
            "GITHUB_RUN_ID": "1234",
        }
        with patch.dict(os.environ, e, clear=True):
            env = v.workflow_env()
        self.assertEqual(env["workspace"], "/ws")
        self.assertEqual(env["namespace"], "ns")
        self.assertEqual(env["scenario"], "sc")
        self.assertEqual(env["workload"], "wl")
        self.assertEqual(env["harness"], "h")
        self.assertEqual(env["model"], "m")
        self.assertEqual(env["run_id"], "1234")


class TestFindResultsDirs(unittest.TestCase):
    """Tests match the (workspace, namespace) signature and yaml-namespace
    matching semantics: only files under `<ws>/*/results/<exp>/run_metadata.yaml`
    whose `namespace` field equals the passed namespace are candidates; the
    newest metadata mtime wins, and all experiments under that run's
    `results/` dir are returned."""

    @staticmethod
    def _make_run(ws: Path, ts_name: str, exp_name: str, namespace: str,
                  mtime: float | None = None) -> Path:
        exp = ws / ts_name / "results" / exp_name
        exp.mkdir(parents=True)
        meta = exp / "run_metadata.yaml"
        meta.write_text(f'harness_name: "vllm-benchmark"\nnamespace: "{namespace}"\n')
        if mtime is not None:
            os.utime(meta, (mtime, mtime))
        return exp

    def test_empty_workspace_returns_none(self):
        with TemporaryDirectory() as td, patch("sys.stderr", io.StringIO()):
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                self._make_run(Path("."), "user-1", "exp", "ns")
                self.assertIsNone(v.find_results_dirs("", "ns"))
            finally:
                os.chdir(old_cwd)

    def test_empty_namespace_returns_none(self):
        with TemporaryDirectory() as td, patch("sys.stderr", io.StringIO()):
            self.assertIsNone(v.find_results_dirs(td, ""))

    def test_no_results_dir_anywhere(self):
        with TemporaryDirectory() as td, patch("sys.stderr", io.StringIO()):
            self.assertIsNone(v.find_results_dirs(td, "ns"))

    def test_happy_path(self):
        with TemporaryDirectory() as td:
            exp = self._make_run(Path(td), "user-20260101-120000-000", "exp1", "ns-A")
            dirs = v.find_results_dirs(td, "ns-A")
            self.assertIsNotNone(dirs)
            self.assertEqual(dirs, [exp])

    def test_namespace_mismatch_returns_none(self):
        with TemporaryDirectory() as td, patch("sys.stderr", io.StringIO()):
            self._make_run(Path(td), "user-1", "exp", "ns-A")
            self.assertIsNone(v.find_results_dirs(td, "ns-B"))

    def test_results_without_run_metadata_returns_none(self):
        # A results/exp dir with no metadata file is not a candidate.
        with TemporaryDirectory() as td, patch("sys.stderr", io.StringIO()):
            (Path(td) / "user-1" / "results" / "exp").mkdir(parents=True)
            self.assertIsNone(v.find_results_dirs(td, "ns-A"))

    def test_concurrent_runs_isolated_by_namespace(self):
        # Two concurrent runs, different namespaces. Each verify picks its own,
        # regardless of which was created more recently.
        with TemporaryDirectory() as td:
            ws = Path(td)
            exp_a = self._make_run(ws, "user-1", "exp", "ns-A", mtime=1000)
            exp_b = self._make_run(ws, "user-2", "exp", "ns-B", mtime=2000)
            self.assertEqual(v.find_results_dirs(td, "ns-A"), [exp_a])
            self.assertEqual(v.find_results_dirs(td, "ns-B"), [exp_b])

    def test_sequential_same_namespace_newest_wins(self):
        # Two runs reusing the same namespace back-to-back: newest metadata
        # mtime wins.
        with TemporaryDirectory() as td:
            ws = Path(td)
            self._make_run(ws, "user-1", "exp", "ns-A", mtime=500)
            new_exp = self._make_run(ws, "user-2", "exp", "ns-A", mtime=1500)
            self.assertEqual(v.find_results_dirs(td, "ns-A"), [new_exp])

    def test_multiple_experiments_in_one_run_returned_together(self):
        # A single `llmdbenchmark run` can produce multiple <exp>/ subdirs.
        # All experiments sharing the winning run's results/ dir come back.
        with TemporaryDirectory() as td:
            ws = Path(td)
            exp0 = self._make_run(ws, "user-1", "exp0", "ns-A", mtime=1000)
            exp1 = self._make_run(ws, "user-1", "exp1", "ns-A", mtime=1100)
            dirs = v.find_results_dirs(td, "ns-A")
            self.assertEqual(sorted(d.name for d in dirs), ["exp0", "exp1"])
            self.assertTrue(all(d.parent.name == "results" for d in dirs))
            self.assertEqual({d.parent for d in dirs}, {exp0.parent})

    def test_stray_metadata_outside_results_ignored(self):
        # A run_metadata.yaml at the wrong depth (grandparent != "results")
        # must be ignored, even if it has the matching namespace AND newest
        # mtime.
        with TemporaryDirectory() as td:
            ws = Path(td)
            good = self._make_run(ws, "user-1", "exp", "ns-A", mtime=1000)
            stray = ws / "stray"
            stray.mkdir()
            (stray / "run_metadata.yaml").write_text('namespace: "ns-A"\n')
            os.utime(stray / "run_metadata.yaml", (9999, 9999))
            self.assertEqual(v.find_results_dirs(td, "ns-A"), [good])

    def test_malformed_yaml_skipped(self):
        with TemporaryDirectory() as td:
            ws = Path(td)
            good = self._make_run(ws, "user-1", "exp", "ns-A", mtime=1000)
            bad = ws / "user-bad" / "results" / "exp"
            bad.mkdir(parents=True)
            (bad / "run_metadata.yaml").write_text("this: is: not: valid: [[[")
            self.assertEqual(v.find_results_dirs(td, "ns-A"), [good])


class TestGetVllmVersion(unittest.TestCase):
    @patch("verify_helpers.kubectl")
    def test_parses_semver(self, mock_kubectl):
        mock_kubectl.return_value = "0.24.0"
        self.assertEqual(v.get_vllm_version("ns", "pod"), (0, 24, 0))

    @patch("verify_helpers.kubectl")
    def test_parses_local_version_tag(self, mock_kubectl):
        # PEP 440 local version — leading digit run of the last segment is kept.
        mock_kubectl.return_value = "0.24.0+cu121"
        self.assertEqual(v.get_vllm_version("ns", "pod"), (0, 24, 0))

    @patch("verify_helpers.kubectl")
    def test_parses_rc_suffix(self, mock_kubectl):
        # `0rc1` -> leading digit `0` is captured before the parse stops.
        mock_kubectl.return_value = "0.24.0rc1"
        self.assertEqual(v.get_vllm_version("ns", "pod"), (0, 24, 0))

    @patch("verify_helpers.kubectl")
    def test_stops_at_non_digit_segment(self, mock_kubectl):
        # First non-digit-led segment stops the parse entirely.
        mock_kubectl.return_value = "0.24.dev0"
        self.assertEqual(v.get_vllm_version("ns", "pod"), (0, 24))

    @patch("verify_helpers.kubectl")
    def test_empty_output_returns_none(self, mock_kubectl):
        mock_kubectl.return_value = ""
        self.assertIsNone(v.get_vllm_version("ns", "pod"))

    @patch("verify_helpers.kubectl")
    def test_leading_non_digit_returns_none(self, mock_kubectl):
        mock_kubectl.return_value = "notaversion"
        self.assertIsNone(v.get_vllm_version("ns", "pod"))


class TestMetricsSummary(unittest.TestCase):
    def _raw(self):
        return {
            "_aggregated": {"metrics": {"m1": {"mean": 1.0, "p99": 2.0, "count": 10}}},
            "_info": {"status": "ok"},
            "pod-a": {"metrics": {"m1": {"mean": 1.2, "max": 3.0}}},
            "pod-b": {"metrics": {"m1": {"mean": 0.9, "max": 2.5}}},
        }

    def test_views(self):
        ms = v.MetricsSummary(self._raw())
        self.assertIn("m1", ms.aggregated)
        self.assertIn("pod-a", ms.per_pod)
        self.assertIn("pod-b", ms.per_pod)
        self.assertNotIn("_aggregated", ms.per_pod)
        self.assertNotIn("_info", ms.per_pod)
        self.assertEqual(ms.pod_count, 2)
        self.assertEqual(ms.metric_count, 1)

    def test_missing_aggregated_defaults_to_empty(self):
        ms = v.MetricsSummary({})
        self.assertEqual(ms.aggregated, {})
        self.assertEqual(ms.per_pod, {})
        self.assertEqual(ms.pod_count, 0)
        self.assertEqual(ms.metric_count, 0)

    def test_non_dict_top_level_values_ignored(self):
        # Scalars at the top level (e.g. schema_version) must not raise.
        raw = {
            "_aggregated": {"metrics": {}},
            "schema_version": 3,
            "note": "some string",
            "pod-a": {"metrics": {"m1": {"max": 1.0}}},
        }
        ms = v.MetricsSummary(raw)
        self.assertEqual(ms.pod_count, 1)
        self.assertIn("pod-a", ms.per_pod)
        self.assertNotIn("schema_version", ms.per_pod)

    def test_load_missing_file(self):
        with TemporaryDirectory() as td, patch("sys.stderr", io.StringIO()):
            self.assertIsNone(v.MetricsSummary.load(Path(td)))

    def test_load_no_data(self):
        with TemporaryDirectory() as td, patch("sys.stderr", io.StringIO()):
            p = Path(td) / "metrics" / "processed"
            p.mkdir(parents=True)
            (p / "metrics_summary.json").write_text(
                json.dumps({"_info": {"status": "no_data", "message": "nope"}})
            )
            self.assertIsNone(v.MetricsSummary.load(Path(td)))

    def test_load_malformed_json(self):
        with TemporaryDirectory() as td, patch("sys.stderr", io.StringIO()):
            p = Path(td) / "metrics" / "processed"
            p.mkdir(parents=True)
            (p / "metrics_summary.json").write_text("not-json{")
            self.assertIsNone(v.MetricsSummary.load(Path(td)))

    def test_load_happy(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "metrics" / "processed"
            p.mkdir(parents=True)
            (p / "metrics_summary.json").write_text(json.dumps(self._raw()))
            ms = v.MetricsSummary.load(Path(td))
            self.assertIsNotNone(ms)
            self.assertEqual(ms.metric_count, 1)
            self.assertEqual(ms.pod_count, 2)


class TestCheckAggregated(unittest.TestCase):
    def _ms(self):
        return v.MetricsSummary({
            "_aggregated": {"metrics": {"m1": {"p99": 1.85, "mean": 1.0}}},
        })

    def test_pass(self):
        c = self._ms().check_aggregated("m1", "p99", "<=", 2.0)
        self.assertTrue(c.passed)
        self.assertIn("1.85", c.detail)
        self.assertIn("<=", c.detail)

    def test_fail(self):
        c = self._ms().check_aggregated("m1", "p99", "<=", 1.0)
        self.assertFalse(c.passed)

    def test_unknown_op(self):
        c = self._ms().check_aggregated("m1", "p99", "??", 1.0)
        self.assertFalse(c.passed)
        self.assertIn("unknown op", c.detail)

    def test_missing_metric(self):
        c = self._ms().check_aggregated("mX", "p99", "<=", 1.0)
        self.assertFalse(c.passed)
        self.assertIn("not in _aggregated.metrics", c.detail)

    def test_missing_aggregate(self):
        c = self._ms().check_aggregated("m1", "p50", "<=", 1.0)
        self.assertFalse(c.passed)
        self.assertIn("missing", c.detail)


class TestCheckPerPod(unittest.TestCase):
    def _ms(self):
        return v.MetricsSummary({
            "_aggregated": {"metrics": {}},
            "pod-a": {"metrics": {"m1": {"max": 5.0}}},
            "pod-b": {"metrics": {"m1": {"max": 3.0}}},
            "pod-c": {"metrics": {}},  # pod without this metric
        })

    def test_max_reducer_pass(self):
        c = self._ms().check_per_pod("m1", "max", ">", 4.0)
        self.assertTrue(c.passed)
        self.assertIn("5", c.detail)

    def test_min_reducer_fail(self):
        c = self._ms().check_per_pod("m1", "max", ">", 4.0, combine=min)
        self.assertFalse(c.passed)

    def test_no_pod_has_metric(self):
        c = self._ms().check_per_pod("missing", "max", ">", 0.0)
        self.assertFalse(c.passed)
        self.assertIn("not reported by any pod", c.detail)

    def test_unknown_op(self):
        c = self._ms().check_per_pod("m1", "max", "??", 0.0)
        self.assertFalse(c.passed)
        self.assertIn("unknown op", c.detail)

    def test_name_includes_reducer(self):
        c = self._ms().check_per_pod("m1", "max", ">", 0.0)
        self.assertIn("max", c.name)


class TestPrintChecksTable(unittest.TestCase):
    def test_empty(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            v.print_checks_table([])
        self.assertIn("no checks", buf.getvalue())

    def test_rows(self):
        checks = [
            v.Check("check-a", True, detail="1 < 2"),
            v.Check("check-b", False, detail="3 > 2"),
        ]
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            v.print_checks_table(checks)
        out = buf.getvalue()
        self.assertIn("PASS", out)
        self.assertIn("FAIL", out)
        self.assertIn("check-a", out)
        self.assertIn("check-b", out)


class TestVerifyChecks(unittest.TestCase):
    def _env(self):
        return {"scenario": "sc", "workload": "wl"}

    def _metrics(self):
        return v.MetricsSummary({"_aggregated": {"metrics": {}}})

    def test_all_pass(self):
        checks = [v.Check("a", True), v.Check("b", True)]
        with patch("sys.stdout", io.StringIO()):
            passed = v.verify_checks(self._env(), self._metrics(), checks)
        self.assertTrue(passed)

    def test_any_fail(self):
        checks = [v.Check("a", True), v.Check("b", False)]
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            passed = v.verify_checks(self._env(), self._metrics(), checks)
        self.assertFalse(passed)

    def test_empty_passes(self):
        with patch("sys.stdout", io.StringIO()):
            passed = v.verify_checks(self._env(), self._metrics(), [])
        self.assertTrue(passed)


class TestKubectl(unittest.TestCase):
    @patch("verify_helpers.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)
        self.assertEqual(v.kubectl(["get", "pod"]), "hello")

    @patch("verify_helpers.subprocess.run")
    def test_nonzero_exit_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="Error from server", returncode=1)
        with patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(v.kubectl(["get", "pod"]), "")
        # Error path should have logged something to stderr.
        self.assertIn("kubectl", err.getvalue())

    @patch("verify_helpers.subprocess.run")
    def test_timeout_returns_empty(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["kubectl"], timeout=1)
        with patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(v.kubectl(["get", "pod"]), "")
        self.assertIn("timed out", err.getvalue())

    @patch("verify_helpers.subprocess.run")
    def test_generic_exception_returns_empty(self, mock_run):
        mock_run.side_effect = FileNotFoundError("no kubectl")
        with patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(v.kubectl(["get", "pod"]), "")
        self.assertIn("kubectl", err.getvalue())


class TestGetModelPods(unittest.TestCase):
    @patch("verify_helpers.kubectl")
    def test_decode_prefill_match(self, mock_kubectl):
        mock_kubectl.return_value = "pod/gpu-decode-a\npod/gpu-prefill-b"
        self.assertEqual(v.get_model_pods("ns"), ["gpu-decode-a", "gpu-prefill-b"])
        self.assertEqual(mock_kubectl.call_count, 1)

    @patch("verify_helpers.kubectl")
    def test_standalone_fallback(self, mock_kubectl):
        mock_kubectl.side_effect = ["", "pod/standalone-a"]
        self.assertEqual(v.get_model_pods("ns"), ["standalone-a"])
        self.assertEqual(mock_kubectl.call_count, 2)

    @patch("verify_helpers.kubectl")
    def test_no_pods_at_all(self, mock_kubectl):
        mock_kubectl.return_value = ""
        self.assertEqual(v.get_model_pods("ns"), [])


if __name__ == "__main__":
    unittest.main()
