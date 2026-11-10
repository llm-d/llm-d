"""CPU-only checks for the opt-in tiered-prefix-cache validation contract."""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE_DIR = REPO_ROOT / "guides/tiered-prefix-cache"
WRAPPER = REPO_ROOT / ".github/scripts/e2e/e2e-validate-tiered-prefix-cache.sh"
SPEC = importlib.util.spec_from_file_location("tiered_cache_verifier", GUIDE_DIR / "verify.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def supported_overlays():
    overlays = []
    root = GUIDE_DIR / "modelserver"
    for path in root.rglob("kustomization.yaml"):
        parts = path.relative_to(root).parts
        engine_index = next((i for i, part in enumerate(parts)
                             if part in ("vllm", "sglang")), None)
        if engine_index is None or len(parts) < engine_index + 4:
            continue
        engine, connector, tier = parts[engine_index:engine_index + 3]
        if connector in ("native", "lmcache-connector", "mooncake-store") and tier in ("cpu", "fs"):
            if "mooncake-client" not in parts:
                overlays.append((path.parent, "/".join(parts[:engine_index]), engine, connector, tier))
    return sorted(overlays)


OVERLAYS = supported_overlays()
SELECTIONS = sorted({entry[1:] for entry in OVERLAYS})


def container_for(engine="vllm", connector="lmcache-connector", tier="cpu",
                  native_name="OffloadingConnector"):
    if engine == "sglang":
        args = ["--model-path=example/model", "--page-size=64",
                "--enable-hierarchical-cache"]
        if tier == "fs":
            args.append("--hicache-storage-backend=file")
        command = ["python3", "-m", "sglang.launch_server"]
    else:
        name = {"native": native_name, "lmcache-connector": "LMCacheConnectorV1",
                "mooncake-store": "MooncakeStoreConnector"}[connector]
        command = ["vllm", "serve"]
        transfer = {"kv_connector": name, "kv_role": "kv_both"}
        if connector == "native" and tier == "fs":
            transfer["kv_connector_extra_config"] = {
                "secondary_tiers": [{"type": "fs", "root_dir": "/mnt/files-storage"}],
            }
        args = ["example/model", "--kv-transfer-config",
                json.dumps(transfer)]
    return {"name": "modelserver", "command": command, "args": args}


class ModelServer:
    """Recorded API shapes and metric sequences, independent of a live engine."""

    def __init__(self, engine, connector, native_name, scenario="pass", underscore=False):
        self.engine = engine
        self.connector = connector
        self.native_name = native_name
        self.scenario = scenario
        self.underscore = underscore
        self.requests = []

    def open(self, request, timeout):
        path = urlsplit(request.full_url).path
        payload = json.loads(request.data) if request.data else None
        if path == "/v1/models":
            body = {"data": [{"id": "other/model" if self.scenario == "wrong_model"
                             else "example/model"}]}
        elif path == "/tokenize":
            body = {"count": 1206, "tokens": list(range(1206)),
                    "max_model_len": 1024 if self.scenario == "short_context" else 8192}
        elif path == "/v1/completions":
            self.requests.append(payload)
            body = {
                "choices": [{"text": "43" if self.scenario == "mismatch"
                             and len(self.requests) == 2 else "42",
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1206, "completion_tokens": 1},
            }
            if self.scenario == "application_error":
                body = {"error": {"message": "KV transfer failed"}}
        elif path == "/metrics":
            body = self.metrics()
        else:
            raise AssertionError(f"Unexpected API: {path}")
        encoded = (body if isinstance(body, str) else json.dumps(body)).encode()
        response = io.BytesIO(encoded)
        response.status = 200
        return response

    def metrics(self):
        stage = len(self.requests)
        labels = '{model_name="example/model"}'
        successful = [100, 101, 102][stage]
        if self.connector == "lmcache-connector":
            stored = [9000, 10280, 10280][stage]
            completed = [4, 5, 5][stage]
            hits = [5000, 5000, 6024][stage]
            resident = 200
            if self.scenario == "no_completed_store":
                completed = 4
            if self.scenario == "no_retrieval":
                hits = 5000
            if self.scenario == "cold_hit" and stage:
                hits += 256
            if self.scenario == "resident_hit" and stage == 2:
                resident += 1024
            if self.scenario == "rollback" and stage:
                stored = 100
            samples = {
                "lmcache:num_stored_tokens_total": stored,
                "lmcache:time_to_store_count": completed,
                "lmcache:num_hit_tokens_total": hits,
                "vllm:prefix_cache_hits_total": resident,
            }
            if self.scenario == "lazy_store_histogram" and stage == 0:
                del samples["lmcache:time_to_store_count"]
            if self.scenario == "missing_hits":
                del samples["lmcache:num_hit_tokens_total"]
            if self.scenario == "malformed":
                samples["lmcache:num_hit_tokens_total"] = "NaN"
            lines = [f"{name}{labels} {value}" for name, value in samples.items()]
            # A positive hit-rate gauge must not replace completed retrieval.
            lines.append(f"lmcache:retrieve_hit_rate{labels} 1")
        elif self.engine == "sglang":
            host = [40000, 42000, 42000][stage]
            if self.scenario in ("stale", "backup_only"):
                host = 40000
            samples = {"sglang:hicache_host_used_tokens": host}
            if self.scenario == "backup_only":
                samples["sglang:hicache_backup_tokens_total"] = [4, 5, 5][stage]
            lines = [f"{name}{labels} {value}" for name, value in samples.items()]
            lines.append(f"sglang:num_requests_total{labels} {successful}")
            return "\n".join(lines).replace("sglang:", "sglang_" if self.underscore else "sglang:")
        elif self.native_name == "OffloadingConnector":
            stored = 1000000 if self.scenario == "stale" else [1000000, 1400000, 1400000][stage]
            if self.scenario == "legacy":
                lines = [f'vllm:kv_offload_total_bytes_total{{transfer_type="GPU_to_CPU"}} {stored}',
                         'vllm:kv_offload_total_bytes_total{transfer_type="CPU_to_GPU"} 9000000']
            else:
                lines = [f"vllm:kv_offload_store_bytes_total{labels} {stored}"]
        else:
            queries = 1000 if self.scenario == "stale" else [1000, 2206, 2206][stage]
            lines = [f"vllm:external_prefix_cache_queries_total{labels} {queries}"]
        lines.append('vllm:request_success_total{model_name="example/model",'
                     f'finished_reason="stop"}} {successful}')
        return "\n".join(lines)


@pytest.fixture
def clock_and_signals(monkeypatch):
    elapsed = iter(i * 0.25 for i in range(10000))
    monkeypatch.setattr(verify.time, "time", lambda: 1000.0)
    monkeypatch.setattr(verify.time, "monotonic", lambda: next(elapsed))
    monkeypatch.setattr(verify.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(verify.signal, "signal", lambda *args: None)
    monkeypatch.setattr(verify.signal, "setitimer", lambda *args: None)


def run_probe(monkeypatch, engine="vllm", connector="lmcache-connector",
              tier="cpu", native_name="OffloadingConnector", scenario="pass",
              underscore=False, local_cpu=True, local_disk="/mnt/files-storage/kv-cache",
              runtime_overrides=None):
    server = ModelServer(engine, connector, native_name, scenario, underscore)
    monkeypatch.setattr(verify, "build_opener", lambda *args: server)
    if connector == "lmcache-connector":
        config = SimpleNamespace(chunk_size=256, local_cpu=local_cpu, local_disk=local_disk)
        module = SimpleNamespace(lmcache_get_or_create_config=lambda: config)
        monkeypatch.setitem(sys.modules, "lmcache.integration.vllm.utils", module)
    elif connector == "mooncake-store":
        monkeypatch.setenv("MOONCAKE_CONFIG_PATH", "/etc/mooncake/mooncake_config.json")
        read_text = Path.read_text

        def config_text(path, *args, **kwargs):
            if str(path) == "/etc/mooncake/mooncake_config.json":
                return json.dumps({"mode": "embedded" if tier == "cpu" else "standalone-store",
                                   "enable_offload": tier == "fs" and scenario != "wrong_tier"})
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", config_text)
    runtime = verify.runtime_config(container_for(engine, connector, tier, native_name),
                                    engine, connector)
    runtime.update(runtime_overrides or {})
    verify.verify_pod("example/model", "invocation", "1100", engine, connector,
                      tier, json.dumps(runtime))
    return server


@pytest.mark.parametrize(("engine", "connector", "native_name", "scenario", "underscore"), [
    ("vllm", "lmcache-connector", "OffloadingConnector", "pass", False),
    ("vllm", "lmcache-connector", "OffloadingConnector", "lazy_store_histogram", False),
    ("vllm", "native", "OffloadingConnector", "pass", False),
    ("vllm", "native", "OffloadingConnector", "legacy", False),
    ("vllm", "native", "TPUOffloadConnector", "pass", False),
    ("vllm", "native", "TPURaidenOffloadingConnector", "pass", False),
    ("vllm", "mooncake-store", "MooncakeStoreConnector", "pass", False),
    ("sglang", "native", "", "pass", False),
    ("sglang", "native", "", "pass", True),
    ("sglang", "native", "", "backup_only", False),
])
def test_connector_probes(monkeypatch, clock_and_signals, capsys, engine, connector,
                          native_name, scenario, underscore):
    server = run_probe(monkeypatch, engine, connector, native_name=native_name,
                       scenario=scenario, underscore=underscore)
    reports = [json.loads(line.partition("=")[2]) for line in capsys.readouterr().out.splitlines()
               if line.startswith("LLMD_TIERED_PREFIX_CACHE_EVIDENCE=")]
    assert len(reports) == 1
    assert reports[0]["validated"] is True
    assert reports[0]["retrieval_checked"] == (connector == "lmcache-connector")
    assert len(server.requests) == 2
    assert server.requests[0]["prompt"] == server.requests[1]["prompt"]
    if connector == "lmcache-connector":
        assert server.requests[0]["cache_salt"] != server.requests[1]["cache_salt"]
        assert reports[0]["warm_resident_prefix_hit_delta"] == 0
    else:
        assert all("cache_salt" not in request for request in server.requests)


@pytest.mark.parametrize("scenario", [
    "no_completed_store", "no_retrieval", "cold_hit", "resident_hit",
    "rollback", "missing_hits", "malformed", "wrong_model", "short_context",
    "mismatch", "application_error",
])
def test_lmcache_failures(monkeypatch, clock_and_signals, capsys, scenario):
    with pytest.raises(RuntimeError):
        run_probe(monkeypatch, scenario=scenario)
    assert "LLMD_TIERED_PREFIX_CACHE_EVIDENCE=" not in capsys.readouterr().out


@pytest.mark.parametrize(("engine", "connector", "native_name"), [
    ("vllm", "native", "OffloadingConnector"),
    ("vllm", "native", "TPURaidenOffloadingConnector"),
    ("vllm", "mooncake-store", "MooncakeStoreConnector"),
    ("sglang", "native", ""),
])
def test_existing_positive_metrics_do_not_pass(monkeypatch, clock_and_signals,
                                             engine, connector, native_name):
    with pytest.raises(RuntimeError, match="Timed out"):
        run_probe(monkeypatch, engine, connector, native_name=native_name, scenario="stale")


def test_lmcache_filesystem_can_run_without_cpu_tier(monkeypatch, clock_and_signals):
    run_probe(monkeypatch, tier="fs", local_cpu=False)


def test_lmcache_filesystem_requires_disk(monkeypatch, clock_and_signals):
    with pytest.raises(RuntimeError, match="filesystem tier is disabled"):
        run_probe(monkeypatch, tier="fs", local_disk=None)


@pytest.mark.parametrize(("engine", "connector", "native_name"), [
    ("vllm", "lmcache-connector", "OffloadingConnector"),
    ("vllm", "native", "OffloadingConnector"),
    ("vllm", "native", "TPURaidenOffloadingConnector"),
    ("vllm", "mooncake-store", "MooncakeStoreConnector"),
    ("sglang", "native", ""),
])
def test_filesystem_probes(monkeypatch, clock_and_signals, engine, connector, native_name):
    run_probe(monkeypatch, engine, connector, tier="fs", native_name=native_name)


@pytest.mark.parametrize("settings", [
    {"local_cpu": False},
    {"connector": "native", "tier": "fs", "runtime_overrides": {"storage_paths": []}},
    {"engine": "sglang", "connector": "native", "tier": "fs",
     "runtime_overrides": {"storage_backend": None}},
    {"connector": "mooncake-store", "tier": "fs", "native_name": "MooncakeStoreConnector",
     "scenario": "wrong_tier"},
])
def test_disabled_tiers_fail(monkeypatch, clock_and_signals, settings):
    with pytest.raises(RuntimeError):
        run_probe(monkeypatch, **settings)


def test_mismatched_engine_connector_fails_before_probe():
    with pytest.raises(RuntimeError, match="SGLang does not support"):
        verify.runtime_config(container_for("sglang"), "sglang", "lmcache-connector")
    with pytest.raises(RuntimeError, match="does not match native"):
        verify.runtime_config(container_for(connector="lmcache-connector"), "vllm", "native")


def test_shell_wrapped_deployed_options():
    container = {
        "command": ["/bin/bash", "-c"],
        "args": ['exec vllm serve example/model --block-size=4352 \\\n'
                 '--kv-transfer-config \\\n\'{"kv_connector":"TPURaidenOffloadingConnector",'
                 '"kv_role":"kv_both"}\''],
    }
    runtime = verify.runtime_config(container, "vllm", "native")
    assert runtime["chunk_size"] == 4352


@pytest.mark.parametrize(("path", "accelerator", "engine", "connector", "tier"), OVERLAYS,
                         ids=[str(entry[0].relative_to(GUIDE_DIR)) for entry in OVERLAYS])
def test_deployed_options_from_real_overlays(path, accelerator, engine, connector, tier):
    if not shutil.which("kubectl"):
        pytest.skip("kubectl is required for local Kustomize rendering")
    result = subprocess.run(["kubectl", "kustomize", str(path)],
                            capture_output=True, text=True, timeout=20, check=True)
    containers = []
    for resource in yaml.safe_load_all(result.stdout):
        if not resource:
            continue
        config = resource.get("spec", {})
        if resource.get("kind") == "LeaderWorkerSet":
            pod_spec = config["leaderWorkerTemplate"]["leaderTemplate"]["spec"]
        else:
            pod_spec = config.get("template", {}).get("spec", {})
        containers.extend(c for c in pod_spec.get("containers", [])
                          if c["name"] in ("modelserver", "modelserver-leader"))
    assert containers
    for container in containers:
        runtime = verify.runtime_config(container, engine, connector)
        assert runtime["chunk_size"] > 0
        if engine == "sglang" and tier == "fs":
            assert runtime["storage_backend"] == "file"
        if engine == "vllm" and connector == "native" and tier == "fs":
            assert runtime["storage_paths"]


def healthy_pod(name, container):
    return {
        "metadata": {"name": name, "uid": name + "-uid", "labels": {
            "llm-d.ai/guide": "tiered-prefix-cache", "llm-d.ai/role": "decode",
        }},
        "spec": {"containers": [container]},
        "status": {
            "phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [{"name": container["name"], "ready": True,
                                   "containerID": name + "-container", "restartCount": 0,
                                   "state": {"running": {"startedAt": "2026-11-10T00:00:00Z"}}}],
        },
    }


@pytest.mark.parametrize("scenario", [
    "pass", "multi_host", "restart", "missing_evidence", "not_ready", "no_pods",
    "bound_pvc", "pending_pvc",
])
def test_all_pods_and_filesystem_guards(monkeypatch, clock_and_signals, scenario):
    tier = "fs" if scenario in ("bound_pvc", "pending_pvc") else "cpu"
    containers = [container_for(connector="native", tier=tier) for _ in range(2)]
    if scenario == "multi_host":
        containers[0]["name"] = "modelserver-leader"
    pods = [healthy_pod(f"model-{i}", container) for i, container in enumerate(containers)]
    if scenario == "multi_host":
        pods[0]["metadata"]["labels"]["leaderworkerset.sigs.k8s.io/worker-index"] = "0"
        worker = healthy_pod("worker", {"name": "modelserver-worker"})
        worker["metadata"]["labels"]["leaderworkerset.sigs.k8s.io/worker-index"] = "1"
        pods.append(worker)
    for pod in pods:
        if tier == "fs":
            pod["spec"]["containers"][0]["volumeMounts"] = [
                {"name": "files", "mountPath": "/mnt/files-storage"}]
            pod["spec"]["volumes"] = [
                {"name": "files", "persistentVolumeClaim": {"claimName": "offload"}}]
    snapshots = 0
    executions = []
    pvc_reads = []

    def kubectl(arguments, **kwargs):
        nonlocal snapshots
        if "exec" in arguments:
            executions.append(arguments)
            index = arguments.index("--pod-probe")
            report = {"validated": True, "invocation_id": arguments[index + 2]}
            output = "" if scenario == "missing_evidence" else (
                "LLMD_TIERED_PREFIX_CACHE_EVIDENCE=" + json.dumps(report))
        elif "pvc" in arguments:
            pvc_reads.append(arguments)
            output = "Pending" if scenario == "pending_pvc" else "Bound"
        else:
            snapshots += 1
            if scenario == "restart" and snapshots >= 3:
                pods[0]["status"]["containerStatuses"][0]["restartCount"] = 1
            if scenario == "not_ready":
                pods[0]["status"]["containerStatuses"][0]["ready"] = False
            output = json.dumps({"items": [] if scenario == "no_pods" else pods})
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(verify.subprocess, "run", kubectl)
    if scenario in ("pass", "multi_host", "bound_pvc"):
        verify.verify_pods("test-ns", "example/model", "vllm", "native", tier)
        assert len(executions) == 2
        if scenario == "multi_host":
            assert executions[0][executions[0].index("-c") + 1] == "modelserver-leader"
        if tier == "fs":
            assert len(pvc_reads) == 1
    else:
        with pytest.raises(RuntimeError):
            verify.verify_pods("test-ns", "example/model", "vllm", "native", tier)


@pytest.fixture
def wrapper_environment(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "probe.json"
    shim = bin_dir / "python3"
    shim.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
        import json
        import os
        import sys
        from pathlib import Path
        if sys.argv[1].endswith("/scripts/guide.py"):
            if os.environ.get("EMPTY_GATE"):
                print("set -euo pipefail")
                sys.exit(0)
            os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
        elif sys.argv[1].endswith("/guides/tiered-prefix-cache/verify.py"):
            Path(os.environ["PROBE_CAPTURE"]).write_text(json.dumps({
                "arguments": sys.argv[2:],
                "selections": [os.environ[name] for name in
                               ("ACCELERATOR_TYPE", "MODEL_SERVER", "CONNECTOR", "VARIANT")],
            }))
            sys.exit(int(os.environ.get("PROBE_STATUS", "0")))
        raise AssertionError(sys.argv)
        """))
    shim.chmod(0o755)
    kubectl = bin_dir / "kubectl"
    kubectl.write_text("#!/bin/sh\nexit 0\n")
    kubectl.chmod(0o755)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("LLMD_", "LLMDBENCH_"))
           and key not in ("ACCELERATOR_TYPE", "MODEL_SERVER", "CONNECTOR", "VARIANT")}
    env.update(PATH=f"{bin_dir}:{env['PATH']}", PROBE_CAPTURE=str(capture), TMPDIR=str(tmp_path))
    return env, capture, tmp_path


@pytest.mark.parametrize(("accelerator", "engine", "connector", "tier"), SELECTIONS)
def test_wrapper_uses_nightly_selections(wrapper_environment, accelerator, engine, connector, tier):
    env, capture, tmp_path = wrapper_environment
    env.update(LLMDBENCH_CICD_ACCELERATOR=accelerator, LLMDBENCH_CICD_BACKEND=engine,
               LLMDBENCH_CICD_CONNECTOR=connector, LLMDBENCH_CICD_OFFLOADING_TARGET=tier)
    result = subprocess.run(["bash", str(WRAPPER), "-n", "test-ns", "-m", "example/model"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    recorded = json.loads(capture.read_text())
    assert recorded["selections"] == [accelerator, engine, connector, tier]
    assert recorded["arguments"] == ["-n", "test-ns", "-m", "example/model",
                                     "--model-server", engine, "--connector", connector,
                                     "--variant", tier]
    assert not list(tmp_path.glob("tiered-prefix-cache-verify.*"))


def test_wrapper_explicit_options_override_nightly(wrapper_environment):
    env, capture, _ = wrapper_environment
    env.update(LLMDBENCH_CICD_ACCELERATOR="xpu", LLMDBENCH_CICD_CONNECTOR="lmcache-connector")
    result = subprocess.run(["bash", str(WRAPPER), "-n", "test-ns", "-m", "example/model",
                             "--accelerator-type", "gpu", "--model-server", "sglang",
                             "--connector", "native", "--variant", "fs"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text())["selections"] == ["gpu", "sglang", "native", "fs"]


@pytest.mark.parametrize(("selection", "expected"), [
    ("rocm", "amd"), ("tpu-v5p", "tpu/v5p"), ("tpu-v6", "tpu/v6"), ("tpu-v7", "tpu/v7"),
])
def test_wrapper_accepts_nightly_accelerator_aliases(wrapper_environment, selection, expected):
    env, capture, _ = wrapper_environment
    env["LLMDBENCH_CICD_ACCELERATOR"] = selection
    result = subprocess.run(["bash", str(WRAPPER), "-n", "test-ns", "-m", "example/model"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text())["selections"][0] == expected


@pytest.mark.parametrize(("settings", "expected_status"), [
    ({"EMPTY_GATE": "1", "LLMD_TIERED_PREFIX_CACHE_VALIDATED": "1"}, 1),
    ({"PROBE_STATUS": "7"}, 7),
])
def test_wrapper_fails_for_missing_or_failed_gate(wrapper_environment, settings, expected_status):
    env, _, tmp_path = wrapper_environment
    env.update(settings)
    result = subprocess.run(["bash", str(WRAPPER), "-n", "test-ns", "-m", "example/model"],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == expected_status, result.stderr
    assert not list(tmp_path.glob("tiered-prefix-cache-verify.*"))


def test_standup_skips_controlled_gate_and_only_xpu_nightlies_opt_in():
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/guide.py"),
                             "emit", str(GUIDE_DIR), "--context", "ci",
                             "verify.tests.offload_gate"],
                            capture_output=True, text=True, timeout=15, check=True)
    assert "/verify.py" not in result.stdout
    readme = (GUIDE_DIR / "README.md").read_text()
    block = readme.split("<!-- guide:verify.tests.offload_gate start -->")[1].split(
        "<!-- guide:verify.tests.offload_gate end -->")[0]
    assert "<!-- llm-d-cicd:skip start -->" in block
    assert "<!-- llm-d-cicd:skip end -->" in block
    callers = []
    for path in (REPO_ROOT / ".github/workflows").glob("nightly-e2e-tiered-prefix-cache-*.yaml"):
        workflow = yaml.safe_load(path.read_text())
        if workflow["jobs"]["nightly"]["with"].get("e2e_validate_args"):
            callers.append(path.name)
    assert sorted(callers) == [
        "nightly-e2e-tiered-prefix-cache-intel-acc-xpu-vllm-lmcache.yaml",
        "nightly-e2e-tiered-prefix-cache-intel-acc-xpu-vllm-native.yaml",
    ]
