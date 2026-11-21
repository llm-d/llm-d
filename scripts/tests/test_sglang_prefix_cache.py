"""The SGLang probe must reject successful inference without proven affinity."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sglang_probe", ROOT / "guides/precise-prefix-cache-routing/verify-sglang.py")
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def backend(requests=0, cached=0):
    return f'sglang:num_requests_total{{model_name="model"}} {requests}\n' + (
        f'sglang:cached_tokens_total{{cache_source="device"}} {cached}\n')


def snapshot(requests=(0, 0), cached=0, predicted=0, renders=0, failures=0):
    return probe.Snapshot(
        f'llm_d_epp_prefix_predicted_cached_tokens_sum {predicted}\n'
        f'llm_d_epp_token_producer_render_duration_seconds_count{{result="success"}} {renders}\n'
        f'llm_d_epp_token_producer_render_failures_total {failures}\n',
        {"a": backend(requests[0], cached), "b": backend(requests[1])})


def test_followup_requires_router_prediction_and_backend_reuse():
    result = probe.check_followup(snapshot((1, 0)), snapshot((2, 0), 128, 128, 1), "a")
    assert result == {"warm_backend": "a", "predicted_cached_tokens": 128, "reused_tokens": 128}


@pytest.mark.parametrize("after", [
    snapshot((1, 1), 128, 128, 1),  # hit on the wrong backend
    snapshot((2, 1), 128, 128, 1),  # unrelated traffic invalidates attribution
    snapshot((2, 0), 128, 0, 1),    # backend reuse without an EPP index hit
    snapshot((2, 0), 0, 128, 1),    # EPP prediction without delivered reuse
    snapshot((2, 0), 128, 128, 0),  # missing tokenization
    snapshot((2, 0), 128, 128, 1, 1),  # failed render alongside a success
    snapshot((0, 0), 128, 128, 1),  # restarted backend
])
def test_false_passes_are_rejected(after):
    with pytest.raises(AssertionError):
        probe.check_followup(snapshot((1, 0)), after, "a")


def test_cold_request_identifies_one_owner():
    assert probe.warm_backend(snapshot(), snapshot((0, 1))) == "b"
    with pytest.raises(AssertionError):
        probe.warm_backend(snapshot(), snapshot((1, 1)))
    with pytest.raises(AssertionError):
        probe.warm_backend(snapshot(), snapshot())


def test_probe_waits_for_router_backend_discovery():
    with pytest.raises(AssertionError):
        probe.require_ready_backends(snapshot())
    ready = snapshot()
    ready.router += 'llm_d_epp_ready_endpoints{name="pool"} 2\n'
    probe.require_ready_backends(ready)


def test_device_cache_is_not_double_counted_with_total_or_host():
    metrics = ('sglang:cached_tokens_total{cache_source="total"} 100\n'
               'sglang:cached_tokens_total{cache_source="host"} 60\n'
               'sglang:cached_tokens_total{cache_source="device"} 40\n')
    assert probe.cached_tokens(metrics) == 40
    assert probe.cached_tokens('sglang:cached_tokens_total 50\n') == 50
    assert probe.total('other_metric 10\nsglang:num_requests_total_extra 5\n', 'sglang:num_requests_total') == 0


@pytest.mark.parametrize("value", ["NaN", "+Inf", "-1"])
def test_invalid_counters_fail(value):
    with pytest.raises(ValueError):
        probe.samples(f"counter {value}", "counter")


@pytest.mark.parametrize("api", probe.APIS)
def test_each_api_requires_valid_output_and_usage(api):
    with pytest.raises(AssertionError):
        probe.validate_response(api, {"error": "unavailable"})
    with pytest.raises(AssertionError):
        probe.validate_response(api, {})
    body = {"text": "answer", "meta_info": {"prompt_tokens": 128, "completion_tokens": 8}}
    if api != "/generate":
        body = {"choices": [{"text": "answer", "message": {"reasoning_content": "thinking"}}],
                "usage": {"prompt_tokens": 128, "completion_tokens": 8}}
    probe.validate_response(api, body)
    request = probe.payload(api, "model", "shared prefix")
    assert request["model"] == "model"


def test_native_request_uses_sglang_sampling_parameters():
    request = probe.payload("/generate", "model", "prefix")
    assert request["text"] == "prefix"
    assert request["sampling_params"]["max_new_tokens"] == 8
    assert "max_tokens" not in request


def test_metrics_token_is_only_sent_to_router_metrics(monkeypatch):
    calls = []
    def fake_read(url, data=None, headers=None):
        calls.append((url, headers))
        return ""
    monkeypatch.setattr(probe, "read", fake_read)
    cluster = probe.Cluster("http://requests", "http://metrics", {"pod": "http://backend"}, "test-token")
    cluster.snapshot()
    assert calls == [("http://metrics/metrics", {"Authorization": "Bearer test-token"}),
                     ("http://backend/metrics", None)]


def test_probe_sequence_waits_for_events_then_compares_a_fresh_baseline(monkeypatch):
    events, prompts = [], []
    class FakeCluster:
        sent = 0
        def snapshot(self):
            events.append("snapshot")
            pairs = self.sent // 2
            return snapshot((self.sent, 0), pairs * 128, pairs * 128, self.sent)
        def send(self, api, model, prompt):
            events.append("send")
            self.sent += 1
            prompts.append((api, prompt))
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    probe.verify(FakeCluster(), "model")
    assert events == ["snapshot", "send", "snapshot", ("sleep", 3), "snapshot", "send", "snapshot"] * 3
    for index, api in enumerate(probe.APIS):
        first, second = prompts[index * 2:index * 2 + 2]
        assert first[0] == second[0] == api
        assert first[1].split("Question")[0] == second[1].split("Question")[0]
        assert first[1] != second[1]
    assert len({prompt.split("Question")[0] for _, prompt in prompts}) == len(probe.APIS)


def test_deploy_helper_includes_sglang_values_render_pool_and_strict_probe(tmp_path):
    bash = shutil.which("bash")
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    if not bash:
        pytest.skip("Bash is required for the deployment contract test")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    for tool in ("kubectl", "helm"):
        script = fake_bin / tool
        script.write_text(f'#!/usr/bin/env bash\necho "{tool} $*" >> "${{PROBE_TEST_LOG}}"\n', encoding="utf-8")
        script.chmod(0o755)
    python = fake_bin / "python3"
    python.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$1" == *"/verify-sglang.py" ]]; then\n'
        '  echo "probe $*" >> "${PROBE_TEST_LOG}"\n'
        'else\n'
        f'  exec "{Path(sys.executable).as_posix()}" "$@"\n'
        'fi\n', encoding="utf-8")
    python.chmod(0o755)
    env = {**os.environ, "NAMESPACE": "test-prefix", "OUTPUT_DIR": (tmp_path / "output").as_posix(),
           "PROBE_TEST_LOG": log.as_posix(), "PYTHONUTF8": "1"}
    env["PATH"] = fake_bin.as_posix() + os.pathsep + env["PATH"]
    result = subprocess.run([bash, str(ROOT / "guides/precise-prefix-cache-routing/scripts/nightly-deploy-sglang-gke.sh")],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="utf-8")
    assert "precise-prefix-cache-routing.sglang.values.yaml" in commands
    assert "/modelserver/gpu/sglang/gke/" in commands
    assert "/render/standalone/" in commands
    assert "deployment/precise-prefix-cache-routing-gpu-sglang-decode" in commands
    assert "deployment/precise-prefix-cache-routing-render" in commands
    assert "probe " in commands and "--namespace test-prefix" in commands
    assert "create secret" not in commands
