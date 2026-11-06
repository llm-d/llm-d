#!/usr/bin/env python3
"""Warn when AWS EFA model-server images differ from shared GPU releases.

AWS DLC builds can lag upstream releases, so drift is advisory rather than a
reason to replace a working EFA image with an unvalidated upstream image.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
IMAGES = Path("guides/recipes/modelserver/components/images")
PAIRS = (
    ("vLLM", IMAGES / "gpu-vllm/release/kustomization.yaml",
     IMAGES / "gpu-vllm/aws-efa/release/kustomization.yaml",
     "REPLACE_MODEL_SERVER_IMAGE", "https://gallery.ecr.aws/deep-learning-containers/vllm"),
    ("SGLang", IMAGES / "gpu-sglang/release/kustomization.yaml",
     Path("guides/pd-disaggregation/modelserver/gpu/sglang/aws/kustomization.yaml"),
     "docker.io/lmsysorg/sglang", "https://gallery.ecr.aws/deep-learning-containers/sglang"),
)
# Compare the release triplet, ignoring post releases and the AWS DLC platform
# suffix. Do not treat RCs, floating/nightly tags or digests as release versions.
RELEASE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?(?:-gpu-[A-Za-z0-9_.-]+)?")


def release_version(image: dict) -> tuple[int, int, int] | None:
    tag = image.get("newTag")
    if image.get("digest") or not isinstance(tag, str):
        return None
    match = RELEASE.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def read_image(path: Path, name: str) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("images"), list):
        raise ValueError(f"{path}: expected an images list")
    matches = [image for image in document["images"]
               if isinstance(image, dict) and image.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"{path}: expected exactly one image named {name!r}")
    return matches[0]


def report(level: str, path: Path, message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level} file={path.as_posix()},title=AWS image version check::{escaped}")
    else:
        print(f"{level.upper()}: {path.as_posix()}: {message}")


def check(root: Path) -> None:
    for engine, upstream_path, aws_path, aws_name, gallery in PAIRS:
        upstream = read_image(root / upstream_path, "REPLACE_MODEL_SERVER_IMAGE")
        aws = read_image(root / aws_path, aws_name)
        upstream_version, aws_version = release_version(upstream), release_version(aws)
        if upstream_version is None or aws_version is None:
            report("notice", aws_path,
                   f"{engine}: cannot compare release versions for upstream "
                   f"{upstream.get('digest') or upstream.get('newTag')!r} and AWS "
                   f"{aws.get('digest') or aws.get('newTag')!r}. Check the pins manually: {gallery}")
        elif upstream_version != aws_version:
            report("warning", aws_path,
                   f"{engine}: upstream {upstream['newTag']} differs from AWS {aws['newTag']}. "
                   f"Check available EFA-compatible DLC tags at {gallery} when updating "
                   f"{upstream_path.as_posix()}; validate the AWS image before changing its pin.")
        else:
            print(f"OK: {engine} upstream {upstream['newTag']} and AWS {aws['newTag']} "
                  f"share release {'.'.join(map(str, upstream_version))}")


def main() -> int:
    try:
        check(ROOT)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
