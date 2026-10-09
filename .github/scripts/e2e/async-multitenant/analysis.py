"""Analysis for the async-multitenant nightly: per-request records -> checks.

Pure functions over inference-perf per-request lifecycle records and a few
scraped metrics. No cluster access; run.py feeds it. Only the standard library
is required; ``verify_helpers`` (shared with the benchmark-lane verifiers) is
used for the Check type and table rendering when importable.

Tolerances are env-overridable: ``AMT_<KEY>`` sets a global default and
``AMT_LEVEL_<L>_<KEY>`` overrides it for one saturation level.
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Mapping, TypeVar

_VERIFY_DIR = Path(__file__).resolve().parent.parent.parent / "nightly-e2e-verification"
if str(_VERIFY_DIR) not in sys.path:
    sys.path.insert(0, str(_VERIFY_DIR))
try:  # pragma: no cover - exercised implicitly
    from verify_helpers import Check, print_checks_table  # type: ignore
except ImportError:  # pragma: no cover
    @dataclass
    class Check:  # type: ignore[no-redef]
        name: str
        passed: bool
        detail: str = ""

    def print_checks_table(checks: Iterable[Check]) -> None:  # type: ignore[no-redef]
        for c in checks:
            print(f"{'PASS' if c.passed else 'FAIL'}  {c.name}  {c.detail}")


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

T = TypeVar("T")

ENV_PREFIX = "AMT_"

DEFAULTS: dict[str, float] = {
    "TTFT_FACTOR": 1.5,
    "TTFT_ABS": 0.3,
    "E2E_FACTOR": 1.25,
    "E2E_ABS": 0.3,
    "RPS_FACTOR": 0.85,
    "TPS_FACTOR": 0.85,
    "STREAM_SANITY_RATIO": 0.6,
    "ASYNC_SAT_MIN": 0.9,
    "ASYNC_SAT_P50_MIN": 0.8,
    "ASYNC_RPS_FACTOR": 0.7,
    "MIN_SAMPLES": 20,
    "WARMUP_S": 15.0,
    "EXPECTED_OUTPUT_TOKENS": 128,
    "CAPACITY_SLACK": 1,
}

# At 20 % the mixed run raises the vLLM batch from 2 to 10 concurrent
# sequences, which slows every decode step before any queueing effect, so the
# throughput/latency floors are looser there by default.
LEVEL_DEFAULTS: dict[int, dict[str, float]] = {
    20: {"RPS_FACTOR": 0.75, "TPS_FACTOR": 0.75, "E2E_FACTOR": 1.35},
}


def tolerance(key: str, env: Mapping[str, str], level: int | None = None) -> float:
    """Resolve one tolerance: level env > global env > level default > default."""
    if key not in DEFAULTS:
        raise KeyError(f"unknown tolerance {key}")
    candidates = []
    if level is not None:
        candidates.append(f"{ENV_PREFIX}LEVEL_{level}_{key}")
    candidates.append(f"{ENV_PREFIX}{key}")
    for name in candidates:
        raw = env.get(name)
        if raw is not None and raw != "":
            return float(raw)
    if level is not None and key in LEVEL_DEFAULTS.get(level, {}):
        return LEVEL_DEFAULTS[level][key]
    return DEFAULTS[key]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Record:
    start: float
    end: float
    ttft: float | None
    output_tokens: int
    ok: bool
    error: str | None = None

    @property
    def e2e(self) -> float:
        return self.end - self.start


def _first_positive(values) -> float | None:
    if not isinstance(values, list):
        return None
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f) and f > 0:
            return f
    return None


def record_from_lifecycle(item: Mapping) -> Record | None:
    """Convert one inference-perf RequestLifecycleMetric dict to a Record.

    Fields used: start_time, end_time, error, info.response_metrics
    .{output_tokens,output_token_times,chunk_times}. Unknown shapes return None.
    """
    try:
        start = float(item["start_time"])
        end = float(item["end_time"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(start) and math.isfinite(end)) or end < start:
        return None
    error = item.get("error")
    err_text = None
    if error:
        if isinstance(error, Mapping):
            err_text = f"{error.get('error_type', 'error')}: {error.get('error_msg', '')}".strip()
        else:
            err_text = str(error)
    info = item.get("info") or {}
    resp = (info.get("response_metrics") or {}) if isinstance(info, Mapping) else {}
    # inference-perf's output_tokens is a client-side re-tokenisation unless
    # configured otherwise; the server's usage.completion_tokens is exact.
    tokens = 0
    usage = resp.get("server_usage") if isinstance(resp, Mapping) else None
    for source in ((usage or {}).get("completion_tokens"), resp.get("output_tokens")):
        try:
            tokens = int(source or 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens > 0:
            break
    ttft = None
    first = _first_positive(resp.get("output_token_times")) or _first_positive(resp.get("chunk_times"))
    if first is not None:
        candidate = first - start
        # Both stamps come from the same clock; anything outside [0, e2e]
        # means a different clock and is discarded rather than trusted.
        if 0 <= candidate <= (end - start) + 1e-6:
            ttft = candidate
    return Record(start=start, end=end, ttft=ttft, output_tokens=tokens, ok=err_text is None, error=err_text)


def parse_per_request(payload) -> list[Record]:
    """Accept a list of records or a mapping holding one under any key."""
    items = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], Mapping) and "start_time" in value[0]:
                items = value
                break
        if items is None:
            for key in ("requests", "per_request", "records", "data"):
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break
    if not items:
        return []
    out = []
    for item in items:
        if isinstance(item, Mapping):
            rec = record_from_lifecycle(item)
            if rec is not None:
                out.append(rec)
    return out


PER_REQUEST_FILENAME = "per_request_lifecycle_metrics.json"


def load_treatment(results_dir: Path) -> list[Record]:
    """Load the per-request records written under a treatment's results dir."""
    candidates = sorted(Path(results_dir).rglob(PER_REQUEST_FILENAME))
    if not candidates:
        return []
    with candidates[0].open() as f:
        payload = json.load(f)
    records = parse_per_request(payload)
    return anchor_records(records, Path(results_dir))


