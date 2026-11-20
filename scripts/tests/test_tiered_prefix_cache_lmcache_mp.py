"""Render and launcher regression for the NVIDIA LMCache overlays of the tiered-prefix-cache guide.

gpu/vllm/lmcache-connector/cpu runs LMCache in multiprocess (MP) mode: vLLM
connects through LMCacheMPConnector to one `lmcache server` that
launch-with-lmcache-mp.sh starts next to it in the model server container.

The render tests pin what the three overlays the nightlies deploy (base, gke,
ocp) must agree on: the connector, the loopback endpoint, the CPU tier size and
the figures derived from it (pod memory, the router capacity in the README).
gpu/vllm/lmcache-connector/fs must not pick any of it up: it still runs the
in-process connector.

The launcher tests run the script against fake `lmcache` and `vllm` binaries
that record their argv and react to signals, so they cover what a render cannot:
which process is told to stop when, and with which status the container exits.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "guides/tiered-prefix-cache"
LMCACHE = GUIDE / "modelserver/gpu/vllm/lmcache-connector"
LAUNCHER = LMCACHE / "cpu/base/launch-with-lmcache-mp.sh"
LAUNCHER_IN_POD = "/etc/lmcache-mp/launch-with-lmcache-mp.sh"
CPU_OVERLAYS = ["base", "gke", "ocp"]

# Qwen3-32B: 64 layers x 8 KV heads x 128 dims x 2 (K, V) x 2 bytes (bf16).
KV_BYTES_PER_TOKEN = 64 * 8 * 128 * 2 * 2
ROUTER_BLOCK_TOKENS = 64
GIB = 1 << 30


def _render(path: Path) -> list[dict]:
    if kubectl := shutil.which("kubectl"):
        cmd = [kubectl, "kustomize", str(path)]
    elif kustomize := shutil.which("kustomize"):
        cmd = [kustomize, "build", str(path)]
    else:
        pytest.skip("kubectl or kustomize is required for the render regression")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _pod(docs: list[dict]) -> dict:
    return next(doc for doc in docs if doc["kind"] == "Deployment")["spec"]["template"]["spec"]


def _modelserver(docs: list[dict]) -> dict:
    return next(c for c in _pod(docs)["containers"] if c["name"] == "modelserver")


def _env(container: dict) -> dict[str, str]:
    return {e["name"]: e["value"] for e in container["env"] if "value" in e}


def _option(args: list[str], name: str) -> str | None:
    for index, arg in enumerate(args):
        if arg.startswith(name + "="):
            return arg.partition("=")[2]
        if arg == name:
            return args[index + 1]
    return None


def _gib(quantity: str) -> int:
    assert quantity.endswith("Gi"), quantity
    return int(quantity[:-2])


def _server_args(container: dict) -> list[str]:
    """The `lmcache server` flags, split the way the launcher splits them."""
    value = _env(container)["LMCACHE_MP_SERVER_ARGS"]
    # The launcher splits on whitespace and knows no quoting.
    assert not set("'\"\\") & set(value)
    return value.split()


# --- render: cpu overlays (LMCache MP) ---------------------------------------


@pytest.mark.parametrize("overlay", CPU_OVERLAYS)
def test_cpu_overlay_connects_vllm_to_a_local_mp_server(overlay):
    container = _modelserver(_render(LMCACHE / "cpu" / overlay))
    env = _env(container)

    transfer = json.loads(_option(container["args"], "--kv-transfer-config"))
    assert transfer["kv_connector"] == "LMCacheMPConnector"
    assert transfer["kv_role"] == "kv_both"
    # A block that cannot be loaded from the CPU tier must not fail the request.
    assert transfer["kv_load_failure_policy"] == "recompute"
    extra = transfer["kv_connector_extra_config"]
    assert extra["lmcache.mp.host"] == "tcp://127.0.0.1"
    # The connector and the launcher must agree on the request port.
    assert extra["lmcache.mp.port"] == int(env.get("LMCACHE_MP_PORT", "5555"))
    # Auto-start would start a second, unsupervised server.
    assert not any(key.startswith("lmcache.mp.autostart") for key in extra)

    # Nothing of the in-process connector is left behind.
    assert "LMCACHE_MAX_LOCAL_CPU_SIZE" not in env
    assert "PROMETHEUS_MULTIPROC_DIR" not in env
    assert "LMCacheConnectorV1" not in json.dumps(container)

    # The args keep the Hub ID first: the nightly harness reads the model from it.
    assert container["args"][0] == "Qwen/Qwen3-32B"
    assert container["resources"]["limits"]["nvidia.com/gpu"] == 2
    assert _option(container["args"], "--tensor-parallel-size") == "2"


@pytest.mark.parametrize("overlay", CPU_OVERLAYS)
def test_cpu_overlay_runs_the_launcher_from_the_script_in_the_repo(overlay):
    docs = _render(LMCACHE / "cpu" / overlay)
    container = _modelserver(docs)
    assert container["command"][:2] == ["/bin/bash", LAUNCHER_IN_POD]

    mount = next(m for m in container["volumeMounts"] if LAUNCHER_IN_POD.startswith(m["mountPath"] + "/"))
    assert mount["readOnly"] is True
    volume = next(v for v in _pod(docs)["volumes"] if v["name"] == mount["name"])
    config_map = next(
        doc for doc in docs
        if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == volume["configMap"]["name"]
    )
    key = LAUNCHER_IN_POD[len(mount["mountPath"]) + 1:]
    assert config_map["data"][key] == LAUNCHER.read_text()


@pytest.mark.parametrize("overlay", CPU_OVERLAYS)
def test_cpu_overlay_keeps_the_probes_and_exposes_only_the_model_server_port(overlay):
    container = _modelserver(_render(LMCACHE / "cpu" / overlay))
    # The MP server's ports are loopback only: nothing to declare or probe.
    assert [(p["name"], p["containerPort"]) for p in container["ports"]] == [("modelserver", 8000)]
    assert container["startupProbe"]["httpGet"] == {"path": "/v1/models", "port": "modelserver"}
    assert container["readinessProbe"]["httpGet"] == {"path": "/v1/models", "port": "modelserver"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/health", "port": "modelserver"}
    # The launcher forwards SIGTERM to vLLM, which drains for --shutdown-timeout.
    assert container["lifecycle"]["preStop"]["exec"]["command"] == ["/bin/sleep", "15"]


@pytest.mark.parametrize("overlay", CPU_OVERLAYS)
def test_cpu_tier_capacity_memory_and_router_figure_agree(overlay):
    container = _modelserver(_render(LMCACHE / "cpu" / overlay))
    server_args = _server_args(container)
    # The launcher owns the endpoints; the manifest must not try to set them.
    assert not {"--host", "--port", "--http-host", "--http-port"} & {a.split("=")[0] for a in server_args}
    assert _option(server_args, "--eviction-policy") == "LRU"
    l1_gib = float(_option(server_args, "--l1-size-gb"))
    watermark = float(_option(server_args, "--eviction-trigger-watermark"))
    eviction_ratio = float(_option(server_args, "--eviction-ratio"))

    # One transfer thread per tensor-parallel rank.
    assert _option(server_args, "--max-workers") == _option(container["args"], "--tensor-parallel-size")

    # The tier holds, before it evicts, what the in-process connector held:
    # LMCACHE_MAX_LOCAL_CPU_SIZE=100.0 (GiB per rank) on 2 ranks.
    usable_gib = round(l1_gib * watermark, 6)
    assert usable_gib == 100.0 * 2

    # The server pins all of --l1-size-gb, so the pod needs it in full, plus the
    # 100Gi the native overlay leaves above its own CPU tier for the same engine.
    resources = container["resources"]
    assert resources["requests"]["memory"] == resources["limits"]["memory"]
    assert _gib(resources["limits"]["memory"]) >= l1_gib + 100

    # README: the router capacity row for this overlay follows from the same numbers.
    blocks = usable_gib * GIB / (KV_BYTES_PER_TOKEN * ROUTER_BLOCK_TOKENS)
    assert (blocks, round(blocks * (1 - eviction_ratio))) == (12800, 10240)
    readme = (GUIDE / "README.md").read_text()
    row = next(line for line in readme.splitlines() if "`lmcache-connector` (multiprocess mode)" in line)
    assert f"{l1_gib:g} GiB" in row and f"{usable_gib:g} GiB" in row
    assert row.rstrip(" |").endswith(f"{blocks:g}")


@pytest.mark.parametrize("overlay", CPU_OVERLAYS)
def test_cpu_overlay_keeps_the_registration_of_an_idle_engine(overlay):
    # An engine worker starts pinging the MP server with its first store or
    # load. With the server's defaults a replica left idle for an hour loses its
    # registration and, vLLM still being ready, stores nothing from then on.
    server_args = _server_args(_modelserver(_render(LMCACHE / "cpu" / overlay)))
    assert float(_option(server_args, "--worker-reap-timeout-seconds")) == 0


def test_gke_overlay_only_adds_the_nccl_tuner_setting():
    base = _render(LMCACHE / "cpu/base")
    gke = _render(LMCACHE / "cpu/gke")
    env = _modelserver(gke)["env"]
    assert {"name": "NCCL_TUNER_PLUGIN", "value": "none"} in env
    env.remove({"name": "NCCL_TUNER_PLUGIN", "value": "none"})
    assert gke == base


# --- render: fs overlays stay in-process --------------------------------------


@pytest.mark.parametrize("overlay", ["base", "gke"])
def test_fs_overlay_still_runs_the_in_process_connector(overlay):
    docs = _render(LMCACHE / "fs" / overlay)
    container = _modelserver(docs)
    env = _env(container)

    transfer = json.loads(_option(container["args"], "--kv-transfer-config"))
    assert transfer == {"kv_connector": "LMCacheConnectorV1", "kv_role": "kv_both"}
    assert container["command"] == ["vllm"]
    assert env["LMCACHE_MAX_LOCAL_CPU_SIZE"] == "100.0"
    assert env["LMCACHE_LOCAL_DISK"] == "file:///mnt/files-storage/kv-cache/"
    assert container["resources"]["limits"]["memory"] == "500Gi"

    assert "LMCACHE_MP_SERVER_ARGS" not in env
    assert not [doc for doc in docs if doc["kind"] == "ConfigMap"]


# --- launcher ------------------------------------------------------------------

# A stand-in for `lmcache` and `vllm`. It records its argv and PID, then follows
# STUB_<NAME>_MODE:
#   run            (default) run until SIGTERM, then exit 0
#   exit:<s>:<rc>  exit by itself with status <rc> after <s> seconds
#   drain:<s>      on SIGTERM keep running for <s> seconds, then exit 0
#   ignore         ignore SIGTERM, and keep a child process of its own running
# It ends by itself after a minute, so that a failing test leaves nothing behind.
STUB = r"""#!/usr/bin/env bash
name=$(basename "$0")
printf '%s\n' "$@" > "${STUB_DIR}/${name}.argv"
mode_var="STUB_${name^^}_MODE"
mode=${!mode_var:-run}
IFS=: read -r kind first second <<< "${mode}"
case "${kind}" in
  drain)  trap 'date +%s.%N > "${STUB_DIR}/${name}.term"; sleep "${first}"; date +%s.%N > "${STUB_DIR}/${name}.exit"; exit 0' TERM ;;
  ignore) trap 'date +%s.%N > "${STUB_DIR}/${name}.term"' TERM; sleep 60 & ;;
  run)    trap 'date +%s.%N > "${STUB_DIR}/${name}.term"; exit 0' TERM ;;
