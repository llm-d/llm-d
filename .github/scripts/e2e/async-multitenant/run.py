#!/usr/bin/env python3
"""Orchestrate the async-multitenant realtime-isolation e2e on the runner.

Called by e2e-validate-async-multitenant.sh after the stack (multitenant guide
plus the llm-d-router coordinator with the async-broker step) is up and the
smoke loop passed. Steps:

  1. preflight the namespace (vLLM, coordinator, objectives, services);
  2. install the llm-d-benchmark CLI (install.sh, into a venv) and make sure the two
     inference-perf profiles exist in the clone;
  3. render the experiment (experiment.py) and run it once against the
     coordinator with --monitoring and --no-pvc;
  4. locate the per-treatment results, scrape the EPP metrics once, and turn
     everything into checks (analysis.py);
  5. write report.json, the markdown summary and copies of the results into
     RESULTS_DIR (the reusable uploads it) and exit non-zero on any failure.

Everything is standard library; verify_helpers (shared with the benchmark
lane verifiers) provides results discovery and the check table.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
VERIFY_DIR = REPO_ROOT / ".github" / "scripts" / "nightly-e2e-verification"
for p in (str(HERE), str(VERIFY_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import analysis  # noqa: E402
import experiment  # noqa: E402
import verify_helpers as v  # noqa: E402

GUIDE_DIR = REPO_ROOT / "guides" / "batch-serving" / "asynchronous-processing" / "multitenant"
ROUTER_VALUES = GUIDE_DIR / "values" / "router" / "flow-control.yaml"
PROFILES_DIR = HERE / "profiles"
INSTALL_URL = "https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/install.sh"
EXPECTED_OBJECTIVES = (
    "reserved-interactive", "reserved-async", "reserved-batch",
    "overflow-interactive", "overflow-async", "overflow-batch",
)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(f"{analysis.ENV_PREFIX}{key}", default)


def default_settings_file() -> Path:
    """Where the guide's nightly-deploy-gke.sh records AMT_* settings it was given."""
    return Path(os.environ.get("OUTPUT_DIR", "/tmp/async-multitenant.ci")) / "validator.env"


def load_settings(cli_values: list[str]) -> None:
    """Apply validator settings before Config reads the environment.

    Precedence: ``--set KEY=VALUE`` > the process environment > the settings
    file (``AMT_SETTINGS_FILE``, default ``$OUTPUT_DIR/validator.env``). The
    file is how a workflow's ``custom_deploy_script`` line passes settings
    through to this step, since e2e-validate.sh forwards only -n and -m.
    """
    path = Path(os.environ.get(f"{analysis.ENV_PREFIX}SETTINGS_FILE") or default_settings_file())
    if path.is_file():
        applied = []
        for key, value in analysis.parse_settings(path.read_text()).items():
            if key not in os.environ:
                os.environ[key] = value
                applied.append(key)
        if applied:
            log(f"settings from {path}: {', '.join(sorted(applied))}")
    parsed = analysis.parse_settings("\n".join(cli_values))
    bad = [v for v in cli_values if v.split("=", 1)[0].strip() not in parsed]
    if bad:
        raise SystemExit(f"--set accepts AMT_*=VALUE or LLMDBENCH_*=VALUE, got: {bad}")
    os.environ.update(parsed)


