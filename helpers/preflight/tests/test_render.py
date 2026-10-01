"""Tests for helpers/preflight/preflight.py — render parsing and primitives.

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


@pytest.mark.parametrize(
    "raw, expected",
    [
        (32, Decimal(32)),
        ("8", Decimal(8)),
        ("8000m", Decimal(8)),
        ("15890m", Decimal("15.89")),
        ("512Gi", Decimal(512 * 2**30)),
        ("1Ti", Decimal(2**40)),
        ("100M", Decimal(100 * 10**6)),
        ("1e3", Decimal(1000)),
    ],
)
def test_parse_quantity(raw, expected):
    assert preflight.parse_quantity(raw) == expected


@pytest.mark.parametrize("raw", ["lots", "1.2.3", "1 Gi"])
def test_parse_quantity_rejects_garbage(raw):
    with pytest.raises(ValueError):
        preflight.parse_quantity(raw)


@pytest.mark.parametrize(
    "name, value, expected",
    [
        ("memory", Decimal(512 * 2**30), "512Gi"),
        ("ephemeral-storage", Decimal(2**40), "1Ti"),
        ("memory", Decimal(57 * 2**30 + 2**29), "57.5Gi"),
        ("cpu", Decimal("15.89"), "15.89"),
        ("nvidia.com/gpu", Decimal(8), "8"),
    ],
)
def test_format_quantity(name, value, expected):
    assert preflight.format_quantity(name, value) == expected


GPU_TAINT = preflight.Taint("nvidia.com/gpu", "present", "NoSchedule")


def test_exists_toleration_matches_key():
    tol = preflight.Toleration("nvidia.com/gpu", "Exists", "", "NoSchedule")
    assert preflight.tolerates([tol], GPU_TAINT)


def test_toleration_effect_must_match():
    tol = preflight.Toleration("nvidia.com/gpu", "Exists", "", "NoExecute")
    assert not preflight.tolerates([tol], GPU_TAINT)


def test_equal_toleration_needs_value():
    assert preflight.tolerates([preflight.Toleration("nvidia.com/gpu", "Equal", "present", "")], GPU_TAINT)
    assert not preflight.tolerates([preflight.Toleration("nvidia.com/gpu", "Equal", "other", "")], GPU_TAINT)


def test_empty_key_exists_tolerates_everything():
    assert preflight.tolerates([preflight.Toleration("", "Exists", "", "")], GPU_TAINT)


def test_no_tolerations():
    assert not preflight.tolerates([], GPU_TAINT)
