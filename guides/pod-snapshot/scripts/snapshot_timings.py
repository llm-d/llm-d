#!/usr/bin/env python3
"""Startup, snapshot and restore timings for GKE Pod Snapshots (vLLM and SGLang).

Used by the nightly through check_thresholds.py (from .github/scripts/e2e/e2e-validate-pod-snapshot.sh),
which reads the cluster in one pass with load_pods (kubectl get pods/events/podsnapshots, then one
kubectl logs per pod; no watching or polling), prints and writes the per-phase metrics as a 5-column
CSV (Seq, ModelServer, Mode, MetricName, MetricValue; Seq is always 1 in the nightly) with
print_report, and then evaluates the alerting thresholds with the compute_*_metrics helpers.

Provenance: this file started as a trimmed copy of the demo's demo_phases.py (the
pod-snapshot-sglang-demo branch, docker/scripts/snapshot/sglang/demo/demo_phases.py), which also
has a live `watch` mode, terminal colors, per-milestone narration, a cold-start cache and a
multi-iteration results.csv/.R writer. THIS FILE IS CANONICAL FOR THE NIGHTLY: the two are not
kept in sync, and when the snapshot hooks change their log lines the regexes below are updated
here against the images the nightly runs. The CSV header and metric names match the demo's
results.csv so the two can still be compared.

The helper only reads from the cluster: it runs kubectl get and kubectl logs.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import pathlib
import re
import subprocess
import sys

KUBECTL_TIMEOUT_S = 60          # per kubectl process, also covers the gke-gcloud-auth-plugin exec
KUBECTL_REQUEST_TIMEOUT = "30s"  # per API request, passed as --request-timeout

# One regex per phase per engine, matching only the fixed part of each line: the numbers in the
# lines are not used, and the image tags come from
# guides/recipes/modelserver/components/images/gpu-{sglang,vllm}/release (bumped by other PRs).
# A line that stops matching fails the nightly's phase-marker check rather than silently
# collapsing a phase.
#
# SGLang (docker.io/lmsysorg/sglang + docker/scripts/snapshot/sglang/)
WEIGHTS_BEGIN_RE = re.compile(r"Load weight begin\.")
WEIGHTS_END_RE = re.compile(r"Load weight end\.")
KV_RE = re.compile(r"KV Cache is allocated\.")
GRAPH_END_RE = re.compile(r"Capture .*cuda graph end\.", re.IGNORECASE)
# Logged by the wrapper's warmup hook, so it is absent if --skip-server-warmup is ever added.
HOLD_RE = re.compile(r"Holding server_status at Starting")
STATUS_RESTORED_RE = re.compile(r"Restored server_status to ")

# vLLM (docker.io/vllm/vllm-openai + docker/scripts/snapshot/vllm/)
VLLM_WEIGHTS_BEGIN_RE = re.compile(r"Loading model from scratch\.\.\.")
VLLM_WEIGHTS_END_RE = re.compile(r"Model loading took ")
VLLM_KV_GIB_RE = re.compile(r"Available KV cache memory: ")
VLLM_GRAPH_END_RE = re.compile(r"Graph captur(?:e|ing) finished in ")
VLLM_SLEEP_REQ_RE = re.compile(r"Executing engine\.sleep\(level=1\) to release physical VRAM")
VLLM_WAKE_DONE_RE = re.compile(r" seconds to wake up")

# Shared snapshot wrapper lines (docker/scripts/snapshot/{vllm,sglang}/wrapper.py). Both wrappers
# log the trigger line right after the GPU memory release returns (engine.sleep for vLLM, POST
# /release_memory_occupation for SGLang), so it marks the end of 4c without depending on the
# engines' wording or on uvicorn's access log. A restored pod's log starts at the checkpoint
# line, so the trigger line only appears in a cold-start log.
TRIGGER_RE = re.compile(r"Triggering snapshot checkpoint")
CHECKPOINT_OK_RE = re.compile(r"Snapshot checkpoint created successfully")

COLD_MARKERS = (
    "weights_begin_ts",
    "weights_end_ts",
    "kv_ts",
    "graph_end_ts",
    "warmup_done_ts",
    "released_ts",
    "checkpoint_ts",
    "up_ts",
)
RESTORE_MARKERS = ("restore_ts", "up_ts")

TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$")


def parse_ts(value):
    """Parses a Kubernetes RFC 3339 timestamp (nanoseconds allowed); returns None if invalid."""
    if not isinstance(value, str):
        return None
    m = TS_RE.match(value.strip())
    if not m:
        return None
    base, frac, zone = m.groups()
    text = base + ("." + frac[:6].ljust(6, "0") if frac else "") + ("+00:00" if zone == "Z" else zone)
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def delta(start, end):
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


class Kubectl:
    """Runs read-only kubectl commands in one namespace; raises RuntimeError on any failure."""

    def __init__(self, namespace):
        self.namespace = namespace

    def run(self, *args):
        cmd = ["kubectl", *args, "-n", self.namespace, f"--request-timeout={KUBECTL_REQUEST_TIMEOUT}"]
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=KUBECTL_TIMEOUT_S,
                check=True,
                stdin=subprocess.DEVNULL,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            # CalledProcessError/TimeoutExpired do not include stderr in str(exc); kubectl's
            # reason (missing CRD, RBAC, container not started) is what on-call needs.
            detail = (getattr(exc, "stderr", None) or "").strip() or str(exc)
            raise RuntimeError(f"kubectl {' '.join(args)} -n {self.namespace} failed: {detail}") from exc

    def json(self, *args):
        out = self.run(*args, "-o", "json")
        try:
            return json.loads(out)
        except ValueError as exc:
            raise RuntimeError(f"kubectl {' '.join(args)} -n {self.namespace} returned invalid JSON: {exc}") from exc


def conditions(pod):
    return {
        c.get("type"): parse_ts(c.get("lastTransitionTime"))
        for c in pod.get("status", {}).get("conditions", [])
        if c.get("status") == "True"
    }


def event_ts(event):
    """The event's timestamp, or None when it has none (such an event is ignored)."""
    for value in (event.get("firstTimestamp"), event.get("eventTime"), event.get("lastTimestamp"),
                  event.get("metadata", {}).get("creationTimestamp")):
        ts = parse_ts(value)
        if ts:
            return ts
    return None