class Config:
    def __init__(self, args: argparse.Namespace) -> None:
        self.namespace = args.namespace
        self.model = args.model
        self.verbose = args.verbose
        self.dry_run = args.dry_run
        guide_name = os.environ.get("GUIDE_NAME", "async-multitenant")
        self.workdir = Path(env("WORKDIR", "/tmp/amt"))
        self.results_dir = Path(env("RESULTS_DIR", f"/tmp/pod-logs-{guide_name}"))
        self.workspace = self.workdir / "workspace"
        self.coordinator_host = env("COORDINATOR_HOST", f"llm-d-coordinator.{self.namespace}.svc.cluster.local:8080")
        self.epp_host = env("EPP_HOST", os.environ.get("GATEWAY_HOST") or "llm-d-router-epp")
        self.epp_metrics_port = env("EPP_METRICS_PORT", "9090")
        # Small enough that both members of a mixed group schedule at once on
        # ordinary nodes; inference-perf at these rates needs little.
        # Two CPUs: the async member post-processes thousands of per-request
        # records when its stage ends, which takes minutes on one CPU.
        self.harness_cpu = env("HARNESS_CPU", "2")
        self.harness_memory = env("HARNESS_MEMORY", "2Gi")
        self.harness_memory_limit = env("HARNESS_MEMORY_LIMIT", "4Gi")
        self.sample_interval = float(env("SAMPLE_INTERVAL_S", "5"))
        self.vllm_selector = env("VLLM_SELECTOR", "app=vllm-1")
        self.vllm_port = env("VLLM_PORT", "8000")
        # A healthy treatment finishes in a few minutes; a hung inference-perf
        # client (seen occasionally after aiohttp timeouts) is cut off here and
        # its level re-run, instead of holding the job for the CLI's default hour.
        self.wait_timeout = int(env("WAIT_TIMEOUT", "900"))
        self.retries = int(env("RETRIES", "1"))
        # Optional llm-d-benchmark source, e.g. a fork carrying profile changes.
        self.bench_repo = env("BENCH_REPO", "")
        self.bench_ref = env("BENCH_REF", "")
        self.skip_install = env("SKIP_INSTALL") not in (None, "", "0", "false")
        self.extra_args = shlex.split(env("LLMDBENCH_EXTRA_ARGS", "") or "")
        levels = [int(x) for x in (env("LEVELS", "20,80,90,100") or "").split(",") if x.strip()]
        self.levels = tuple(levels)
        self.realtime_seconds = float(env("REALTIME_SECONDS", "80"))
        # One 128-token completion of Qwen/Qwen3-8B takes about 1 s on an H100
        # (measured on the nightly cluster); an L4 takes about 9 s.
        self.service_seconds = float(env("SERVICE_S", "1.0"))
        # Unset: derived from the pool capacity at render time (see async_rate_for).
        rate = env("ASYNC_RATE")
        self.async_rate: float | None = float(rate) if rate else None
        # The async member must cover the whole realtime member even when the
        # realtime pod starts later; START_SKEW_S is that allowance. On the
        # nightly's Standard cluster both pods start within seconds; on GKE
        # Autopilot (new nodes per pod) use 180.
        self.start_skew = float(env("START_SKEW_S", "60"))
        self.async_duration = int(env("ASYNC_DURATION",
                                      str(max(120, round(self.realtime_seconds + self.start_skew)))))
        # A dispatched async request needs several service times to finish under
        # contention; a timeout below that abandons work the GPU has already
        # started. 20 s suits a ~2 s service time (H100); slower GPUs scale up.
        self.async_timeout = int(env("ASYNC_TIMEOUT", str(max(20, round(4 * self.service_seconds)))))
        self.realtime_num_requests: int | None = None
        self.enforce = True
        if self.dry_run:
            self.levels = self.levels[:1]
            self.realtime_num_requests = int(env("DRY_RUN_REQUESTS", "10"))
            self.async_duration = int(env("DRY_RUN_ASYNC_DURATION", "45"))
            self.async_timeout = 10
            self.enforce = False


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------

def kubectl(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=timeout, check=False)


def kubectl_out(args: list[str], timeout: int = 60) -> str:
    r = kubectl(args, timeout)
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def stream(cmd: list[str], cwd: Path | None, log_path: Path, extra_env: dict | None = None) -> int:
    """Run a command, teeing stdout+stderr to the console and a log file."""
    merged = dict(os.environ)
    if extra_env:
        merged.update(extra_env)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as fh:  # one file per run.py invocation
        fh.write(f"$ {' '.join(shlex.quote(c) for c in cmd)}\n")
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=merged,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            fh.write(line)
        proc.wait()
        return proc.returncode


# ---------------------------------------------------------------------------
# Stack inspection
# ---------------------------------------------------------------------------

def ready_replicas(namespace: str, deploy: str) -> int:
    out = kubectl_out(["get", "deploy", deploy, "-n", namespace, "-o", "jsonpath={.status.readyReplicas}"])
    try:
        return int(out or 0)
    except ValueError:
        return 0


def discover_model(namespace: str) -> str:
    out = kubectl_out(["get", "deploy", "vllm", "-n", namespace, "-o",
                       "jsonpath={.spec.template.spec.containers[0].args}"])
    try:
        args = json.loads(out)
    except (TypeError, ValueError):
        return ""
    for i, a in enumerate(args):
        if a == "--model" and i + 1 < len(args):
            return str(args[i + 1])
    return ""