esac
echo $$ > "${STUB_DIR}/${name}.pid.tmp" && mv "${STUB_DIR}/${name}.pid.tmp" "${STUB_DIR}/${name}.pid"
if [ "${kind}" = exit ]; then
  sleep "${first}"
  exit "${second}"
fi
for ((tick = 0; tick < 1200; tick++)); do sleep 0.05; done
"""


class Launcher:
    def __init__(self, tmp_path: Path) -> None:
        self.dir = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name in ("lmcache", "vllm"):
            stub = self.bin / name
            stub.write_text(STUB)
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.proc: subprocess.Popen | None = None

    def start(self, *command: str, **env: str) -> "Launcher":
        full_env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "STUB_DIR": str(self.dir),
            "LMCACHE_MP_SERVER_ARGS": "--l1-size-gb 250 --eviction-policy LRU",
            "LMCACHE_MP_STOP_TIMEOUT": "3",
            **env,
        }
        self.proc = subprocess.Popen(
            ["bash", str(LAUNCHER), *command], env=full_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True,
        )
        return self

    def wait_for(self, *names: str, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while not all((self.dir / name).exists() for name in names):
            assert time.monotonic() < deadline, f"timed out waiting for {names}"
            time.sleep(0.02)

    def lines(self, name: str) -> list[str]:
        return (self.dir / name).read_text().splitlines()

    def stamp(self, name: str) -> float:
        return float((self.dir / name).read_text())

    def finish(self, timeout: float = 10) -> tuple[int, str]:
        """Exit status and output of the launcher, once nothing it started is left.

        The launcher and everything it starts write to one pipe, which reaches
        its end only when all of them are gone. Waiting for that does not
        depend on a stand-in having got as far as writing its PID file.
        """
        assert self.proc is not None
        try:
            output, _ = self.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the launcher or a process it started is still running after {timeout} s")
        return self.proc.returncode, output

    def cleanup(self) -> None:
        if self.proc is None:
            return
        # The launcher is a session leader and runs each child in a process
        # group of its own, led by the PID the stand-in recorded.
        for path in self.dir.glob("*.pid"):
            pid = int(path.read_text())
            try:
                if os.getsid(pid) == self.proc.pid:
                    os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.proc.kill()
        self.proc.wait(5)


@pytest.fixture
def launcher(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("bash is required to run the launcher")
    instance = Launcher(tmp_path)
    yield instance
    instance.cleanup()


def _hold_before(launcher: Launcher, command: str) -> dict[str, str]:
    """Environment that holds the launcher once, just before a command matching the regex.

    bash runs a DEBUG trap, set here through BASH_ENV, before every command.
    The launcher creates `paused` when it is held and goes on once `go` exists.
    """
    hook = launcher.dir / "hold.sh"
    hook.write_text(
        "unset BASH_ENV\n"
        f"hold_before='^({command})'\n"
        "trap 'if [[ ${BASH_COMMAND} =~ ${hold_before} && ! -e \"${STUB_DIR}/paused\" ]]; then "
        ": > \"${STUB_DIR}/paused\"; while [ ! -e \"${STUB_DIR}/go\" ]; do sleep 0.05; done; fi' DEBUG\n"
    )
    return {"BASH_ENV": str(hook)}


def test_launcher_starts_the_server_on_loopback_and_the_given_command(launcher):
    launcher.start(
        "vllm", "serve", "Qwen/Qwen3-32B", "--tensor-parallel-size=2",
        # An attempt to expose the endpoints: the launcher's own flags come last,
        # and argparse keeps the last value.
        LMCACHE_MP_SERVER_ARGS="--l1-size-gb 250 --eviction-policy LRU --http-host 0.0.0.0 --host 0.0.0.0",
    )
    launcher.wait_for("lmcache.pid", "vllm.pid")
    assert launcher.lines("lmcache.argv") == [
        "server", "--l1-size-gb", "250", "--eviction-policy", "LRU",
        "--http-host", "0.0.0.0", "--host", "0.0.0.0",
        "--host", "127.0.0.1", "--port", "5555",
        "--http-host", "127.0.0.1", "--http-port", "8080",
    ]
    assert launcher.lines("vllm.argv") == ["serve", "Qwen/Qwen3-32B", "--tensor-parallel-size=2"]


def test_server_flags_may_span_several_lines(launcher):
    # A YAML block scalar, for example.
    launcher.start("vllm", "serve", LMCACHE_MP_SERVER_ARGS="--l1-size-gb 250\n  --eviction-policy LRU\n\t--max-workers 2\n")
    launcher.wait_for("lmcache.pid", "vllm.pid")
    assert launcher.lines("lmcache.argv")[:7] == [
        "server", "--l1-size-gb", "250", "--eviction-policy", "LRU", "--max-workers", "2",
    ]


def test_launcher_ports_follow_the_environment(launcher):
    launcher.start("vllm", "serve", LMCACHE_MP_PORT="6555", LMCACHE_MP_HTTP_PORT="8181")
    launcher.wait_for("lmcache.pid", "vllm.pid")
    argv = launcher.lines("lmcache.argv")
    assert argv[-8:] == ["--host", "127.0.0.1", "--port", "6555", "--http-host", "127.0.0.1", "--http-port", "8181"]


def test_ocp_command_serves_the_model_from_the_node_cache(launcher):
    container = _modelserver(_render(LMCACHE / "cpu/ocp"))
    command = container["command"]
    assert command[:2] == ["/bin/bash", LAUNCHER_IN_POD]
    # Run what the pod would run after the launcher path: the ocp wrapper with the base's args.
    launcher.start(*command[2:], *container["args"])
    launcher.wait_for("lmcache.pid", "vllm.pid")
    assert launcher.lines("vllm.argv") == [
        "serve", "/model-cache/models/Qwen/Qwen3-32B", "--served-model-name", "Qwen/Qwen3-32B",
        *container["args"][1:],
    ]


# Pod deletion: the kubelet sends SIGTERM to the launcher.


def test_sigterm_drains_the_model_server_before_stopping_the_cache_server(launcher):
    launcher.start("vllm", "serve", STUB_VLLM_MODE="drain:1")
    launcher.wait_for("lmcache.pid", "vllm.pid")
    launcher.proc.send_signal(signal.SIGTERM)
    status, output = launcher.finish()
    assert status == 0
    # The cache server keeps running while the model server finishes its requests.
    assert launcher.stamp("lmcache.term") >= launcher.stamp("vllm.exit")
    assert launcher.stamp("vllm.exit") - launcher.stamp("vllm.term") >= 1
    assert "model server stopped (status 0)" in output


def test_a_second_sigterm_does_not_cut_the_drain_short(launcher):
    launcher.start("vllm", "serve", STUB_VLLM_MODE="drain:2")
    launcher.wait_for("lmcache.pid", "vllm.pid")
    launcher.proc.send_signal(signal.SIGTERM)
    launcher.wait_for("vllm.term")
    launcher.proc.send_signal(signal.SIGTERM)
    status, _ = launcher.finish()
    assert status == 0
    # The stand-in writes vllm.exit only if it is left to finish its drain.
    assert launcher.stamp("vllm.exit") - launcher.stamp("vllm.term") >= 2


def test_cache_server_dying_during_the_drain_does_not_cut_it_short(launcher):
    launcher.start("vllm", "serve", STUB_VLLM_MODE="drain:2")
    launcher.wait_for("lmcache.pid", "vllm.pid")
    launcher.proc.send_signal(signal.SIGTERM)
    launcher.wait_for("vllm.term")
    os.kill(int((launcher.dir / "lmcache.pid").read_text()), signal.SIGKILL)
    status, _ = launcher.finish()
    assert status == 0
    assert launcher.stamp("vllm.exit") - launcher.stamp("vllm.term") >= 2


def test_sigterm_before_the_model_server_exists_is_not_lost(launcher):
    launcher.start("vllm", "serve", **_hold_before(launcher, "lmcache server"))
    launcher.wait_for("paused")
    launcher.proc.send_signal(signal.SIGTERM)
    (launcher.dir / "go").touch()
    status, output = launcher.finish()
    # The model server is told to stop as soon as it exists. The stand-in has
    # usually not set its handler by then and dies of the signal (143).
    assert re.search(r"model server stopped \(status (0|143)\); stopping the LMCache MP server", output), output
    assert status in (0, 143)


def test_pod_deletion_kills_a_cache_server_that_does_not_stop(launcher):
    launcher.start("vllm", "serve", STUB_LMCACHE_MODE="ignore")
    launcher.wait_for("lmcache.pid", "vllm.pid")
    started = time.monotonic()
    launcher.proc.send_signal(signal.SIGTERM)
    status, _ = launcher.finish(timeout=30)
    assert status == 0
    # LMCACHE_MP_STOP_TIMEOUT=3 in these tests (30 by default).
    assert 3 <= time.monotonic() - started <= 10
    assert (launcher.dir / "lmcache.term").exists()


# One of the two processes exits while the pod is in service.


@pytest.mark.parametrize("status, expected", [(3, 3), (0, 1)])
def test_cache_server_exit_kills_the_model_server_and_fails_the_container(launcher, status, expected):
    # The model server would drain for 5 s on SIGTERM. It must not get the
    # chance: a draining vLLM still answers its readiness path.
    launcher.start("vllm", "serve", STUB_LMCACHE_MODE=f"exit:0.5:{status}", STUB_VLLM_MODE="drain:5")
    started = time.monotonic()
    assert launcher.finish() == (expected, f"launch-with-lmcache-mp: LMCache MP server exited (status {status}); killing the model server\n")
    assert time.monotonic() - started < 3
    assert not (launcher.dir / "vllm.term").exists()


def test_a_death_between_two_checks_is_not_missed(launcher):
    # The cache server is killed, and reaped by bash, while the launcher is
    # between its check and its wait. `wait -n` does not return for such a
    # child and would block on the model server instead.
    launcher.start("vllm", "serve", **_hold_before(launcher, "sleep 1|wait -n"))
    launcher.wait_for("paused", "lmcache.pid", "vllm.pid")
    cache_pid = int((launcher.dir / "lmcache.pid").read_text())
    os.kill(cache_pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(cache_pid, 0)  # still there until bash has reaped it
        except ProcessLookupError:
            break
        time.sleep(0.02)
    (launcher.dir / "go").touch()
    status, output = launcher.finish()
    assert status == 137
    assert "LMCache MP server exited (status 137); killing the model server" in output


def test_cache_server_that_fails_at_once_is_not_missed(launcher):
    # E.g. a flag the installed LMCache does not know: the server is gone before
    # the launcher starts waiting.
    launcher.start("vllm", "serve", STUB_LMCACHE_MODE="exit:0:2")
    status, output = launcher.finish()
    assert status == 2
    assert "LMCache MP server exited (status 2)" in output


@pytest.mark.parametrize("status, expected", [(7, 7), (0, 1)])
def test_model_server_exit_kills_the_cache_server_and_fails_the_container(launcher, status, expected):
    launcher.start("vllm", "serve", STUB_VLLM_MODE=f"exit:0.5:{status}", STUB_LMCACHE_MODE="drain:5")
    started = time.monotonic()
    assert launcher.finish() == (expected, f"launch-with-lmcache-mp: model server exited (status {status}); killing the LMCache MP server\n")
    assert time.monotonic() - started < 3
    assert not (launcher.dir / "lmcache.term").exists()


def test_the_whole_process_tree_of_the_survivor_is_killed(launcher):
    # In `ignore` mode the stand-in keeps a child of its own running, like
    # vLLM's engine and worker processes: finish() fails if that child is left.
    launcher.start("vllm", "serve", STUB_LMCACHE_MODE="exit:0.5:3", STUB_VLLM_MODE="ignore")
    launcher.wait_for("vllm.pid")
    status, _ = launcher.finish()
    assert status == 3


@pytest.mark.parametrize("command, env", [
    ((), {}),
    (("vllm", "serve"), {"LMCACHE_MP_SERVER_ARGS": ""}),
    (("vllm", "serve"), {"LMCACHE_MP_SERVER_ARGS": " \n"}),
])
def test_launcher_refuses_to_start_without_a_command_or_server_flags(launcher, command, env):
    launcher.start(*command, **env)
    status, _ = launcher.finish()
    assert status == 64
    assert not (launcher.dir / "lmcache.argv").exists()
    assert not (launcher.dir / "vllm.argv").exists()