def log_values(log_text):
    """Returns the timestamps parsed from one container's `kubectl logs --timestamps` output.

    Keys ending in `_ts` are datetimes used by compute_cold_metrics / compute_restore_metrics;
    `restored` is set when the log belongs to a pod that was restored from the snapshot. A
    restored pod's log starts at the checkpoint line, so a checkpoint line without cold-start
    markers before it means a restore.
    """
    values = {}
    cold = False

    for raw in log_text.split("\n"):
        stamp, _, text = raw.partition(" ")
        ts = parse_ts(stamp)
        if ts is None or '"GET /' in text:
            continue

        if WEIGHTS_BEGIN_RE.search(text) or VLLM_WEIGHTS_BEGIN_RE.search(text):
            cold = True
            values["weights_begin_ts"] = ts
        elif WEIGHTS_END_RE.search(text) or VLLM_WEIGHTS_END_RE.search(text):
            cold = True
            values["weights_end_ts"] = ts
        elif KV_RE.search(text) or VLLM_KV_GIB_RE.search(text):
            values["kv_ts"] = ts
        elif GRAPH_END_RE.search(text) or VLLM_GRAPH_END_RE.search(text):
            values["graph_end_ts"] = ts
        elif HOLD_RE.search(text) or VLLM_SLEEP_REQ_RE.search(text):
            cold = True
            values["warmup_done_ts"] = ts
        elif TRIGGER_RE.search(text):
            cold = True
            values["released_ts"] = ts
        elif CHECKPOINT_OK_RE.search(text):
            if cold:
                values["checkpoint_ts"] = ts
            elif "restore_ts" not in values:
                values.update(restored=True, restore_ts=ts)
        elif STATUS_RESTORED_RE.search(text) or VLLM_WAKE_DONE_RE.search(text):
            values["up_ts"] = ts
    return values


