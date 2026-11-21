"""Regression tests for the KEDA pool-saturation metric target."""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OVERLAY_ROOT = REPO_ROOT / "guides/workload-autoscaling/keda-epp/optimized-baseline/overlays"


@pytest.mark.parametrize("platform", ["k8s", "ocp"])
def test_saturation_ratio_uses_value_target_and_counts_remain_average(platform: str) -> None:
    """A normalized pool ratio is a global value; request counts are per-replica."""
    scaled_object = yaml.safe_load(
        (OVERLAY_ROOT / platform / "saturation/scaledobject.yaml").read_text()
    )
    triggers = {trigger["name"]: trigger for trigger in scaled_object["spec"]["triggers"]}

    assert triggers["pool-saturation"]["metricType"] == "Value"
    assert triggers["epp-running-requests"]["metricType"] == "AverageValue"