def max_concurrency_from_values(path: Path) -> int:
    matches = re.findall(r"^\s*maxConcurrency:\s*(\d+)\s*$", path.read_text(), flags=re.M)
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one maxConcurrency in {path}, found {len(matches)}")
    return int(matches[0])


def detect_capacity(cfg: Config) -> int:
    override = env("CAPACITY")
    if override:
        return int(override)
    per_replica = max_concurrency_from_values(ROUTER_VALUES)
    replicas = ready_replicas(cfg.namespace, "vllm")
    if replicas < 1:
        raise RuntimeError("deploy/vllm has no ready replicas; cannot derive pool capacity")
    return per_replica * replicas


def preflight(cfg: Config) -> None:
    problems = []
    if ready_replicas(cfg.namespace, "vllm") < 1:
        problems.append("deploy/vllm is not ready")
    if ready_replicas(cfg.namespace, "llm-d-coordinator") < 1:
        problems.append("deploy/llm-d-coordinator is not ready")
    # Fully qualified: clusters that also carry an older InferenceObjective CRD in
    # another API group resolve the short name to that one.
    names = set(kubectl_out(["get", "inferenceobjectives.llm-d.ai", "-n", cfg.namespace,
                             "-o", "jsonpath={.items[*].metadata.name}"]).split())
    missing = [n for n in EXPECTED_OBJECTIVES if n not in names]
    if missing:
        problems.append(f"InferenceObjectives missing: {missing}")
    for svc in ("llm-d-router-epp", "llm-d-coordinator", "redis"):
        if not kubectl_out(["get", "svc", svc, "-n", cfg.namespace, "-o", "name"]):
            problems.append(f"service {svc} not found")
    if problems:
        raise RuntimeError("preflight failed (was the guide's scripts/nightly-deploy-gke.sh run?): " + "; ".join(problems))
    log("preflight ok: vLLM, coordinator, objectives and services present")


# ---------------------------------------------------------------------------
# llm-d-benchmark
# ---------------------------------------------------------------------------

def find_cli(clone: Path) -> list[str] | None:
    """Locate the llmdbenchmark entry point.

    install.sh --no-uv / --uv install into <clone>/.venv (what this validator
    uses); -y or an active venv install elsewhere, and the script may or may not
    be on PATH.
    """
    import sysconfig
    candidates = [
        clone / ".venv" / "bin" / "llmdbenchmark",
        Path(sysconfig.get_path("scripts")) / "llmdbenchmark",
        Path(sys.executable).parent / "llmdbenchmark",
        Path.home() / ".local" / "bin" / "llmdbenchmark",
    ]
    found = shutil.which("llmdbenchmark")
    if found:
        candidates.insert(1, Path(found))
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return [str(c)]
    probe = subprocess.run([sys.executable, "-c", "import llmdbenchmark"], capture_output=True, text=True)
    if probe.returncode == 0:
        return [sys.executable, "-m", "llmdbenchmark"]
    return None