def pod_times(kubectl, pod, events):
    meta = pod.get("metadata", {})
    name = meta.get("name", "")
    conds = conditions(pod)
    statuses = pod.get("status", {}).get("containerStatuses") or [{}]
    running = (statuses[0].get("state") or {}).get("running") or {}
    containers = pod.get("spec", {}).get("containers") or [{}]
    times = {
        "name": name,
        "created": parse_ts(meta.get("creationTimestamp")),
        "scheduled": conds.get("PodScheduled"),
        "sandbox": conds.get("PodReadyToStartContainers"),
        "restored": conds.get("PodRestored"),
        "ready": conds.get("Ready"),
        "started": parse_ts(running.get("startedAt")),
        "pulling_ts": None,
        "pulled_ts": None,
    }
    # Events expire after about an hour; without them, 1c is 0s and pull time lands in 1b/1d.
    for event in sorted((e for e in events if event_ts(e)), key=event_ts):
        if event.get("involvedObject", {}).get("name") != name:
            continue
        if event.get("reason") == "Pulling" and times["pulling_ts"] is None:
            times["pulling_ts"] = event_ts(event)
        elif event.get("reason") == "Pulled" and times["pulled_ts"] is None:
            times["pulled_ts"] = event_ts(event)
    times["log"] = log_values(kubectl.run("logs", name, "-c", containers[0].get("name", ""), "--timestamps"))
    return times


def snapshot_ready(snaps, pod):
    """The latest time a PodSnapshot reached Ready at or after Pod 1 (`pod`) was created, or None.

    The deploy script allows the PodSnapshot up to 600s after Pod 1 is Ready, so there is no
    upper bound; the lower bound only rules out a snapshot left over from an earlier run.
    """
    if not pod or not pod.get("created"):
        return None
    ready = [
        parse_ts(c.get("lastTransitionTime"))
        for snap in snaps
        for c in snap.get("status", {}).get("conditions", [])
        if c.get("type") == "Ready" and c.get("status") == "True"
    ]
    in_window = [ts for ts in ready if ts and ts >= pod["created"]]
    return max(in_window) if in_window else None


def is_restored(pod):
    return bool(pod["restored"] or pod["log"].get("restored"))


# --- Metrics: the unified 1..4f hierarchy ---------------------------------------------------------
#
# 1  Kubernetes: schedule the pod and start the container   1a..1d
# 2  LLM engine cold start                                   2c..2g     (Pod 1 only)
# 3  Wait for the readiness probe
# 4  Take the snapshot                                       4c, 4e, 4f (Pod 1; Pod 2 has 4e, 4f)
#
# The numbered steps add up to pod created -> Ready and the lettered sub-steps add up to their
# step, so each boundary is clamped to be no earlier than the previous one.


def compute_k8s_metrics(pod):
    """Computes contiguous Kubernetes sub-step durations (1, 1a..1d)."""
    t0 = pod["created"]
    t_1a = max(t0, pod.get("scheduled") or t0)
    t_1b = max(t_1a, pod.get("sandbox") or pod.get("pulling_ts") or t_1a)
    t_1c = max(t_1b, pod.get("pulled_ts") or t_1b)
    t_1d = max(t_1c, pod.get("started") or t_1c)
    return t_1d, {
        "1": (t_1d - t0).total_seconds(),
        "1a": (t_1a - t0).total_seconds(),
        "1b": (t_1b - t_1a).total_seconds(),
        "1c": (t_1c - t_1b).total_seconds(),
        "1d": (t_1d - t_1c).total_seconds(),
    }


