"""Unit tests for analysis.py and experiment.py (stdlib unittest, no cluster).

Run from anywhere:
    python3 -m unittest discover -s .github/scripts/e2e/async-multitenant -p 'test_*.py' -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analysis as a  # noqa: E402
import experiment as x  # noqa: E402


def rec(start, e2e, ttft=0.1, tokens=128, ok=True):
    return a.Record(start=start, end=start + e2e, ttft=ttft, output_tokens=tokens, ok=ok,
                    error=None if ok else "boom")


def closed_loop(k: int, latency: float, t0: float, n_per_worker: int, ttft: float = 0.1):
    """k workers, each issuing back-to-back requests of fixed latency."""
    out = []
    for w in range(k):
        t = t0 + w * 0.01
        for _ in range(n_per_worker):
            out.append(rec(t, latency, ttft))
            t += latency
    return out


def lifecycle(start, end, ok=True, tokens=128, token_times=None, chunk_times=None):
    item = {
        "stage_id": 0, "scheduled_time": start, "start_time": start, "end_time": end,
        "request_data": "{}", "response_data": None,
        "info": {"request_metrics": {"text": {"input_tokens": 200}},
                 "response_metrics": {"output_tokens": tokens}},
        "error": None if ok else {"error_type": "timeout", "error_msg": "client timeout"},
    }
    if token_times is not None:
        item["info"]["response_metrics"]["output_token_times"] = token_times
    if chunk_times is not None:
        item["info"]["response_metrics"]["chunk_times"] = chunk_times
    return item


class PercentileTests(unittest.TestCase):
    def test_single_and_bounds(self):
        self.assertEqual(a.percentile([3.0], 0.9), 3.0)
        self.assertEqual(a.percentile([1.0, 2.0, 3.0, 4.0], 0.0), 1.0)
        self.assertEqual(a.percentile([1.0, 2.0, 3.0, 4.0], 1.0), 4.0)

    def test_interpolates(self):
        self.assertAlmostEqual(a.percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertAlmostEqual(a.percentile([10.0, 20.0, 30.0, 40.0, 50.0], 0.9), 46.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            a.percentile([], 0.5)


class ToleranceTests(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(a.tolerance("RPS_FACTOR", {}), 0.85)
        self.assertEqual(a.tolerance("RPS_FACTOR", {}, level=20), 0.75)      # built-in per-level default
        self.assertEqual(a.tolerance("RPS_FACTOR", {}, level=80), 0.85)
        self.assertEqual(a.tolerance("RPS_FACTOR", {"AMT_RPS_FACTOR": "0.5"}, level=20), 0.5)  # global env beats built-in
        self.assertEqual(a.tolerance("RPS_FACTOR", {"AMT_RPS_FACTOR": "0.5", "AMT_LEVEL_20_RPS_FACTOR": "0.6"}, level=20), 0.6)
        self.assertEqual(a.tolerance("RPS_FACTOR", {"AMT_LEVEL_20_RPS_FACTOR": "0.6"}, level=80), 0.85)
        self.assertEqual(a.tolerance("TTFT_ABS", {"AMT_TTFT_ABS": ""}), 0.3)  # empty string means unset

    def test_unknown_key(self):
        with self.assertRaises(KeyError):
            a.tolerance("NOPE", {})


class RecordParsingTests(unittest.TestCase):
    def test_streamed_record(self):
        r = a.record_from_lifecycle(lifecycle(100.0, 102.0, token_times=[100.25, 100.5]))
        self.assertIsNotNone(r)
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.e2e, 2.0)
        self.assertAlmostEqual(r.ttft, 0.25)
        self.assertEqual(r.output_tokens, 128)

    def test_chunk_times_fallback_and_error(self):
        r = a.record_from_lifecycle(lifecycle(100.0, 101.0, ok=False, chunk_times=[100.4]))
        self.assertFalse(r.ok)
        self.assertIn("timeout", r.error)
        self.assertAlmostEqual(r.ttft, 0.4)

    def test_ttft_outside_e2e_discarded(self):
        r = a.record_from_lifecycle(lifecycle(100.0, 101.0, token_times=[5.0]))  # different clock
        self.assertIsNone(r.ttft)
        r = a.record_from_lifecycle(lifecycle(100.0, 101.0, token_times=[]))
        self.assertIsNone(r.ttft)

    def test_malformed(self):
        self.assertIsNone(a.record_from_lifecycle({"start_time": "x", "end_time": 1}))
        self.assertIsNone(a.record_from_lifecycle({"start_time": 5.0, "end_time": 4.0}))
        self.assertIsNone(a.record_from_lifecycle({}))

    def test_parse_per_request_shapes(self):
        items = [lifecycle(1.0, 2.0), lifecycle(2.0, 3.0), "junk"]
        self.assertEqual(len(a.parse_per_request(items)), 2)
        self.assertEqual(len(a.parse_per_request({"metrics": items})), 2)
        self.assertEqual(len(a.parse_per_request({"requests": items})), 2)
        self.assertEqual(a.parse_per_request({"nothing": 1}), [])
        self.assertEqual(a.parse_per_request(None), [])

    def test_load_treatment_anchors_monotonic_clock_on_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "exp"
            (d / "reports").mkdir(parents=True)
            (d / "reports" / a.PER_REQUEST_FILENAME).write_text(json.dumps([lifecycle(10.0, 12.0), lifecycle(12.0, 14.0)]))
            (d / "run_metadata.yaml").write_text(
                'harness_start: "2026-09-25T10:00:00+00:00"\nharness_stop: "2026-09-25T10:01:00+00:00"\nnamespace: "ns"\n')
            recs = a.load_treatment(d)
            self.assertEqual(len(recs), 2)
            stop_epoch = a._iso_to_epoch("2026-09-25T10:01:00+00:00")
            self.assertAlmostEqual(max(r.end for r in recs), stop_epoch)   # pinned to harness_stop
            self.assertAlmostEqual(recs[1].start - recs[0].start, 2.0)
            self.assertAlmostEqual(recs[0].e2e, 2.0)

    def test_anchor_falls_back_to_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / a.PER_REQUEST_FILENAME).write_text(json.dumps([lifecycle(10.0, 12.0)]))
            (d / "run_metadata.yaml").write_text('harness_start: "2026-09-25T10:00:00+00:00"\n')
            recs = a.load_treatment(d)
            self.assertAlmostEqual(recs[0].start, a._iso_to_epoch("2026-09-25T10:00:00+00:00"))

    def test_server_usage_preferred_for_tokens(self):
        item = lifecycle(1.0, 2.0, tokens=126)
        item["info"]["response_metrics"]["server_usage"] = {"completion_tokens": 128, "prompt_tokens": 11}
        self.assertEqual(a.record_from_lifecycle(item).output_tokens, 128)
        self.assertEqual(a.record_from_lifecycle(lifecycle(1.0, 2.0, tokens=126)).output_tokens, 126)

    def test_treatment_name(self):
        stems = ["guide_async-multitenant_1", "guide_async-multitenant_2"]
        meta = {"experiment_id": "inference-perf-mixed_20_rt-1790370735-1xj6h4",
                "harness_workload": "guide_async-multitenant_1-mixed_20_rt.yaml", "description_text": ""}
        self.assertEqual(a.treatment_name(meta, stems), "mixed_20_rt")
        self.assertEqual(a.treatment_name({"experiment_id": "inference-perf-async_only-1790370232-09ucm1"}, stems), "async_only")
        self.assertEqual(a.treatment_name({"description_text": "custom"}, stems), "custom")
        self.assertEqual(a.treatment_name({"harness_workload": "other.yaml"}, stems), "")

    def test_load_treatment_epoch_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / a.PER_REQUEST_FILENAME).write_text(json.dumps([lifecycle(1.7e9, 1.7e9 + 2)]))
            recs = a.load_treatment(d)
            self.assertAlmostEqual(recs[0].start, 1.7e9)

    def test_read_flat_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "run_metadata.yaml"
            p.write_text('a: "x"\nb: y\n# comment\nc: "q: r"\n')
            self.assertEqual(a.read_flat_yaml(p), {"a": "x", "b": "y", "c": "q: r"})


class WindowTests(unittest.TestCase):
    def test_baseline_window_trims_warmup(self):
        recs = closed_loop(2, 1.0, 1000.0, 10)
        w = a.baseline_window(recs, 3.0)
        self.assertAlmostEqual(w[0], 1003.0)
        self.assertAlmostEqual(w[1], max(r.end for r in recs))

    def test_overlap_window(self):
        rt = closed_loop(2, 1.0, 1000.0, 20)      # 1000 -> ~1020
        asy = closed_loop(4, 2.0, 1005.0, 20)     # 1005 -> ~1045
        w = a.overlap_window(rt, asy, 2.0)
        self.assertAlmostEqual(w[0], 1007.0)
        self.assertAlmostEqual(w[1], max(r.end for r in rt))
        self.assertIsNone(a.overlap_window(rt, [], 2.0))
        self.assertIsNone(a.overlap_window(rt, closed_loop(1, 1.0, 5000.0, 2), 2.0))

    def test_in_window_drops_straddlers(self):
        recs = [rec(0.0, 1.0), rec(0.5, 1.0), rec(1.0, 1.0), rec(1.9, 1.0)]
        inside = a.in_window(recs, (0.5, 2.0))
        self.assertEqual([r.start for r in inside], [0.5, 1.0])


class SummaryTests(unittest.TestCase):
    def test_closed_loop_identity(self):
        k, latency = 4, 2.0
        recs = closed_loop(k, latency, 0.0, 50, ttft=0.2)
        s = a.summarize(recs, a.baseline_window(recs, 10.0))
        self.assertGreater(s.count, 100)
        self.assertEqual(s.errors, 0)
        self.assertAlmostEqual(s.e2e_p50, latency)
        self.assertAlmostEqual(s.ttft_p90, 0.2)
        self.assertAlmostEqual(s.rps, k / latency, delta=0.1)          # req/s = k / mean(E2E)
        self.assertAlmostEqual(s.output_tps, 128 * k / latency, delta=13)
        self.assertEqual((s.output_tokens_min, s.output_tokens_max), (128, 128))

    def test_errors_and_empty(self):
        recs = [rec(0.0, 1.0), rec(0.0, 1.0, ok=False)]
        s = a.summarize(recs, (0.0, 5.0))
        self.assertEqual((s.count, s.errors), (2, 1))
        self.assertEqual(a.summarize(recs, None).count, 0)
        self.assertEqual(a.summarize([], (0.0, 1.0)).count, 0)


def summary(ttft=0.1, e2e=2.0, rps=4.0, tps=512.0, count=100, errors=0, tmin=128, tmax=128):
    return a.Summary(count=count, errors=errors, window_s=60.0, ttft_p50=ttft, ttft_p90=ttft * 1.2, ttft_p95=ttft * 1.3,
                     ttft_mean=ttft, e2e_p50=e2e, e2e_p90=e2e * 1.1, e2e_p95=e2e * 1.2, e2e_mean=e2e,
                     rps=rps, output_tps=tps, output_tokens_min=tmin, output_tokens_max=tmax)


class CompareLevelTests(unittest.TestCase):
    def names(self, checks):
        return {c.name: c for c in checks}

    def test_all_pass_within_bounds(self):
        base = summary()
        mixed = summary(ttft=0.3, e2e=2.2, rps=3.6, tps=460.0)
        checks = self.names(a.compare_level(80, base, mixed, {}, async_successes=5))
        self.assertTrue(all(c.passed for c in checks.values()), [c for c in checks.values() if not c.passed])
        self.assertNotIn("L80 async uses slack", checks)  # only asserted at L<=20

    def test_no_overlap_is_a_single_clear_failure(self):
        checks = a.compare_level(80, summary(), a.Summary(), {})
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0].passed)
        self.assertIn("no common window", checks[0].detail)

    def test_boundaries_flip(self):
        base = summary()
        # TTFT p50 bound = 0.1*1.5 + 0.3 = 0.45
        ok = self.names(a.compare_level(80, base, summary(ttft=0.449), {}))
        bad = self.names(a.compare_level(80, base, summary(ttft=0.451), {}))
        self.assertTrue(ok["L80 ttft p50"].passed)
        self.assertFalse(bad["L80 ttft p50"].passed)
        # rps bound = 4.0*0.85 = 3.4 at L80, 4.0*0.75 = 3.0 at L20
        self.assertFalse(self.names(a.compare_level(80, base, summary(rps=3.39), {}))["L80 req/s"].passed)
        self.assertTrue(self.names(a.compare_level(20, base, summary(rps=3.39), {}))["L20 req/s"].passed)
        self.assertTrue(self.names(a.compare_level(80, base, summary(rps=3.39), {"AMT_LEVEL_80_RPS_FACTOR": "0.8"}))["L80 req/s"].passed)

    def test_errors_samples_and_slack(self):
        base = summary()
        dispatching = self.names(a.compare_level(20, base, summary(), {}, async_successes=0, async_dispatch_rps=3.5))
        self.assertTrue(dispatching["L20 async uses slack"].passed)  # clients timed out, but the router dispatched
        self.assertIn("3.50 async req/s", dispatching["L20 async uses slack"].detail)
        idle = self.names(a.compare_level(20, base, summary(), {}, async_successes=4, async_dispatch_rps=0.0))
        self.assertFalse(idle["L20 async uses slack"].passed)
        checks = self.names(a.compare_level(20, base, summary(errors=1, count=10), {}, async_successes=0))
        self.assertFalse(checks["L20 realtime errors"].passed)
        self.assertFalse(checks["L20 samples"].passed)
        self.assertFalse(checks["L20 async uses slack"].passed)
        self.assertTrue(self.names(a.compare_level(20, base, summary(), {}, async_successes=1))["L20 async uses slack"].passed)

    def test_missing_ttft_fails_loudly(self):
        mixed = summary()
        mixed.ttft_p50 = None
        c = self.names(a.compare_level(90, summary(), mixed, {}))["L90 ttft p50"]
        self.assertFalse(c.passed)
        self.assertIn("missing", c.detail)

    def test_streaming_sanity(self):
        self.assertTrue(a.streaming_sanity(80, summary(ttft=0.1, e2e=2.0), {}).passed)
        self.assertFalse(a.streaming_sanity(80, summary(ttft=1.9, e2e=2.0), {}).passed)
        self.assertFalse(a.streaming_sanity(80, a.Summary(), {}).passed)


def sat(mx, p50, n=20):
    return a.SaturationSummary(count=n, running_max=mx, running_p50=p50, saturation_max=mx / 10, saturation_p50=p50 / 10)


class AsyncOnlyTests(unittest.TestCase):
    def test_pass(self):
        checks = {c.name: c for c in a.async_only_checks(sat(10, 9.5), summary(rps=0.5), summary(rps=5.0), 10, {}, epp_dispatch_rps=4.0)}
        self.assertTrue(all(c.passed for c in checks.values()), [c for c in checks.values() if not c.passed])
        self.assertIn("EPP dispatched 4.00", checks["async-only dispatch rate"].detail)

    def test_saturation_and_rate_bounds(self):
        checks = {c.name: c for c in a.async_only_checks(sat(8.9, 7.9), summary(rps=3.4), summary(rps=5.0), 10, {}, epp_dispatch_rps=3.4)}
        self.assertFalse(checks["async-only pool saturation (max)"].passed)
        self.assertFalse(checks["async-only pool saturation (p50)"].passed)
        self.assertFalse(checks["async-only dispatch rate"].passed)
        missing = {c.name: c for c in a.async_only_checks(sat(10, 10), summary(), summary(rps=5.0), 10, {}, None)}
        self.assertFalse(missing["async-only dispatch rate"].passed)

    def test_holdback_ceiling_scales_the_bounds(self):
        # Holdback admits overflow-batch to half of capacity: 6 running (p50 5) and
        # 2.0 req/s against baseline(100) 5.0 is "async fills its share".
        checks = {c.name: c for c in a.async_only_checks(sat(6, 5), summary(rps=1.0), summary(rps=5.0), 10, {},
                                                           epp_dispatch_rps=2.0, ceiling=0.5)}
        self.assertTrue(all(c.passed for c in checks.values()), [c for c in checks.values() if not c.passed])
        self.assertIn("holdback ceiling 0.5", checks["async-only pool saturation (max)"].detail)
        self.assertIn("x ceiling 0.5", checks["async-only dispatch rate"].detail)
        # The same numbers fail without holdback, where async alone must fill the pool.
        full = {c.name: c for c in a.async_only_checks(sat(6, 5), summary(rps=1.0), summary(rps=5.0), 10, {},
                                                         epp_dispatch_rps=2.0)}
        self.assertFalse(full["async-only pool saturation (max)"].passed)
        self.assertFalse(full["async-only dispatch rate"].passed)

    def test_dispatch_rate_from_counters(self):
        samples = [a.Sample(t=100 + i * 10, band_requests={-10: 50 + 4 * i, 30: 10 + i, 100: 7}) for i in range(5)]
        rate = a.dispatch_rate(samples, (100, 140), a.ASYNC_PRIORITIES)
        self.assertAlmostEqual(rate, (16 + 4) / 40)          # bands -10 and 30 grew 16 and 4 over 40 s
        self.assertIsNone(a.dispatch_rate(samples, (100, 105), a.ASYNC_PRIORITIES))   # one sample
        self.assertIsNone(a.dispatch_rate(samples, None, a.ASYNC_PRIORITIES))
        self.assertAlmostEqual(a.dispatch_rate(samples, (100, 140), [100]), 0.0)

    def test_missing_samples_and_tokens(self):
        checks = {c.name: c for c in a.async_only_checks(None, summary(tmin=100), None, 10, {})}
        self.assertFalse(checks["async-only pool saturation (max)"].passed)
        self.assertIn("no vLLM", checks["async-only pool saturation (max)"].detail)
        self.assertNotIn("async-only dispatch rate", checks)
        self.assertFalse(checks["async results honour ignore_eos"].passed)

    def test_no_completions(self):
        checks = {c.name: c for c in a.async_only_checks(sat(10, 10), a.Summary(count=5, errors=5), None, 10, {})}
        self.assertFalse(checks["async-only completions"].passed)
        self.assertNotIn("async results honour ignore_eos", checks)


VLLM_TEXT = """
vllm:num_requests_running{engine="0",model_name="Qwen/Qwen3-8B"} 7.0
vllm:num_requests_waiting{engine="0",model_name="Qwen/Qwen3-8B"} 2.0
"""
EPP_TEXT = """
llm_d_epp_flow_control_pool_saturation{inference_pool="llm-d-router",stage="decode"} 0.6
llm_d_epp_flow_control_pool_saturation{inference_pool="llm-d-router",stage="effective"} 0.7
llm_d_epp_flow_control_requests_total{inference_pool="llm-d-router",outcome="Dispatched",priority="-10"} 105
llm_d_epp_flow_control_requests_total{inference_pool="llm-d-router",outcome="Rejected",priority="-10"} 2
llm_d_epp_flow_control_requests_total{inference_pool="llm-d-router",outcome="Dispatched",priority="100"} 23
"""


class CapacityTests(unittest.TestCase):
    def test_bound_and_slack(self):
        self.assertTrue(a.capacity_check("x", sat(11, 10), 10, {}).passed)
        self.assertFalse(a.capacity_check("x", sat(12, 10), 10, {}).passed)
        self.assertTrue(a.capacity_check("x", sat(12, 10), 10, {"AMT_CAPACITY_SLACK": "2"}).passed)
        self.assertFalse(a.capacity_check("x", None, 10, {}).passed)
        self.assertIn("EPP saturation", a.capacity_check("x", sat(16, 12), 10, {}).detail)


class SampleTests(unittest.TestCase):
    def test_parse_sample(self):
        s = a.parse_sample(1000.0, VLLM_TEXT + VLLM_TEXT, EPP_TEXT)   # two model pods -> sums
        self.assertEqual((s.running, s.waiting), (14.0, 4.0))
        self.assertEqual(s.saturation, 0.7)                             # effective stage preferred
        self.assertEqual(s.band_requests, {-10: 107.0, 100: 23.0})
        empty = a.parse_sample(1.0, "", "")
        self.assertIsNone(empty.running)
        self.assertIsNone(empty.saturation)
        self.assertEqual(empty.band_requests, {})
        self.assertEqual(s.as_dict()["band_requests"], {"-10": 107.0, "100": 23.0})

    def test_saturation_summary_window(self):
        samples = [a.Sample(t=float(t), running=float(r), saturation=r / 10) for t, r in
                   [(0, 2), (5, 9), (10, 10), (15, 10), (20, 3)]]
        s = a.saturation_summary(samples, (5, 15))
        self.assertEqual((s.count, s.running_max, s.running_p50), (3, 10.0, 10.0))
        self.assertAlmostEqual(s.saturation_max, 1.0)
        self.assertEqual(a.saturation_summary(samples, None).count, 0)
        self.assertEqual(a.saturation_summary(samples, (100, 200)).count, 0)

    def test_band_counts_from_samples_and_merge(self):
        samples = [a.Sample(t=1, band_requests={100: 5, -10: 1}), a.Sample(t=2, band_requests={100: 9, 30: 2})]
        self.assertEqual(a.band_counts_from_samples(samples), {100: 9, -10: 1, 30: 2})
        merged = a.merge_band_counts({100: 9, 30: 2}, {100: 23, -10: 105})
        self.assertEqual(merged, {100: 23, 30: 2, -10: 105})

    def test_epoch_window_from_metadata(self):
        meta = {"harness_start": "2026-09-25T10:00:00+00:00", "harness_stop": "2026-09-25T10:02:00+00:00"}
        w = a.epoch_window_from_metadata(meta, 15)
        self.assertAlmostEqual(w[1] - w[0], 105.0)
        self.assertIsNone(a.epoch_window_from_metadata(meta, 200))
        self.assertIsNone(a.epoch_window_from_metadata({}, 0))


EXPOSITION = """
# HELP llm_d_epp_flow_control_request_queue_duration_seconds queue wait
# TYPE llm_d_epp_flow_control_request_queue_duration_seconds histogram
llm_d_epp_flow_control_request_queue_duration_seconds_bucket{fairness_id="realtime",priority="100",le="+Inf"} 40
llm_d_epp_flow_control_request_queue_duration_seconds_count{fairness_id="realtime",priority="100"} 40
llm_d_epp_flow_control_request_queue_duration_seconds_count{fairness_id="batch",priority="-10"} 12
llm_d_epp_flow_control_request_queue_duration_seconds_count{fairness_id="batch",priority="30"} 3
llm_d_epp_flow_control_request_queue_duration_seconds_sum{fairness_id="batch",priority="30"} 1.5e-01
llm_d_epp_flow_control_pool_saturation{inference_pool="llm-d-router"} 1
"""


class ExpositionTests(unittest.TestCase):
    def test_parse(self):
        samples = a.parse_exposition(EXPOSITION)
        names = {s[0] for s in samples}
        self.assertIn("llm_d_epp_flow_control_pool_saturation", names)
        sat = [s for s in samples if s[0] == "llm_d_epp_flow_control_pool_saturation"][0]
        self.assertEqual(sat[1], {"inference_pool": "llm-d-router"})
        self.assertEqual(sat[2], 1.0)

    def test_band_counts_and_checks(self):
        self.assertEqual(a.band_counts(EXPOSITION, a.QUEUE_DURATION_COUNT), {100: 40.0, -10: 12.0, 30: 3.0})
        self.assertEqual(a.band_counts(EPP_TEXT), {-10: 107.0, 100: 23.0})   # default: requests_total
        checks = a.band_checks({100: 40.0, -10: 12.0, 30: 3.0})
        self.assertTrue(all(c.passed for c in checks))
        checks = {c.name: c for c in a.band_checks({100: 40.0})}
        self.assertTrue(checks["realtime classified into band 100"].passed)
        self.assertFalse(checks["async classified into batch bands"].passed)
        self.assertFalse(any(c.passed for c in a.band_checks({})))


class MarkdownTests(unittest.TestCase):
    def test_render(self):
        report = {
            "capacity": 10,
            "levels": [a.level_table_row(20, 2, summary(), summary(ttft=0.2), 7),
                       a.level_table_row(100, 10, summary(), summary(), None)],
            "async_only": {"completions": 300, "window_s": 105.0, "rps": 2.9, "running_max": 10.0, "running_p50": 9.0},
            "checks": a.checks_to_dicts([a.Check("x", True, "d"), a.Check("y", False, "e")]),
        }
        md = a.render_markdown(report)
        self.assertIn("| 20 | 2 |", md)
        self.assertIn("| 100 | 10 |", md)
        self.assertIn("| FAIL | y | e |", md)
        self.assertIn("300 client completions", md)
        row = report["levels"][0]
        self.assertAlmostEqual(row["ttft_p50_ratio"], 2.0)
        self.assertNotIn("Informational", md)

    def test_render_observations_separately(self):
        report = {"capacity": 10, "levels": [], "checks": a.checks_to_dicts([a.Check("x", True, "d")]),
                  "observations": a.checks_to_dicts([a.Check("L20 pool held at capacity", False, "max running 14")])}
        md = a.render_markdown(report)
        self.assertIn("Informational (does not fail the run", md)
        self.assertIn("| OVER | L20 pool held at capacity | max running 14 |", md)
        self.assertNotIn("| FAIL | L20 pool held at capacity", md)


class CapacityEnforcementTests(unittest.TestCase):
    def test_capacity_is_informational_unless_enforced(self):
        self.assertFalse(a.capacity_enforced({}))
        self.assertFalse(a.capacity_enforced({"AMT_ENFORCE_CAPACITY": "0"}))
        for value in ("1", "true", "YES"):
            self.assertTrue(a.capacity_enforced({"AMT_ENFORCE_CAPACITY": value}))


class RerunPlanTests(unittest.TestCase):
    def setUp(self):
        self.base = closed_loop(2, 4.0, 0.0, 20)            # 40 records over ~80 s
        self.rt = closed_loop(2, 4.0, 1000.0, 20)           # mixed realtime, t=1000..1080
        self.asy = [rec(990.0 + i, 5.0) for i in range(120)]  # async active t=990..1115

    def records(self, **override):
        r = {"async_only": [rec(0, 5.0)], "baseline_20": self.base,
             "mixed_20_rt": self.rt, "mixed_20_async": self.asy}
        r.update(override)
        return r

    def test_valid_level_needs_no_rerun(self):
        self.assertEqual(a.rerun_plan([20], self.records(), {}), ([], False))

    def test_missing_treatment_reruns_level(self):
        self.assertEqual(a.rerun_plan([20], self.records(mixed_20_async=None), {}), ([20], False))
        r = self.records(); del r["baseline_20"]
        self.assertEqual(a.rerun_plan([20], r, {}), ([20], False))

    def test_members_without_overlap_rerun_level(self):
        late_async = [rec(1200.0 + i, 5.0) for i in range(60)]   # starts after realtime ended
        self.assertEqual(a.rerun_plan([20], self.records(mixed_20_async=late_async), {}), ([20], False))

    def test_short_overlap_below_min_samples_reruns_level(self):
        brief_async = [rec(1060.0 + i, 5.0) for i in range(20)]  # overlaps the last ~20 s only
        self.assertEqual(a.rerun_plan([20], self.records(mixed_20_async=brief_async), {}), ([20], False))
        self.assertEqual(a.rerun_plan([20], self.records(mixed_20_async=brief_async), {"AMT_MIN_SAMPLES": "1"}),
                         ([], False))

    def test_missing_async_only_is_reported(self):
        self.assertEqual(a.rerun_plan([20], self.records(async_only=None), {}), ([], True))
        self.assertEqual(a.rerun_plan([20], self.records(async_only=[]), {}), ([], True))


class MergeRerunTests(unittest.TestCase):
    OLD = {"async_only": "a0", "baseline_20": "b0", "mixed_20_async": "ma0", "mixed_20_rt": "mr0",
           "baseline_100": "B0", "mixed_100_async": "MA0", "mixed_100_rt": "MR0"}

    def test_complete_rerun_level_replaces_old_set(self):
        new = {"baseline_20": "b1", "mixed_20_async": "ma1", "mixed_20_rt": "mr1"}
        merged, notes = a.merge_rerun(self.OLD, new, [20], False)
        self.assertEqual([merged[k] for k in ("baseline_20", "mixed_20_async", "mixed_20_rt")], ["b1", "ma1", "mr1"])
        self.assertEqual(merged["baseline_100"], "B0")
        self.assertEqual(notes, [])

    def test_incomplete_rerun_level_keeps_old_set(self):
        # the async member hung in the re-run: never pair it with the old async member
        new = {"baseline_20": "b1", "mixed_20_rt": "mr1"}
        merged, notes = a.merge_rerun(self.OLD, new, [20], False)
        self.assertEqual([merged[k] for k in ("baseline_20", "mixed_20_async", "mixed_20_rt")], ["b0", "ma0", "mr0"])
        self.assertEqual(len(notes), 1)
        self.assertIn("mixed_20_async", notes[0])

    def test_async_only_rerun(self):
        merged, notes = a.merge_rerun(self.OLD, {"async_only": "a1"}, [], True)
        self.assertEqual(merged["async_only"], "a1")
        merged, notes = a.merge_rerun({k: v for k, v in self.OLD.items() if k != "async_only"}, {}, [], True)
        self.assertNotIn("async_only", merged)
        self.assertEqual(notes, ["async-only: re-run produced no results"])


class ModelFromArgsTests(unittest.TestCase):
    def test_model_from_args(self):
        self.assertEqual(a.model_from_args(["Qwen/Qwen3-8B", "--tensor-parallel-size=1"]), "Qwen/Qwen3-8B")
        self.assertEqual(a.model_from_args(["--model", "Qwen/Qwen3-8B", "--port", "8000"]), "Qwen/Qwen3-8B")
        self.assertEqual(a.model_from_args(["--model=Qwen/Qwen3-8B"]), "Qwen/Qwen3-8B")
        self.assertEqual(a.model_from_args(["--port", "8000"]), "")
        self.assertEqual(a.model_from_args([]), "")


class SettingsTests(unittest.TestCase):
    def test_parse_settings(self):
        text = """# comment