def install_cli(cfg: Config) -> tuple[Path, list[str]]:
    clone = cfg.workdir / "llm-d-benchmark"
    cli = find_cli(clone) if clone.exists() else None
    if cli:
        log(f"reusing llmdbenchmark: {' '.join(cli)}")
        return clone, cli
    if cfg.skip_install:
        raise RuntimeError(f"AMT_SKIP_INSTALL set but no llmdbenchmark found for {clone}")
    cfg.workdir.mkdir(parents=True, exist_ok=True)
    extra_env: dict[str, str] = {}
    if cfg.bench_repo and not clone.exists():
        # install.sh installs from an existing ./llm-d-benchmark checkout when
        # one is present, so cloning the requested source first is enough.
        clone_cmd = ["git", "clone", "--depth", "1"] + (["--branch", cfg.bench_ref] if cfg.bench_ref else [])
        log(f"cloning {cfg.bench_repo} ({cfg.bench_ref or 'default branch'}) into {clone}")
        if stream(clone_cmd + [cfg.bench_repo, str(clone)], cfg.workdir, cfg.workdir / "clone.log") != 0:
            raise RuntimeError(f"could not clone {cfg.bench_repo}; see {cfg.workdir / 'clone.log'}")
    elif cfg.bench_ref:
        extra_env["LLMDBENCH_BRANCH"] = cfg.bench_ref
    source = cfg.bench_repo or "llm-d/llm-d-benchmark"
    branch = cfg.bench_ref or os.environ.get("LLMDBENCH_BRANCH", "main")
    log(f"installing llm-d-benchmark into {clone} ({source}, branch {branch})")
    # A virtual environment inside the clone, not the system Python: runner images
    # mark the system Python externally managed (PEP 668) and refuse pip installs.
    # --no-uv uses python3 -m venv; --uv is the fallback (uv can fetch a Python).
    rc, cli = 1, None
    for mode in ("--no-uv", "--uv"):
        rc = stream(["bash", "-c", f"curl -sSL {INSTALL_URL} | bash -s -- {mode}"], cfg.workdir,
                    cfg.workdir / f"install{mode.replace('--', '-')}.log", extra_env or None)
        cli = find_cli(clone)
        if rc == 0 and cli:
            return clone, cli
        log(f"install.sh {mode} failed (rc={rc}, cli={cli})")
    raise RuntimeError(f"llm-d-benchmark install failed (rc={rc}, cli={cli}); see {cfg.workdir}/install-*.log")


def install_profiles(clone: Path) -> None:
    """Make sure the two profiles exist in the clone.

    A profile tracked by the clone's git is upstream's and wins; an untracked one
    is a copy this script installed earlier and is refreshed from the bundle.
    """
    dest_dir = clone / "workload" / "profiles" / "inference-perf"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(PROFILES_DIR.glob("*.yaml.in")):
        dest = dest_dir / src.name
        tracked = subprocess.run(["git", "-C", str(clone), "ls-files", "--error-unmatch", str(dest)],
                                 capture_output=True, text=True).returncode == 0
        if tracked:
            log(f"profile {src.name}: using upstream's copy from the clone")
        elif dest.exists() and dest.read_bytes() == src.read_bytes():
            log(f"profile {src.name}: bundled copy already installed")
        else:
            shutil.copyfile(src, dest)
            log(f"profile {src.name}: installed bundled copy into {dest_dir}")


def async_rate_for(cfg: Config, capacity: int) -> float:
    """Async arrivals per second: AMT_ASYNC_RATE, else twice what the pool completes.

    The async member must keep a backlog queued for the whole stage. The pool
    completes about capacity / service time requests per second, so arrivals at
    twice that keep llm-d-async's queue full on any GPU without generating more
    requests than the harness can post-process quickly; the floor of 10 covers
    slow GPUs where that product is small.
    """
    if cfg.async_rate is not None:
        return cfg.async_rate
    return float(max(10, round(2 * capacity / cfg.service_seconds)))


def render_experiment(cfg: Config, capacity: int, levels: tuple[int, ...] | None = None,
                      include_async_only: bool = True, name: str = "experiment.yaml") -> Path:
    levels = cfg.levels if levels is None else levels
    params = experiment.ExperimentParams(
        capacity=capacity, levels=levels,
        realtime_seconds=cfg.realtime_seconds, service_seconds=cfg.service_seconds,
        async_rate=async_rate_for(cfg, capacity), async_duration=cfg.async_duration, async_timeout=cfg.async_timeout,
        realtime_num_requests=cfg.realtime_num_requests, include_async_only=include_async_only,
    )
    path = cfg.workdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(experiment.render_experiment(params))
    log(f"experiment rendered to {path} (levels {list(levels)}, async-only {include_async_only}, C={capacity}, "
        f"async stage {cfg.async_duration}s at {params.async_rate:g}/s)")
    return path


