"""Regression tests for environment variables in E/P/D overlays."""

from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
OVERLAY_ROOT = (
    ROOT
    / "guides/multimodal-serving/e-disaggregation/modelserver/gpu/vllm/e-p-d"
)


@pytest.mark.parametrize("variant", ["base", "gke"])
def test_rendered_containers_have_unique_environment_names(variant: str) -> None:
    result = subprocess.run(
        ["kustomize", "build", str(OVERLAY_ROOT / variant)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    encode_user_values = []
    for resource in yaml.safe_load_all(result.stdout):
        if not resource or resource.get("kind") != "Deployment":
            continue
        for container in resource["spec"]["template"]["spec"].get("containers", []):
            env = container.get("env", [])
            names = [item["name"] for item in env]
            assert len(names) == len(set(names)), (
                f"{resource['metadata']['name']}/{container['name']} has duplicate env names"
            )
            if resource["metadata"]["name"].endswith("encode"):
                encode_user_values.extend(
                    item.get("value") for item in env if item["name"] == "USER"
                )

    assert encode_user_values == ["llm-d"]