def compute_cold_metrics(pod):
    """Computes contiguous ColdStart metrics (1, 1a..1d, 2, 2c..2g, 3, 4, 4c, 4e, 4f) for Pod 1."""
    t_1d, k8s = compute_k8s_metrics(pod)
    log = pod["log"]
    t_2c = max(t_1d, log.get("weights_begin_ts") or t_1d)
    t_2d = max(t_2c, log.get("weights_end_ts") or t_2c)
    t_2e = max(t_2d, log.get("kv_ts") or t_2d)
    t_2f = max(t_2e, log.get("graph_end_ts") or t_2e)
    t_2g = max(t_2f, log.get("warmup_done_ts") or t_2f)

    t_4c = max(t_2g, log.get("released_ts") or t_2g)
    t_4e = max(t_4c, log.get("checkpoint_ts") or t_4c)
    t_4f = max(t_4e, log.get("up_ts") or t_4e)
    t_ready = max(t_4f, pod.get("ready") or t_4f)

    m = {
        **k8s,
        "2": (t_2g - t_1d).total_seconds(),
        "2c": (t_2c - t_1d).total_seconds(),
        "2d": (t_2d - t_2c).total_seconds(),
        "2e": (t_2e - t_2d).total_seconds(),
        "2f": (t_2f - t_2e).total_seconds(),
        "2g": (t_2g - t_2f).total_seconds(),
        "3": (t_ready - t_4f).total_seconds(),
        "4": (t_4f - t_2g).total_seconds(),
        "4c": (t_4c - t_2g).total_seconds(),
        "4e": (t_4e - t_4c).total_seconds(),
        "4f": (t_4f - t_4e).total_seconds(),
    }
    m["ecs"] = m["1"] + m["2"] + m["3"]
    m["cs"] = m["1b"] + m["1c"] + m["1d"] + m["2"]
    return m


def compute_restore_metrics(pod):
    """Computes contiguous Restore metrics (1, 1a..1d, 4e, 4f, 3) for Pod 2."""
    t_1d, k8s = compute_k8s_metrics(pod)
    log = pod["log"]
    t_4e = max(t_1d, log.get("restore_ts") or pod.get("restored") or t_1d)
    t_4f = max(t_4e, log.get("up_ts") or t_4e)
    t_ready = max(t_4f, pod.get("ready") or t_4f)
    m = {
        **k8s,
        "4e": (t_4e - t_1d).total_seconds(),
        "4f": (t_4f - t_4e).total_seconds(),
        "3": (t_ready - t_4f).total_seconds(),
    }
    m["wsr"] = m["1b"] + m["1c"] + m["4e"] + m["4f"] + m["3"]
    return m


MODEL_SERVER_NAMES = {"vllm": "vLLM", "sglang": "SGLang"}  # the CSV's ModelServer column

# Labels of the 1..4f hierarchy for the CSV.
STEP_LABELS = {
    "1": "Kubernetes: schedule the pod and start the container",
    "1a": "Wait for a GPU node",
    "1b": "Set up the pod sandbox (gvisor)",
    "1c": "Pull the image (image streaming)",
    "1d": "Start the container",
    "2": "LLM Engine cold start (vLLM / SGLang)",
    "2c": "Start Python, import engine, initialize workers",
    "2d": "Download and load model weights",
    "2e": "Allocate KV cache memory",
    "2f": "Capture CUDA graphs / compile execution graph",
    "2g": "Start API server / frontend and run warm-up requests",
    "3": "Wait for the readiness probe",
    "4": "Take the snapshot (phase 5)",  # the demo's results.csv name, kept for comparability
    "4c": "Release GPU memory (weights + KV cache) to CPU RAM",
    "4e": "gVisor checkpoint + upload to GCS",
    "4f": "Resume GPU memory (weights + KV cache)",
}