def run_experiment(cfg: Config, clone: Path, cli: list[str], exp_path: Path,
                   log_name: str = "llmdbenchmark.log") -> int:
    cmd = [
        *cli, "--workspace", str(cfg.workspace), "--spec", "gpu", "run",
        "--endpoint-url", f"http://{cfg.coordinator_host}",
        "--model", cfg.model,
        "--namespace", cfg.namespace,
        "--gateway-class", "epponly",
        "--harness", "inference-perf",
        "--workload", experiment.REALTIME_PROFILE,
        "--experiments", str(exp_path),
        # No --monitoring: the harness's in-pod collector found no model pods
        # in run-only mode; the Sampler below reads vLLM and EPP metrics itself.
        "--no-pvc",
        "--wait-timeout", str(cfg.wait_timeout),
        "--set", f"harness.resources.cpu={cfg.harness_cpu}",
        "--set", f"harness.resources.memory={cfg.harness_memory}",
        "--set", f"harness.resources.memoryLimit={cfg.harness_memory_limit}",
    ] + cfg.extra_args
    extra_env = {"PYTHONUNBUFFERED": "1"}  # stream the CLI's progress into the CI log as it happens
    if os.environ.get("HF_TOKEN") and not os.environ.get("LLMDBENCH_HF_TOKEN"):
        extra_env["LLMDBENCH_HF_TOKEN"] = os.environ["HF_TOKEN"]
    log(f"running llmdbenchmark with {exp_path.name} (the full matrix takes ~40 minutes)")
    return stream(cmd, clone, cfg.workdir / log_name, extra_env)


def collect_results(cfg: Config) -> dict[str, Path]:
    dirs = v.find_results_dirs(str(cfg.workspace), cfg.namespace) or []
    found: dict[str, Path] = {}
    stems = [p[:-len(".yaml")] for p in (experiment.REALTIME_PROFILE, experiment.ASYNC_PROFILE)]
    for d in dirs:
        meta = analysis.read_flat_yaml(d / "run_metadata.yaml")
        name = analysis.treatment_name(meta, stems)
        if name:
            found[name] = d
        else:
            log(f"results dir with no recognisable treatment name (ignored): {d}")
    log(f"results matched: {sorted(found)}")
    return found


# ---------------------------------------------------------------------------
# Cluster sampler
# ---------------------------------------------------------------------------

SAMPLE_SEPARATOR = "---AMT-EPP---"


class Sampler(threading.Thread):
    """Poll vLLM and EPP metrics from a long-lived curl pod while the run is on.

    Every ``interval`` seconds one ``kubectl exec`` fetches the few metric
    families the checks need (vLLM running/waiting requests, EPP pool
    saturation and per-band request counters). Samples carry wall-clock
    timestamps so they can be attributed to treatment windows afterwards.
    """

    def __init__(self, cfg: Config) -> None:
        super().__init__(name="amt-sampler", daemon=True)
        self.cfg = cfg
        self.pod = f"amt-sampler-{os.getpid()}"
        self.samples: list[analysis.Sample] = []
        self.errors = 0
        # Not `_stop`: threading.Thread has a private _stop() that join() calls on
        # Python <= 3.12, and an Event in its place breaks join().
        self._halt = threading.Event()
        self._vllm_ips: list[str] = []

    # -- pod lifecycle -----------------------------------------------------
    def start_pod(self) -> None:
        ns = self.cfg.namespace
        kubectl(["delete", "pod", self.pod, "-n", ns, "--ignore-not-found", "--wait=false"])
        r = kubectl(["run", self.pod, "--restart=Never", "--image=curlimages/curl", "-n", ns,
                     "--command", "--", "sleep", "14400"], timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"could not create sampler pod: {r.stderr.strip()[:300]}")
        w = kubectl(["wait", "pod", self.pod, "-n", ns, "--for=condition=Ready", "--timeout=180s"], timeout=200)
        if w.returncode != 0:
            raise RuntimeError(f"sampler pod not ready: {w.stderr.strip()[:300]}")
        self._vllm_ips = kubectl_out(["get", "pods", "-n", ns, "-l", self.cfg.vllm_selector,
                                      "--field-selector=status.phase=Running",
                                      "-o", "jsonpath={.items[*].status.podIP}"]).split()
        if not self._vllm_ips:
            raise RuntimeError(f"no running vLLM pods match -l {self.cfg.vllm_selector}")
        log(f"sampler pod {self.pod} ready; vLLM pods {self._vllm_ips}")

    def cleanup(self) -> None:
        kubectl(["delete", "pod", self.pod, "-n", self.cfg.namespace, "--ignore-not-found", "--wait=false"])

    # -- scraping ------------------------------------------------------------
    def _exec(self, script: str, timeout: int = 40) -> subprocess.CompletedProcess:
        return kubectl(["exec", self.pod, "-n", self.cfg.namespace, "--", "sh", "-c", script], timeout=timeout)

    def _epp_url(self) -> str:
        return f"http://{self.cfg.epp_host}:{self.cfg.epp_metrics_port}/metrics"

    def sample_once(self) -> analysis.Sample | None:
        vllm_cmds = " ; ".join(
            f"curl -sS --max-time 4 http://{ip}:{self.cfg.vllm_port}/metrics | grep -E '^vllm:num_requests_(running|waiting)[ {{]'"
            for ip in self._vllm_ips)
        script = (f"{vllm_cmds} ; echo {SAMPLE_SEPARATOR} ; "
                  f"curl -sS --max-time 4 {self._epp_url()} | grep -E '^llm_d_epp_flow_control_(pool_saturation|requests_total)[ {{]'")
        t = time.time()
        r = self._exec(script)
        if r.returncode != 0 and not r.stdout.strip():
            return None
        vllm_text, _, epp_text = r.stdout.partition(SAMPLE_SEPARATOR)
        return analysis.parse_sample(t, vllm_text, epp_text)

    def scrape_epp_full(self) -> str:
        r = self._exec(f"curl -sS --max-time 20 {self._epp_url()}", timeout=60)
        return r.stdout if r.returncode == 0 else ""

    def run(self) -> None:  # thread body
        while not self._halt.is_set():
            started = time.time()
            try:
                s = self.sample_once()
                if s is None:
                    self.errors += 1
                else:
                    self.samples.append(s)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                log(f"sampler error: {exc}")
            self._halt.wait(max(0.5, self.cfg.sample_interval - (time.time() - started)))

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=60)
        log(f"sampler stopped: {len(self.samples)} samples, {self.errors} errors")