_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")


def read_flat_yaml(path: Path) -> dict[str, str]:
    """Read a flat ``key: "value"`` YAML file (run_metadata.yaml) without PyYAML."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        if ":" not in line or line.startswith((" ", "#")):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


_EXPERIMENT_ID_RE = re.compile(r"^(?P<harness>[a-z0-9-]+?)-(?P<name>[A-Za-z0-9_]+)-\d{9,}-[a-z0-9]+$")


def treatment_name(meta: Mapping[str, str], profile_stems: Iterable[str] = ()) -> str:
    """Recover the treatment name from run_metadata.yaml.

    llmdbenchmark names each treatment's experiment ``<harness>-<treatment>-<ts>-<rand>``
    and its rendered workload ``<profile stem>-<treatment>.yaml``; ``description_text``
    is used when present but run treatments cannot set it.
    """
    desc = (meta.get("description_text") or "").strip()
    if desc:
        return desc
    workload = meta.get("harness_workload", "")
    for stem in profile_stems:
        prefix = f"{stem}-"
        if workload.startswith(prefix) and workload.endswith(".yaml"):
            return workload[len(prefix):-len(".yaml")]
    m = _EXPERIMENT_ID_RE.match(meta.get("experiment_id", ""))
    return m.group("name") if m else ""


def _iso_to_epoch(text: str) -> float | None:
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def anchor_records(records: list[Record], results_dir: Path) -> list[Record]:
    """Shift monotonic-clock stamps onto wall time using run_metadata.yaml.

    inference-perf stamps start/end from a monotonic clock. Records inside one
    treatment stay comparable either way, but the mixed-group overlap window
    compares two pods, which needs wall time. The last request's end is pinned
    to ``harness_stop`` (the harness exits right after its final request;
    ``harness_start`` precedes the first request by a variable tokenizer-load
    delay, so it is only the fallback). Epoch stamps are left untouched.
    """
    if not records:
        return records
    median_start = sorted(r.start for r in records)[len(records) // 2]
    if median_start > 1e9:  # already wall-clock epoch seconds
        return records
    meta = read_flat_yaml(Path(results_dir) / "run_metadata.yaml")
    if not meta:
        return records
    stop = _iso_to_epoch(meta.get("harness_stop", ""))
    start = _iso_to_epoch(meta.get("harness_start", ""))
    if stop is not None:
        offset = stop - max(r.end for r in records)
    elif start is not None:
        offset = start - min(r.start for r in records)
    else:
        return records
    return [Record(r.start + offset, r.end + offset, r.ttft, r.output_tokens, r.ok, r.error) for r in records]


# ---------------------------------------------------------------------------
# Windows and summaries
# ---------------------------------------------------------------------------

def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile of a non-empty list; p in [0, 1]."""
    if not values:
        raise ValueError("percentile of empty list")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[int(idx)]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