def csv_rows(cold_m, rest_m, p1, snap_ready, engine):
    """Returns (ModelServer, Mode, MetricName, MetricValue) tuples: the 1..4f hierarchy of each
    pod, the composite metrics, and Pod 1's total and PodSnapshot Ready time."""
    model_server = MODEL_SERVER_NAMES[engine]
    out = []

    def add(mode, name, val):
        if val is not None:
            out.append((model_server, mode, name, float(val)))

    for mode, m in (("ColdStart", cold_m), ("Restore", rest_m)):
        if m is not None:
            for key, label in STEP_LABELS.items():
                if key in m:
                    add(mode, f"{key}. {label}", m[key])
    if cold_m is not None:
        add("ColdStart", "E2E Cold Start (including node provisioning)", cold_m["ecs"])
    if rest_m is not None:
        add("Restore", "Warm Start (from restoration)", rest_m["wsr"])
    if cold_m is not None:
        add("ColdStart", "Cold Start (excluding node provisioning)", cold_m["cs"])
        add("ColdStart", "Application Cold Start", cold_m["2"])
        add("ColdStart", "Total Snapshot Overhead Time", cold_m["4"])
        add("ColdStart", "Total: pod created -> Ready", delta(p1["created"], p1["ready"]))
        if snap_ready:
            add("ColdStart", "PodSnapshot Ready", delta(p1["created"], snap_ready))
    return out


def write_csv(rows, csv_path=""):
    """Formats rows as the 5-column results.csv and writes it to csv_path if given.

    Seq is always 1: a nightly run is a single iteration (the demo appends one per iteration).
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["Seq", "ModelServer", "Mode", "MetricName", "MetricValue"])
    for model_server, mode, name, val in rows:
        writer.writerow([1, model_server, mode, name, f"{val:.2f}"])
    text = buf.getvalue()
    if csv_path:
        try:
            path = pathlib.Path(csv_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"WARNING: could not write the CSV to {csv_path}: {exc}", file=sys.stderr)
    return text


def print_report(p1, p2, snap_ready, engine, csv_path=""):
    cold_m = compute_cold_metrics(p1) if p1 and p1.get("created") and not is_restored(p1) else None
    rest_m = compute_restore_metrics(p2) if p2 and p2.get("created") and is_restored(p2) else None
    rows = csv_rows(cold_m, rest_m, p1, snap_ready, engine)
    if not rows:
        print("No cold-start or restore metrics to report.")
        return
    print(write_csv(rows, csv_path=csv_path).rstrip("\n"))


def load_pods(kubectl, selector):
    """Reads the cluster in one pass: returns (pod1, pod2, podsnapshot_ready_ts).

    Pod 1 is the oldest live pod matching `selector` (cold start + snapshot) and Pod 2 the newest
    (restore from the snapshot); each is a pod_times dict, or None when there are fewer than one /
    two pods. Pods being deleted or in a terminal phase (Failed, e.g. evicted, or Succeeded) are
    ignored so a replaced pod is not mistaken for Pod 1. Raises RuntimeError when kubectl fails.
    """
    pods = kubectl.json("get", "pods", "-l", selector)
    items = sorted(
        (p for p in pods.get("items", [])
         if not p.get("metadata", {}).get("deletionTimestamp")
         and p.get("status", {}).get("phase") not in ("Failed", "Succeeded")),
        key=lambda p: p.get("metadata", {}).get("creationTimestamp", ""),
    )
    events = kubectl.json("get", "events", "--field-selector", "involvedObject.kind=Pod").get("items", [])
    snaps = kubectl.json("get", "podsnapshots").get("items", [])
    p1 = pod_times(kubectl, items[0], events) if items else None
    p2 = pod_times(kubectl, items[-1], events) if len(items) >= 2 else None
    return p1, p2, snapshot_ready(snaps, pod=p1)