def coordinator_logs(cfg: Config) -> str:
    return kubectl(["logs", "deploy/llm-d-coordinator", "-n", cfg.namespace, "--tail=3000"], timeout=120).stdout


# ---------------------------------------------------------------------------
# Analysis and reporting
# ---------------------------------------------------------------------------

def analyze(cfg: Config, capacity: int, results: dict[str, Path], samples: list,
            epp_text: str) -> tuple[dict, list]:
    e = os.environ
    warm = analysis.tolerance("WARMUP_S", e)
    checks: list = []
    report: dict = {"namespace": cfg.namespace, "model": cfg.model, "capacity": capacity,
                    "levels": [], "async_only": None, "dry_run": cfg.dry_run, "samples": len(samples)}

    def need(name: str) -> Path | None:
        d = results.get(name)
        if d is None:
            checks.append(analysis.Check(f"results for {name}", False,
                                         "no treatment results matched a treatment name"))
        return d

    def sat_for(d: Path) -> analysis.SaturationSummary:
        meta = analysis.read_flat_yaml(d / "run_metadata.yaml")
        return analysis.saturation_summary(samples, analysis.epoch_window_from_metadata(meta, warm))

    # "Pool held at capacity" results go to `observations` (reported, never
    # failing) unless AMT_ENFORCE_CAPACITY is set; see analysis.capacity_enforced.
    observations: list = []
    capacity_results = checks if analysis.capacity_enforced(e) else observations

    level_summaries: dict[int, analysis.Summary] = {}
    for level in cfg.levels:
        k = experiment.concurrency_for_level(level, capacity)
        b_dir, m_dir, a_dir = need(f"baseline_{level}"), need(f"mixed_{level}_rt"), need(f"mixed_{level}_async")
        if not (b_dir and m_dir and a_dir):
            continue
        base_recs = analysis.load_treatment(b_dir)
        base = analysis.summarize(base_recs, analysis.baseline_window(base_recs, warm))
        rt_recs = analysis.load_treatment(m_dir)
        as_recs = analysis.load_treatment(a_dir)
        window = analysis.overlap_window(rt_recs, as_recs, warm)
        mixed = analysis.summarize(rt_recs, window)
        async_in_window = analysis.summarize(as_recs, window)
        async_ok = async_in_window.count - async_in_window.errors
        level_summaries[level] = base
        async_rps = analysis.dispatch_rate(samples, window, analysis.ASYNC_PRIORITIES)
        checks += analysis.compare_level(level, base, mixed, e, async_ok, async_rps)
        checks.append(analysis.streaming_sanity(level, base, e))
        mixed_sat = analysis.saturation_summary(samples, window)
        if window is not None:
            capacity_results.append(analysis.capacity_check(f"L{level} pool held at capacity", mixed_sat, capacity, e, level))
        row = analysis.level_table_row(level, k, base, mixed, async_ok, sat_for(b_dir), mixed_sat)
        row["async_dispatch_rps"] = async_rps
        report["levels"].append(row)

    a_dir = need("async_only")
    if a_dir:
        recs = analysis.load_treatment(a_dir)
        summary = analysis.summarize(recs, analysis.baseline_window(recs, warm))
        sat = sat_for(a_dir)
        a_meta = analysis.read_flat_yaml(a_dir / "run_metadata.yaml")
        a_window = analysis.epoch_window_from_metadata(a_meta, warm)
        epp_rps = analysis.dispatch_rate(samples, a_window, analysis.ASYNC_PRIORITIES)
        baseline100 = level_summaries.get(100)
        checks += analysis.async_only_checks(sat, summary, baseline100, capacity, e, epp_rps)
        capacity_results.append(analysis.capacity_check("async-only pool held at capacity", sat, capacity, e))
        report["async_only"] = {
            "completions": summary.count - summary.errors, "errors": summary.errors,
            "window_s": summary.window_s, "rps": summary.rps, "epp_dispatch_rps": epp_rps,
            "running_max": sat.running_max, "running_p50": sat.running_p50,
            "saturation_max": sat.saturation_max, "saturation_p50": sat.saturation_p50,
            "samples": sat.count,
        }

    counts = analysis.merge_band_counts(analysis.band_counts_from_samples(samples), analysis.band_counts(epp_text))
    checks += analysis.band_checks(counts)
    report["band_requests"] = {str(k): v for k, v in counts.items()}
    report["checks"] = analysis.checks_to_dicts(checks)
    report["observations"] = analysis.checks_to_dicts(observations)
    return report, checks


