"""Regression tests for executable snippets in the ModelExpress guide."""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "guides/modelexpress-p2p/README.md"


def _documented_base_image_component() -> Path:
    """Resolve the component path used by the README's Docker build command."""
    readme = README.read_text()
    match = re.search(
        r"yq -r .*?\\\s*\n\s+\$\{REPO_ROOT\}/(?P<path>guides/recipes/"
        r"modelserver/components/images/gpu-vllm/[^\s]+)",
        readme,
        flags=re.DOTALL,
    )
    assert match, "README must document the shared GPU vLLM image lookup"
    return REPO_ROOT / match.group("path")


def test_modelexpress_base_image_lookup_uses_existing_component():
    component_path = _documented_base_image_component()

    assert component_path.is_file(), (
        "README's base image lookup must reference an existing kustomize component: "
        f"{component_path.relative_to(REPO_ROOT)}"
    )

    component = yaml.safe_load(component_path.read_text())
    image = next(
        (
            image
            for image in component.get("images", [])
            if image.get("name") == "REPLACE_MODEL_SERVER_IMAGE"
        ),
        None,
    )
    assert image and image.get("newName") and image.get("newTag"), (
        "the documented GPU vLLM component must provide a complete "
        "REPLACE_MODEL_SERVER_IMAGE entry"
    )