def baseline_window(records: list[Record], warmup_s: float) -> tuple[float, float] | None:
    if not records:
        return None
    start = min(r.start for r in records) + warmup_s
    end = max(r.end for r in records)
    return (start, end) if end > start else None


def overlap_window(primary: list[Record], other: list[Record], warmup_s: float) -> tuple[float, float] | None:
    """Window where both populations were active, minus a warm-up at the front."""
    if not primary or not other:
        return None
    start = max(min(r.start for r in primary), min(r.start for r in other)) + warmup_s
    end = min(max(r.end for r in primary), max(r.end for r in other))
    return (start, end) if end > start else None


def in_window(records: Iterable[Record], window: tuple[float, float]) -> list[Record]:
    start, end = window
    return [r for r in records if r.start >= start and r.end <= end]



def comparison_valid(level: int, baseline: list[Record] | None, rt: list[Record] | None,
                     async_recs: list[Record] | None, env: Mapping[str, str]) -> bool:
    """Whether one level yields a usable baseline/mixed comparison.

    Invalid means a treatment is missing, or the mixed members did not overlap
    for long enough to collect MIN_SAMPLES realtime completions (the members of
    a group can start minutes apart when their pods land on fresh nodes).
    """
    if not baseline or not rt or not async_recs:
        return False
    warm = tolerance("WARMUP_S", env)
    need = int(tolerance("MIN_SAMPLES", env, level))
    if summarize(baseline, baseline_window(baseline, warm)).count < need:
        return False
    window = overlap_window(rt, async_recs, warm)
    return window is not None and summarize(rt, window).count >= need


def rerun_plan(levels: Iterable[int], records: Mapping[str, list[Record] | None],
               env: Mapping[str, str]) -> tuple[list[int], bool]:
    """Levels whose comparison is invalid, and whether async-only is missing.

    ``records`` maps treatment names to their loaded records (absent or None
    when the treatment produced no results).
    """
    bad = [L for L in levels if not comparison_valid(
        L, records.get(f"baseline_{L}"), records.get(f"mixed_{L}_rt"), records.get(f"mixed_{L}_async"), env)]
    return bad, not records.get("async_only")



def merge_rerun(old: Mapping[str, T], new: Mapping[str, T], levels: Iterable[int],
                async_only: bool) -> tuple[dict[str, T], list[str]]:
    """Merge a re-run's results into the first run's, level by level.

    A level's baseline and mixed members must come from the same run, or the
    mixed members would be compared across runs that never overlapped. A
    re-run level replaces the old one only when all three of its treatments
    came back; otherwise the old set is kept. Returns the merged mapping and
    notes on what was kept.
    """
    merged, notes = dict(old), []
    for L in levels:
        names = (f"baseline_{L}", f"mixed_{L}_async", f"mixed_{L}_rt")
        if all(n in new for n in names):
            merged.update({n: new[n] for n in names})
        else:
            notes.append(f"level {L}: re-run incomplete ({', '.join(n for n in names if n not in new)} missing); "
                         f"keeping the first run's results")
    if async_only:
        if "async_only" in new:
            merged["async_only"] = new["async_only"]
        else:
            notes.append("async-only: re-run produced no results")
    return merged, notes


def model_from_args(args: Iterable[str]) -> str:
    """The model in a vLLM command line: `--model X`, or the first positional of `vllm serve X ...`."""
    args = [str(a) for a in args]
    for i, a in enumerate(args):
        if a == "--model" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--model="):
            return a.split("=", 1)[1]
    return args[0] if args and not args[0].startswith("-") else ""