AMT_SERVICE_S=10
export AMT_LEVELS="20,100"
LLMDBENCH_BRANCH='my-branch'
PATH=/evil
HF_TOKEN=secret
not a setting
"""
        self.assertEqual(a.parse_settings(text),
                         {"AMT_SERVICE_S": "10", "AMT_LEVELS": "20,100", "LLMDBENCH_BRANCH": "my-branch"})


class SamplerTests(unittest.TestCase):
    def test_sampler_does_not_shadow_thread_methods(self):
        # A threading.Event stored as `_stop` replaced Thread._stop(), which
        # join() calls on Python <= 3.12: "'Event' object is not callable".
        import threading
        import run

        # Private Thread methods of Python 3.10-3.12 (CI runners), some of which
        # newer versions removed, plus whatever the running version defines.
        thread_methods = {name for name in dir(threading.Thread) if callable(getattr(threading.Thread, name))}
        thread_methods |= {"_stop", "_bootstrap", "_bootstrap_inner", "_wait_for_tstate_lock",
                           "_set_tstate_lock", "_set_ident", "_set_native_id", "_delete",
                           "_reset_internal_locks"}
        cfg = type("Cfg", (), {"namespace": "ns"})()
        own = set(vars(run.Sampler(cfg))) - set(vars(threading.Thread()))
        self.assertEqual(sorted(own & thread_methods), [])


class ProfileCheckTests(unittest.TestCase):
    def test_check_profiles(self):
        import run

        with tempfile.TemporaryDirectory() as d:
            clone = Path(d)
            with self.assertRaises(RuntimeError) as err:
                run.check_profiles(clone)
            self.assertIn("llm-d/llm-d-benchmark#2004", str(err.exception))
            profile_dir = clone / "workload" / "profiles" / "inference-perf"
            profile_dir.mkdir(parents=True)
            for name in (x.REALTIME_PROFILE, x.ASYNC_PROFILE):
                (profile_dir / f"{name}.in").write_text("load: {}\n")
            run.check_profiles(clone)  # no exception once both are present


class InstallTests(unittest.TestCase):
    def test_fallback_starts_from_a_fresh_venv(self):
        # install.sh reuses an existing .venv; the one a failed --no-uv attempt
        # leaves (on a too-old system Python) must not reach the --uv attempt.
        import run

        with tempfile.TemporaryDirectory() as d:
            cfg = type("Cfg", (), {"workdir": Path(d), "skip_install": False, "bench_repo": "", "bench_ref": ""})()
            clone = Path(d) / "llm-d-benchmark"
            seen = []

            def fake_stream(cmd, cwd, log_path, env=None):
                mode = cmd[-1].split()[-1]
                seen.append((mode, (clone / ".venv").exists()))
                (clone / ".venv").mkdir(parents=True, exist_ok=True)   # left behind by every attempt
                return 1

            orig_stream, orig_find = run.stream, run.find_cli
            run.stream, run.find_cli = fake_stream, lambda c: None
            try:
                with self.assertRaises(RuntimeError):
                    run.install_cli(cfg)
            finally:
                run.stream, run.find_cli = orig_stream, orig_find
            self.assertEqual(seen, [("--no-uv", False), ("--uv", False)])


class RouterValuesTests(unittest.TestCase):
    def test_guide_router_values(self):
        import run

        holdback = run.GUIDE_DIR / "values" / "router" / "flow-control-holdback.yaml"
        evictable = run.GUIDE_DIR / "values" / "router" / "flow-control-evictable.yaml"
        self.assertEqual(run.min_ceiling_from_values(holdback), 0.5)
        self.assertEqual(run.min_ceiling_from_values(evictable), 1.0)   # no holdback
        self.assertEqual(run.max_concurrency_from_values(holdback), 10)
        self.assertEqual(run.max_concurrency_from_values(evictable), 10)
        self.assertEqual(tuple(sorted(run.FLOW_CONTROL_MODES)), ("evictable", "holdback"))
        for mode in run.FLOW_CONTROL_MODES:
            self.assertTrue((run.GUIDE_DIR / "values" / "router" / f"flow-control-{mode}.yaml").is_file(), mode)

    def test_default_matches_the_guide(self):
        # The validator's default router values are guide.yaml's FLOW_CONTROL
        # default, the same one the deploy script falls back to.
        import re

        import run

        guide = (run.GUIDE_DIR / "guide.yaml").read_text()
        default = re.search(r"^\s*FLOW_CONTROL:\s*\{default:\s*(\w+)", guide, re.MULTILINE).group(1)
        self.assertEqual(run.FLOW_CONTROL_MODES[0], default)


class ExperimentTests(unittest.TestCase):
    def test_levels_map_to_concurrency(self):
        self.assertEqual([x.concurrency_for_level(L, 10) for L in (20, 80, 90, 100)], [2, 8, 9, 10])
        self.assertEqual(x.concurrency_for_level(5, 10), 1)
        with self.assertRaises(ValueError):
            x.concurrency_for_level(0, 10)
        with self.assertRaises(ValueError):
            x.concurrency_for_level(50, 0)

    def test_num_requests(self):
        p = x.ExperimentParams(capacity=10)
        self.assertEqual(x.num_requests_for(2, p), 80)
        self.assertEqual(x.num_requests_for(10, p), 400)
        self.assertEqual(x.num_requests_for(10, x.ExperimentParams(capacity=10, realtime_num_requests=10)), 10)

    def test_render(self):
        p = x.ExperimentParams(capacity=10, levels=(20, 100))
        text = x.render_experiment(p)
        for name in x.treatment_names((20, 100)):
            self.assertIn(f"- name: {name}", text)
        self.assertEqual(x.treatment_names((20, 100)),
                         ["async_only", "baseline_20", "mixed_20_async", "mixed_20_rt", "baseline_100", "mixed_100_async", "mixed_100_rt"])
        self.assertIn("load.stages.0.concurrency_level: 2", text)
        self.assertIn("load.stages.0.num_requests: 400", text)
        self.assertNotIn("description.text", text)  # not a valid run-treatment override; silently dropped
        self.assertIn("load.stages.0.rate: 10.0", text)
        self.assertIn("load.stages.0.duration: 180", text)
        self.assertIn("load.request_timeout: 20", text)
        self.assertIn("max_parallel_treatments: 2", text)
        self.assertIn(f"profile: {x.ASYNC_PROFILE}", text)
        self.assertIn(f"profile: {x.REALTIME_PROFILE}", text)
        # mixed groups have exactly two members, baseline/async_only one
        groups = text.split("\n  - name: ")[1:]
        members = {g.split("\n", 1)[0]: g.count("      - name: ") for g in groups}
        self.assertEqual(members, {"async_only": 1, "baseline_20": 1, "mixed_20": 2, "baseline_100": 1, "mixed_100": 2})

    def test_mixed_group_lists_async_member_first(self):
        text = x.render_experiment(x.ExperimentParams(capacity=10, levels=(20,)))
        mixed = text.split("  - name: mixed_20\n", 1)[1]
        self.assertLess(mixed.index("- name: mixed_20_async"), mixed.index("- name: mixed_20_rt"))

    def test_render_without_async_only(self):
        p = x.ExperimentParams(capacity=10, levels=(100,), include_async_only=False)
        text = x.render_experiment(p)
        self.assertNotIn("name: async_only", text)
        self.assertIn("groups:\n  - name: baseline_100\n", text)
        self.assertEqual(x.treatment_names((100,), include_async_only=False),
                         ["baseline_100", "mixed_100_async", "mixed_100_rt"])
        self.assertIn("treatment_max_attempts: 1", text)


if __name__ == "__main__":
    unittest.main()
