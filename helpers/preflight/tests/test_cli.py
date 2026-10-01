"""Tests for helpers/preflight/preflight.py — guide config and the CLI.

Run from the repo root:

    python -m pytest helpers/preflight/tests/ -v
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "helpers" / "preflight"))
import preflight  # noqa: E402

WIDE_EP = REPO_ROOT / "guides" / "wide-ep"


def test_wide_ep_config_loads():
    cfg = preflight.load_guide_config(WIDE_EP / "preflight.yaml")
    assert cfg.lws_min_version == (0, 11, 0)
    assert cfg.driver_max_major_exclusive == 580
    assert cfg.driver_severity == "warn"
    assert cfg.router_standalone == preflight.RouterSize(Decimal(8), Decimal(16 * 2**30))
    assert "leaderworkersets.leaderworkerset.x-k8s.io" in cfg.crds
    assert cfg.docs["rdma"] == "docs/infrastructure/rdma/README.md"


def test_missing_config_is_a_usage_error(tmp_path):
    with pytest.raises(preflight.PreflightError, match="no preflight requirements"):
        preflight.load_guide_config(tmp_path / "preflight.yaml")


def test_bad_severity_is_rejected(tmp_path):
    text = (WIDE_EP / "preflight.yaml").read_text().replace("severity: warn", "severity: loud")
    (tmp_path / "preflight.yaml").write_text(text)
    with pytest.raises(preflight.PreflightError, match="severity"):
        preflight.load_guide_config(tmp_path / "preflight.yaml")


def test_missing_key_is_rejected(tmp_path):
    (tmp_path / "preflight.yaml").write_text("crds: []\n")
    with pytest.raises(preflight.PreflightError, match="invalid preflight config"):
        preflight.load_guide_config(tmp_path / "preflight.yaml")


@pytest.mark.parametrize("raw, expected", [("v0.11.0", (0, 11, 0)), ("0.10.2", (0, 10, 2)), ("main", None), ("", None)])
def test_parse_semver(raw, expected):
    assert preflight.parse_semver(raw) == expected