def parse_settings(text: str) -> dict[str, str]:
    """Parse a validator settings file: ``KEY=VALUE`` lines, ``#`` comments.

    Only ``AMT_*`` and ``LLMDBENCH_*`` keys are accepted, so the file can only
    tune this validator and the benchmark CLI. Surrounding quotes are removed.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key.startswith((ENV_PREFIX, "LLMDBENCH_")):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out

@dataclass
class Summary:
    count: int = 0
    errors: int = 0
    window_s: float = 0.0
    ttft_p50: float | None = None
    ttft_p90: float | None = None
    ttft_p95: float | None = None
    ttft_mean: float | None = None
    e2e_p50: float | None = None
    e2e_p90: float | None = None
    e2e_p95: float | None = None
    e2e_mean: float | None = None
    rps: float = 0.0
    output_tps: float = 0.0
    output_tokens_min: int | None = None
    output_tokens_max: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def summarize(records: list[Record], window: tuple[float, float] | None) -> Summary:
    """Summarise the records that start and finish inside ``window``."""
    if window is None:
        return Summary()
    inside = in_window(records, window)
    window_s = window[1] - window[0]
    ok = [r for r in inside if r.ok]
    s = Summary(count=len(inside), errors=len(inside) - len(ok), window_s=window_s)
    if not ok:
        return s
    e2e = [r.e2e for r in ok]
    ttft = [r.ttft for r in ok if r.ttft is not None]
    s.e2e_p50, s.e2e_p90, s.e2e_p95 = percentile(e2e, 0.5), percentile(e2e, 0.9), percentile(e2e, 0.95)
    s.e2e_mean = sum(e2e) / len(e2e)
    if ttft:
        s.ttft_p50, s.ttft_p90, s.ttft_p95 = percentile(ttft, 0.5), percentile(ttft, 0.9), percentile(ttft, 0.95)
        s.ttft_mean = sum(ttft) / len(ttft)
    tokens = [r.output_tokens for r in ok]
    s.output_tokens_min, s.output_tokens_max = min(tokens), max(tokens)
    if window_s > 0:
        s.rps = len(ok) / window_s
        s.output_tps = sum(tokens) / window_s
    return s


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def _upper_bound_check(name: str, mixed: float | None, base: float | None, factor: float, abs_floor: float) -> Check:
    if mixed is None or base is None:
        return Check(name, False, "missing measurement (no TTFT samples?)")
    bound = base * factor + abs_floor
    return Check(name, mixed <= bound, f"mixed {mixed:.3f}s <= {bound:.3f}s (= {base:.3f}s x {factor} + {abs_floor}s)")


def _lower_bound_check(name: str, mixed: float, base: float, factor: float, unit: str) -> Check:
    bound = base * factor
    return Check(name, mixed >= bound, f"mixed {mixed:.2f}{unit} >= {bound:.2f}{unit} (= {base:.2f}{unit} x {factor})")


def compare_level(level: int, baseline: Summary, mixed: Summary, env: Mapping[str, str],
                  async_successes: int | None = None,
                  async_dispatch_rps: float | None = None) -> list[Check]:
    """Per-level bounded-degradation checks (mixed vs baseline realtime)."""
    t = lambda key: tolerance(key, env, level)  # noqa: E731
    p = f"L{level}"
    checks: list[Check] = []
    min_samples = int(t("MIN_SAMPLES"))
    if mixed.window_s <= 0:
        # The mixed group's members never ran at the same time (e.g. the second
        # harness pod waited for a node), so there is nothing to compare.
        checks.append(Check(f"{p} members overlapped", False,
                            "realtime and async members had no common window; check harness pod scheduling"))
        return checks
    checks.append(Check(f"{p} members overlapped", True, f"{mixed.window_s:.0f}s common window after warm-up"))
    checks.append(Check(f"{p} samples", baseline.count >= min_samples and mixed.count >= min_samples,
                        f"baseline {baseline.count}, mixed {mixed.count} (need >= {min_samples} each)"))
    checks.append(Check(f"{p} realtime errors", baseline.errors == 0 and mixed.errors == 0,
                        f"baseline {baseline.errors}, mixed {mixed.errors} non-2xx/errors"))
    checks.append(_upper_bound_check(f"{p} ttft p50", mixed.ttft_p50, baseline.ttft_p50, t("TTFT_FACTOR"), t("TTFT_ABS")))
    checks.append(_upper_bound_check(f"{p} ttft p90", mixed.ttft_p90, baseline.ttft_p90, t("TTFT_FACTOR"), t("TTFT_ABS")))
    checks.append(_upper_bound_check(f"{p} e2e p50", mixed.e2e_p50, baseline.e2e_p50, t("E2E_FACTOR"), t("E2E_ABS")))
    checks.append(_upper_bound_check(f"{p} e2e p90", mixed.e2e_p90, baseline.e2e_p90, t("E2E_FACTOR"), t("E2E_ABS")))
    checks.append(_lower_bound_check(f"{p} req/s", mixed.rps, baseline.rps, t("RPS_FACTOR"), " req/s"))
    checks.append(_lower_bound_check(f"{p} output tok/s", mixed.output_tps, baseline.output_tps, t("TPS_FACTOR"), " tok/s"))
    if level == min(LEVEL_DEFAULTS) if LEVEL_DEFAULTS else False:
        pass
    # At 20 % the pool has spare capacity that queued async work should use. The
    # router's dispatch counter is the measure: with a deep backlog most async
    # clients time out before their turn, so client completions read near zero.
    if level <= 20 and async_dispatch_rps is not None:
        checks.append(Check(f"{p} async uses slack", async_dispatch_rps > 0,
                            f"EPP dispatched {async_dispatch_rps:.2f} async req/s during the mixed window"
                            + (f" ({async_successes} client completions)" if async_successes is not None else "")))
    elif level <= 20 and async_successes is not None:
        checks.append(Check(f"{p} async uses slack", async_successes > 0,
                            f"{async_successes} async completions during the mixed window"))
    return checks


def streaming_sanity(level: int, baseline: Summary, env: Mapping[str, str]) -> Check:
    """TTFT well below E2E proves the response really streamed through the coordinator."""
    ratio_max = tolerance("STREAM_SANITY_RATIO", env, level)
    if baseline.ttft_p50 is None or baseline.e2e_p50 is None or baseline.e2e_p50 <= 0:
        return Check(f"L{level} streaming sanity", False, "no TTFT/E2E samples")
    ratio = baseline.ttft_p50 / baseline.e2e_p50
    return Check(f"L{level} streaming sanity", ratio < ratio_max,
                 f"ttft/e2e p50 ratio {ratio:.2f} < {ratio_max} (higher means SSE is buffered upstream)")


# ---------------------------------------------------------------------------
# Cluster samples (vLLM + EPP metrics polled during the run)
# ---------------------------------------------------------------------------

VLLM_RUNNING = "vllm:num_requests_running"
VLLM_WAITING = "vllm:num_requests_waiting"
EPP_SATURATION = "llm_d_epp_flow_control_pool_saturation"
EPP_BAND_REQUESTS = "llm_d_epp_flow_control_requests_total"


@dataclass
class Sample:
    t: float
    running: float | None = None      # sum of vllm:num_requests_running over model pods
    waiting: float | None = None      # sum of vllm:num_requests_waiting
    saturation: float | None = None   # EPP pool saturation (stage="effective" when labelled)
    band_requests: dict[int, float] = field(default_factory=dict)  # requests_total by priority

    def as_dict(self) -> dict:
        d = asdict(self)
        d["band_requests"] = {str(k): v for k, v in self.band_requests.items()}
        return d


def parse_sample(t: float, vllm_text: str, epp_text: str) -> Sample:
    """Build a Sample from raw vLLM and EPP exposition snippets."""
    s = Sample(t=t)
    running = waiting = None
    for name, _labels, value in parse_exposition(vllm_text):
        if name == VLLM_RUNNING:
            running = (running or 0.0) + value
        elif name == VLLM_WAITING:
            waiting = (waiting or 0.0) + value
    s.running, s.waiting = running, waiting
    sat_by_stage: dict[str, float] = {}
    for name, labels, value in parse_exposition(epp_text):
        if name == EPP_SATURATION:
            sat_by_stage[labels.get("stage", "")] = value
        elif name == EPP_BAND_REQUESTS and "priority" in labels:
            try:
                prio = int(labels["priority"])
            except ValueError:
                continue
            s.band_requests[prio] = s.band_requests.get(prio, 0.0) + value
    if sat_by_stage:
        s.saturation = sat_by_stage.get("effective", max(sat_by_stage.values()))
    return s


@dataclass
class SaturationSummary:
    count: int = 0
    running_max: float | None = None
    running_p50: float | None = None
    saturation_max: float | None = None
    saturation_p50: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def saturation_summary(samples: Iterable[Sample], window: tuple[float, float] | None) -> SaturationSummary:
    """Summarise the samples taken inside ``window``."""
    if window is None:
        return SaturationSummary()
    inside = [s for s in samples if window[0] <= s.t <= window[1]]
    out = SaturationSummary(count=len(inside))
    running = [s.running for s in inside if s.running is not None]
    sat = [s.saturation for s in inside if s.saturation is not None]
    if running:
        out.running_max, out.running_p50 = max(running), percentile(running, 0.5)
    if sat:
        out.saturation_max, out.saturation_p50 = max(sat), percentile(sat, 0.5)
    return out


def capacity_enforced(env: Mapping[str, str]) -> bool:
    """Whether "pool held at capacity" fails the run (AMT_ENFORCE_CAPACITY).

    Off by default: llm-d-router counts an admitted request only after
    scheduling it, so the pool overshoots capacity under a backlog even when
    realtime isolation holds (llm-d-async#468). Until that is fixed the result
    is reported as an observation instead of a check.
    """
    return env.get(f"{ENV_PREFIX}ENFORCE_CAPACITY", "").strip().lower() in ("1", "true", "yes")


def capacity_check(name: str, sat: SaturationSummary | None, capacity: int, env: Mapping[str, str],
                   level: int | None = None) -> Check:
    """Flow control must hold pool concurrency at its configured capacity.

    Async traffic is meant to be queued, not admitted past C; a sustained
    excess is what turns into realtime queueing. One extra request of slack
    covers accounting jitter.
    """
    slack = tolerance("CAPACITY_SLACK", env, level)
    bound = capacity + slack
    if sat is None or sat.running_max is None:
        return Check(name, False, "no vLLM running-request samples")
    return Check(name, sat.running_max <= bound,
                 f"max running {sat.running_max:g}, bound {bound:g} (C={capacity} + slack {slack:g}; p50 {_fmt(sat.running_p50)}, "
                 f"EPP saturation max {_fmt(sat.saturation_max)})")


def band_counts_from_samples(samples: Iterable[Sample]) -> dict[int, float]:
    """Per-priority request counters are cumulative, so the maximum seen is the total."""
    counts: dict[int, float] = {}
    for s in samples:
        for prio, value in s.band_requests.items():
            counts[prio] = max(counts.get(prio, 0.0), value)
    return counts


def dispatch_rate(samples: Iterable[Sample], window: tuple[float, float] | None,
                  priorities: Iterable[int]) -> float | None:
    """Requests/s the EPP dispatched for the given bands during ``window``.

    Read from the cumulative flow_control_requests_total counters, so it counts
    what reached the model server even when the open-loop client had already
    given up on the request.
    """
    if window is None:
        return None
    prios = set(priorities)
    inside = sorted((s for s in samples if window[0] <= s.t <= window[1] and s.band_requests), key=lambda s: s.t)
    if len(inside) < 2:
        return None
    total = lambda s: sum(v for p, v in s.band_requests.items() if p in prios)  # noqa: E731
    span = inside[-1].t - inside[0].t
    if span <= 0:
        return None
    return (total(inside[-1]) - total(inside[0])) / span


ASYNC_PRIORITIES = (60, 30, -5, -10)


def async_only_checks(sat: SaturationSummary | None, async_summary: Summary, baseline100: Summary | None,
                      capacity: int, env: Mapping[str, str], epp_dispatch_rps: float | None = None,
                      ceiling: float = 1.0) -> list[Check]:
    """Async alone must fill the pool and keep the model server dispatching.

    ``ceiling`` is the fraction of capacity the router admits async traffic to:
    priority holdback's minCeiling, or 1.0 without holdback. The saturation and
    dispatch-rate bounds scale with it, so with holdback the checks ask that
    async alone fills the share holdback allows it.

    ``epp_dispatch_rps`` is the EPP-side dispatch rate for the async bands (see
    dispatch_rate); it is the throughput figure compared with baseline(100),
    because the open-loop async client abandons queued requests by design and
    its own completion count understates what the pool processed.
    """
    t = lambda key: tolerance(key, env)  # noqa: E731
    checks: list[Check] = []
    running_max = sat.running_max if sat else None
    running_p50 = sat.running_p50 if sat else None
    n = sat.count if sat else 0
    sat_min = t("ASYNC_SAT_MIN") * capacity * ceiling
    p50_min = t("ASYNC_SAT_P50_MIN") * capacity * ceiling
    scope = f"C={capacity}" + (f", holdback ceiling {ceiling:g}" if ceiling < 1 else "")
    checks.append(Check("async-only pool saturation (max)", running_max is not None and running_max >= sat_min,
                        f"{_fmt(running_max)} >= {sat_min:.1f} running requests ({scope}, {n} samples)"
                        if running_max is not None else f"no vLLM running-request samples in the async-only window ({n} samples)"))
    checks.append(Check("async-only pool saturation (p50)", running_p50 is not None and running_p50 >= p50_min,
                        f"{_fmt(running_p50)} >= {p50_min:.1f} running requests" if running_p50 is not None
                        else "no vLLM running-request samples"))
    completed = async_summary.count - async_summary.errors
    checks.append(Check("async-only completions", completed > 0,
                        f"{completed} async requests completed at the client ({async_summary.rps:.2f}/s; "
                        f"timeouts of queued requests are expected)"))
    if baseline100 is not None and baseline100.rps > 0:
        floor = t("ASYNC_RPS_FACTOR") * baseline100.rps * ceiling
        if epp_dispatch_rps is None:
            checks.append(Check("async-only dispatch rate", False,
                                "no EPP per-band dispatch samples in the async-only window"))
        else:
            checks.append(Check("async-only dispatch rate", epp_dispatch_rps >= floor,
                                f"EPP dispatched {epp_dispatch_rps:.2f} async req/s >= {floor:.2f} req/s "
                                f"(= baseline(100) {baseline100.rps:.2f} x {t('ASYNC_RPS_FACTOR')}"
                                + (f" x ceiling {ceiling:g}" if ceiling < 1 else "") + ")"))
    expected = int(t("EXPECTED_OUTPUT_TOKENS"))
    if completed > 0:
        ok_tokens = (async_summary.output_tokens_min == expected and async_summary.output_tokens_max == expected)
        checks.append(Check("async results honour ignore_eos", ok_tokens,
                            f"completion tokens min {async_summary.output_tokens_min} max {async_summary.output_tokens_max} (expected {expected})"))
    return checks


# ---------------------------------------------------------------------------
# EPP metrics exposition
# ---------------------------------------------------------------------------

_SAMPLE_RE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(\S+)')
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')

QUEUE_DURATION_COUNT = "llm_d_epp_flow_control_request_queue_duration_seconds_count"


def parse_exposition(text: str) -> list[tuple[str, dict[str, str], float]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        name, raw_labels, raw_value = m.group(1), m.group(2) or "", m.group(3)
        try:
            value = float(raw_value)
        except ValueError:
            continue
        labels = {k: v for k, v in _LABEL_RE.findall(raw_labels)}
        out.append((name, labels, value))
    return out


def band_counts(text: str, metric: str = EPP_BAND_REQUESTS) -> dict[int, float]:
    """Sum a per-priority counter across label sets; keys are band priorities."""
    counts: dict[int, float] = {}
    for name, labels, value in parse_exposition(text):
        if name != metric or "priority" not in labels:
            continue
        try:
            prio = int(labels["priority"])
        except ValueError:
            continue
        counts[prio] = counts.get(prio, 0.0) + value
    return counts


def band_checks(counts: Mapping[int, float], realtime_priority: int = 100,
                async_priorities: Iterable[int] = (30, -10)) -> list[Check]:
    """Per-band request counters prove the objectives were applied and honoured."""
    shown = ", ".join(f"{k}={v:g}" for k, v in sorted(counts.items(), reverse=True)) or "none"
    rt = counts.get(realtime_priority, 0.0)
    async_total = sum(counts.get(p, 0.0) for p in async_priorities)
    return [
        Check("realtime classified into band 100", rt > 0, f"flow-control requests by priority: {shown}"),
        Check("async classified into batch bands", async_total > 0,
              f"sum over priorities {list(async_priorities)} = {async_total:g}"),
    ]


def merge_band_counts(*sources: Mapping[int, float]) -> dict[int, float]:
    out: dict[int, float] = {}
    for src in sources:
        for k, v in src.items():
            out[k] = max(out.get(k, 0.0), v)
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def level_table_row(level: int, k: int, base: Summary, mixed: Summary, async_ok: int | None,
                    base_sat: SaturationSummary | None = None, mixed_sat: SaturationSummary | None = None) -> dict:
    def ratio(a, b):
        return None if a is None or b in (None, 0) else a / b
    return {
        "level": level, "k": k,
        "baseline": base.as_dict(), "mixed": mixed.as_dict(),
        "baseline_saturation": base_sat.as_dict() if base_sat else None,
        "mixed_saturation": mixed_sat.as_dict() if mixed_sat else None,
        "ttft_p50_ratio": ratio(mixed.ttft_p50, base.ttft_p50),
        "ttft_p90_ratio": ratio(mixed.ttft_p90, base.ttft_p90),
        "e2e_p50_ratio": ratio(mixed.e2e_p50, base.e2e_p50),
        "rps_ratio": ratio(mixed.rps, base.rps),
        "async_completions": async_ok,
    }


def epoch_window_from_metadata(meta: Mapping[str, str], warmup_s: float = 0.0) -> tuple[float, float] | None:
    """[harness_start + warmup, harness_stop] of a treatment, in epoch seconds."""
    start = _iso_to_epoch(meta.get("harness_start", ""))
    stop = _iso_to_epoch(meta.get("harness_stop", ""))
    if start is None or stop is None or stop <= start + warmup_s:
        return None
    return (start + warmup_s, stop)


def render_markdown(report: Mapping) -> str:
    lines = ["## Async multi-tenant isolation (realtime vs llm-d-async backlog)", ""]
    cap = report.get("capacity")
    lines.append(f"Pool capacity C = {cap}; realtime concurrency k = round(L/100 x C). "
                 "Closed loop, so req/s = k / mean(E2E).")
    lines.append("")
    lines.append("| L | k | TTFT p50 base/mixed (s) | TTFT p90 base/mixed (s) | E2E p50 base/mixed (s) | req/s base/mixed | tok/s base/mixed | async done |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in report.get("levels", []):
        b, m = row["baseline"], row["mixed"]
        lines.append(
            f"| {row['level']} | {row['k']} | {_fmt(b['ttft_p50'])} / {_fmt(m['ttft_p50'])} "
            f"| {_fmt(b['ttft_p90'])} / {_fmt(m['ttft_p90'])} | {_fmt(b['e2e_p50'])} / {_fmt(m['e2e_p50'])} "
            f"| {b['rps']:.2f} / {m['rps']:.2f} | {b['output_tps']:.0f} / {m['output_tps']:.0f} "
            f"| {row.get('async_completions') if row.get('async_completions') is not None else 'n/a'} |")
    a = report.get("async_only")
    if a:
        lines += ["", f"Async-only: EPP dispatched {_fmt(a.get('epp_dispatch_rps'))} async req/s; "
                      f"{a.get('completions')} client completions in {a.get('window_s', 0):.0f}s; "
                      f"vLLM running requests max {_fmt(a.get('running_max'))}, p50 {_fmt(a.get('running_p50'))}; "
                      f"EPP pool saturation max {_fmt(a.get('saturation_max'))}."]
    lines += ["", "| Status | Check | Detail |", "|---|---|---|"]
    for c in report.get("checks", []):
        lines.append(f"| {'PASS' if c['passed'] else 'FAIL'} | {c['name']} | {c['detail']} |")
    observations = report.get("observations", [])
    if observations:
        lines += ["", "Informational (does not fail the run; set `AMT_ENFORCE_CAPACITY=1` to enforce):", "",
                  "| Status | Observation | Detail |", "|---|---|---|"]
        for o in observations:
            lines.append(f"| {'OK' if o['passed'] else 'OVER'} | {o['name']} | {o['detail']} |")
    lines.append("")
    return "\n".join(lines)


def checks_to_dicts(checks: Iterable[Check]) -> list[dict]:
    return [{"name": c.name, "passed": bool(c.passed), "detail": c.detail} for c in checks]
