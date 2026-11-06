"""AWS DLC drift should be visible without blocking on release availability."""

import importlib.util
from pathlib import Path

import pytest
import yaml


MODULE_PATH = Path(__file__).parents[1] / "check-aws-image-versions.py"
SPEC = importlib.util.spec_from_file_location("check_aws_image_versions", MODULE_PATH)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


@pytest.mark.parametrize("tag, expected", [
    ("v0.31.0", (0, 31, 0)),
    ("v0.5.13.post1", (0, 5, 13)),
    ("0.5.13-gpu-py312-cu130-ubuntu24.04-ec2", (0, 5, 13)),
    ("0.5.13.post1-gpu-py312-cu130-ubuntu24.04-ec2", (0, 5, 13)),
    ("nightly", None),
    ("nightly-20261031", None),
    ("main", None),
    ("v0.31.0rc1", None),
    ("v0.31.0-rc1", None),
    ("sha256:abc", None),
    (None, None),
])
def test_release_tags(tag, expected):
    assert checker.release_version({"newTag": tag}) == expected


def test_digest_takes_precedence_over_a_release_tag():
    assert checker.release_version({"newTag": "v0.31.0", "digest": "sha256:abc"}) is None


def write_pins(root, *, sglang_upstream="v0.5.13.post1", sglang_aws="0.5.13-gpu-py312-cu130-ubuntu24.04-ec2"):
    for engine, upstream_path, aws_path, aws_name, _ in checker.PAIRS:
        upstream_tag = sglang_upstream if engine == "SGLang" else "v0.31.0"
        aws_tag = sglang_aws if engine == "SGLang" else "0.31.0-gpu-py312-cu130-ubuntu24.04-ec2"
        for path, name, tag in ((upstream_path, "REPLACE_MODEL_SERVER_IMAGE", upstream_tag),
                                (aws_path, aws_name, aws_tag)):
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(yaml.safe_dump({"images": [{"name": name, "newTag": tag}]}),
                                   encoding="utf-8")


def test_matching_base_release_does_not_warn_for_post_release(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    write_pins(tmp_path)
    checker.check(tmp_path)
    output = capsys.readouterr().out
    assert "WARNING" not in output
    assert "OK: SGLang" in output
    assert "share release 0.5.13" in output


def test_drift_is_advisory_and_identifies_aws_pin_and_gallery(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    write_pins(tmp_path, sglang_upstream="v0.5.22", sglang_aws="0.5.20-gpu-py312-cu130-ubuntu24.04-ec2")
    assert checker.main() == 0
    output = capsys.readouterr().out
    assert "::warning file=guides/pd-disaggregation/modelserver/gpu/sglang/aws/kustomization.yaml" in output
    assert "upstream v0.5.22 differs from AWS 0.5.20" in output
    assert "https://gallery.ecr.aws/deep-learning-containers/sglang" in output
    assert "validate the AWS image" in output


@pytest.mark.parametrize("upstream, aws", [
    ("nightly", "0.5.13-gpu-py312-cu130-ubuntu24.04-ec2"),
    ("v0.5.13", "main"),
])
def test_nonrelease_pins_are_not_reported_as_drift(tmp_path, capsys, monkeypatch, upstream, aws):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    write_pins(tmp_path, sglang_upstream=upstream, sglang_aws=aws)
    checker.check(tmp_path)
    output = capsys.readouterr().out
    assert "NOTICE:" in output
    assert "cannot compare release versions" in output
    assert "WARNING" not in output


def test_missing_expected_image_fails_instead_of_silently_skipping(tmp_path, capsys, monkeypatch):
    write_pins(tmp_path)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    aws_path = tmp_path / checker.PAIRS[1][2]
    aws_path.write_text("images: []\n", encoding="utf-8")
    assert checker.main() == 1
    assert "expected exactly one image" in capsys.readouterr().err


def test_malformed_image_config_fails_with_a_diagnostic(tmp_path, capsys, monkeypatch):
    write_pins(tmp_path)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    (tmp_path / checker.PAIRS[0][1]).write_text("images: [\n", encoding="utf-8")
    assert checker.main() == 1
    assert "ERROR:" in capsys.readouterr().err
