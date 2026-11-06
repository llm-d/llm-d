"""Keep the rendered adapter rollout example able to load LoRA adapters."""

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_adapter_rollout_enables_lora_in_rendered_modelserver() -> None:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        pytest.skip("kubectl is required for the kustomize render regression")

    result = subprocess.run(
        [kubectl, "kustomize", str(ROOT / "guides/rollouts/adapter-rollout-example")],
        check=True,
        capture_output=True,
        text=True,
    )
    deployment = next(
        doc for doc in yaml.safe_load_all(result.stdout) if doc["kind"] == "Deployment"
    )
    modelserver = next(
        container
        for container in deployment["spec"]["template"]["spec"]["containers"]
        if container["name"] == "modelserver"
    )
    env = {entry["name"]: entry.get("value") for entry in modelserver["env"]}
    assert env["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] == "True"
    assert "lora_filesystem_resolver" in env["VLLM_PLUGINS"].split(",")
    # Runtime loading and a resolver do not enable the engine's LoRA support.
    assert "--enable-lora" in modelserver["args"]
    assert "--no-enable-lora" not in modelserver["args"]