def write_outputs(cfg: Config, report: dict, results: dict[str, Path], epp_text: str, exp_path: Path | None,
                  samples: list | None = None) -> None:
    out = cfg.results_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    (out / "epp-metrics.txt").write_text(epp_text)
    (out / "samples.json").write_text(json.dumps([s.as_dict() for s in (samples or [])], indent=1))
    for pattern in ("llmdbenchmark*.log", "install*.log", "clone.log", "experiment-retry*.yaml"):
        for src in sorted(cfg.workdir.glob(pattern)):
            shutil.copyfile(src, out / src.name)
    if exp_path and exp_path.exists():
        shutil.copyfile(exp_path, out / "experiment.yaml")
    for name, d in results.items():
        dest = out / "results" / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(d, dest, ignore=shutil.ignore_patterns("*.pvc", "*.bin"))
    (out / "coordinator.log").write_text(coordinator_logs(cfg))
    md = analysis.render_markdown(report)
    (out / "summary.md").write_text(md)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as fh:
            fh.write(md)
    log(f"artifacts written to {out}")


def retry_invalid_levels(cfg: Config, capacity: int, clone: Path, cli: list[str],
                         results: dict[str, Path]) -> dict[str, Path]:
    """Re-run, once per allowed retry, the levels whose comparison is unusable.

    A level is re-run whole (baseline and mixed) when a treatment is missing or
    its mixed members barely overlapped; async-only is re-run when missing.
    A re-run level replaces the earlier one only if all its treatments came
    back, so a level's members always come from the same run.
    """
    if cfg.dry_run:
        return results
    for attempt in range(1, cfg.retries + 1):
        records = {name: analysis.load_treatment(d) for name, d in results.items()}
        levels, async_only = analysis.rerun_plan(cfg.levels, records, os.environ)
        if not levels and not async_only:
            return results
        log(f"retry {attempt}/{cfg.retries}: re-running levels {levels}" + (" and async-only" if async_only else ""))
        exp = render_experiment(cfg, capacity, tuple(levels), include_async_only=async_only,
                                name=f"experiment-retry{attempt}.yaml")
        rc = run_experiment(cfg, clone, cli, exp, log_name=f"llmdbenchmark-retry{attempt}.log")
        log(f"llmdbenchmark (retry {attempt}) exited {rc}")
        results, notes = analysis.merge_rerun(results, collect_results(cfg), levels, async_only)
        for note in notes:
            log(f"retry {attempt}: {note}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--namespace", required=True)
    ap.add_argument("-m", "--model", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="one short baseline group; report only")
    ap.add_argument("--analyze-only", action="store_true",
                    help="skip the benchmark run; analyze the newest results already in the workspace")
    ap.add_argument("--set", dest="settings", action="append", default=[], metavar="KEY=VALUE",
                    help="validator setting, e.g. AMT_SERVICE_S=10 (repeatable; overrides the environment)")
    args = ap.parse_args()
    load_settings(args.settings)
    cfg = Config(args)

    exp_path: Path | None = None
    results: dict[str, Path] = {}
    epp_text = ""
    samples: list = []
    sampler: Sampler | None = None
    try:
        preflight(cfg)
        if not cfg.model:
            cfg.model = discover_model(cfg.namespace)
            if not cfg.model:
                raise RuntimeError("could not discover the model from deploy/vllm; pass -m")
        capacity = detect_capacity(cfg)
        log(f"model {cfg.model}; pool capacity C={capacity}; levels {list(cfg.levels)}"
            + (" (dry run)" if cfg.dry_run else ""))
        sampler = Sampler(cfg)
        sampler.start_pod()
        if args.analyze_only:
            rc = 0
            exp_path = cfg.workdir / "experiment.yaml"
            samples_path = cfg.results_dir / "samples.json"
            if samples_path.exists():
                samples = [analysis.Sample(t=s["t"], running=s.get("running"), waiting=s.get("waiting"),
                                           saturation=s.get("saturation"),
                                           band_requests={int(k): v for k, v in (s.get("band_requests") or {}).items()})
                           for s in json.loads(samples_path.read_text())]
            log(f"analyze-only: reusing the newest results in the workspace ({len(samples)} saved samples)")
        else:
            clone, cli = install_cli(cfg)
            install_profiles(clone)
            for stale in [*cfg.workdir.glob("llmdbenchmark-retry*.log"), *cfg.workdir.glob("experiment-retry*.yaml")]:
                stale.unlink()  # left by an earlier local run in the same workdir
            exp_path = render_experiment(cfg, capacity)
            sampler.start()
            try:
                rc = run_experiment(cfg, clone, cli, exp_path)
                log(f"llmdbenchmark exited {rc}")
                results = collect_results(cfg)
                results = retry_invalid_levels(cfg, capacity, clone, cli, results)
            finally:
                sampler.stop()
            samples = list(sampler.samples)
        if not results:
            results = collect_results(cfg)
        epp_text = sampler.scrape_epp_full()
        report, checks = analyze(cfg, capacity, results, samples, epp_text)
        report["llmdbenchmark_rc"] = rc
    except Exception as exc:  # noqa: BLE001 - surface everything with artifacts
        log(f"ERROR: {exc}")
        report = {"error": str(exc), "checks": [], "levels": [], "capacity": None}
        checks = [analysis.Check("validator ran to completion", False, str(exc))]
        try:
            write_outputs(cfg, report, results, epp_text, exp_path, samples)
        except Exception as inner:  # noqa: BLE001
            log(f"could not write artifacts: {inner}")
        return 1
    finally:
        if sampler is not None:
            if sampler.is_alive():
                sampler.stop()
            sampler.cleanup()

    write_outputs(cfg, report, results, epp_text, exp_path, samples)
    print()
    print(f"=== Async multi-tenant isolation — namespace {cfg.namespace}, C={capacity} ===")
    analysis.print_checks_table(checks)
    observations = report.get("observations", [])
    if observations:
        print("\nInformational (does not fail the run; AMT_ENFORCE_CAPACITY=1 makes these checks):")
        for o in observations:
            print(f"  {'OK  ' if o['passed'] else 'OVER'}  {o['name']}: {o['detail']}")
    print()
    failed = [c for c in checks if not c.passed]
    expected = set(experiment.treatment_names(cfg.levels))
    missing = sorted(expected - set(results))
    if missing:
        print(f"FAIL: missing treatment results: {missing}", file=sys.stderr)
        return 1
    if failed and cfg.enforce:
        print(f"FAIL: {len(failed)}/{len(checks)} check(s) failed", file=sys.stderr)
        return 1
    if failed:
        print(f"REPORT ONLY (dry run): {len(failed)}/{len(checks)} check(s) would fail")
        return 0
    print(f"PASS: {len(checks)} check(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
