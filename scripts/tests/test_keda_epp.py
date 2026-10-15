"""Regression coverage for the KEDA EPP README copy-paste commands."""

import os
from pathlib import Path
import subprocess

import pytest
import yaml

GUIDE = Path(__file__).resolve().parents[2] / "guides/workload-autoscaling/keda-epp/guide.yaml"
DATA = yaml.safe_load(GUIDE.read_text())
STEPS = [key for key in DATA["deploy"] if key.startswith("apply_")]
REQUIRED = ["NAMESPACE", "MODEL", "TARGET_DEPLOYMENT", "EPP_SERVICE", "INFERENCE_POOL", "SCALEDOBJECT_NAME", "HPA_NAME"]


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("missing", REQUIRED + [None])
def test_interactive_apply_guard(tmp_path, step, missing):
    # Stub both pipeline commands: this test must never contact a cluster.
    calls = tmp_path / "calls"
    env = {**os.environ, **{key: "test-value" for key in REQUIRED}, "CALLS": str(calls)}
    if missing:
        env.pop(missing, None)
    command = DATA["deploy"][step][0]["run"]
    script = """kubectl() { echo "$*" >> "$CALLS"; }
envsubst() { cat; }
""" + command + "\necho SHELL_SURVIVED\nexit\n"
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-i"], input=script,
        text=True, capture_output=True, env=env, timeout=10,
    )
    assert "SHELL_SURVIVED" in result.stdout
    if missing:
        assert missing in result.stderr
        assert not calls.exists(), result.stderr
    else:
        assert "apply -f -" in calls.read_text()
