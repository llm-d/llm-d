"""The multitenant guide's llm-d-async overlays stay consistent across backends.

Every Redis and Pub/Sub overlay under values/ describes the same three team
queues (subscriptions on Pub/Sub) in one `teams` worker pool, with the same
tiers and quota gates, and stamps the guide's six lane objectives. These tests
keep the overlays from drifting apart as scenarios are edited one at a time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MT = ROOT / "guides/batch-serving/asynchronous-processing/multitenant"
OVERLAYS = sorted(p.relative_to(MT / "values").as_posix() for p in (MT / "values").glob("*/*.yaml")
                  if p.parent.name in ("redis", "pubsub"))
# Overlays whose gates read a self-hosted Prometheus, which should also scrape
# llm-d-async itself (Scenario C and D). The GMP overlay uses GMP PodMonitoring.
PROMETHEUS_SCRAPED = {"redis/saturation-prometheus.yaml", "pubsub/saturation-prometheus.yaml",
                      "redis/tier-priority-admission.yaml", "pubsub/tier-priority-admission.yaml"}
TEAMS = {"premium": ("interactive", "2"), "standard": ("async", "2"), "batch": ("batch", "1")}


def _ap(overlay: str) -> dict:
    return yaml.safe_load((MT / "values" / overlay).read_text())["ap"]


def _entries(ap: dict) -> dict[str, dict]:
    """Team queue (Redis) or subscription (Pub/Sub) entries, keyed by team."""
    out = {}
    transport = ap["transportConfig"]
    for q in transport.get("queues") or transport["topics"]:  # Redis queues, Pub/Sub topics
        name = q.get("queue_name") or q["subscriber_id"]
        team = next(t for t in TEAMS if f"team-{t}" in name)
        out[team] = q
    return out


def test_overlays_found() -> None:
    assert len(OVERLAYS) == 7, OVERLAYS
    assert PROMETHEUS_SCRAPED <= set(OVERLAYS)


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_team_queues_tiers_and_quotas(overlay: str) -> None:
    ap = _ap(overlay)
    entries = _entries(ap)
    assert set(entries) == set(TEAMS)
    assert [p["id"] for p in ap["workerPools"]] == ["teams"]
    for team, (tier, limit) in TEAMS.items():
        q = entries[team]
        assert q["worker_pool_id"] == "teams", team
        assert q["labels"]["tier"] == tier, team
        # By Service name: llm-d-async runs in the router's namespace.
        assert q["igw_base_url"] == "http://llm-d-router-epp:80", team
        gate = q["gate_params"]
        assert q["gate_type"] == "redis-quota", team
        assert (gate["attribute"], gate["gating_mode"], gate["prefix"], gate["limit"]) == \
            ("team", "classifying", "quota:", limit), team


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_no_queue_level_inference_objective(overlay: str) -> None:
    # A queue-level inference_objective is sent as a second objective header
    # (x-gateway-inference-objective) next to the lane objective; the guide's
    # objectives are the six lanes only.
    for team, q in _entries(_ap(overlay)).items():
        assert "inference_objective" not in q, f"{overlay}: {team}"


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_lane_objectives_match_the_guide_objectives(overlay: str) -> None:
    objectives = {d["metadata"]["name"] for d in yaml.safe_load_all(
        (MT / "manifests/objectives/inferenceobjectives.yaml").read_text()) if d}
    policy = _ap(overlay)["requestMergePolicyConfig"]
    assert policy["type"] == "tier-priority"
    lanes = policy["parameters"]["lane_objectives"]
    assert set(lanes.values()) == objectives
    assert policy["parameters"]["objective_header"] == "x-llm-d-inference-objective"


@pytest.mark.parametrize("overlay", sorted(PROMETHEUS_SCRAPED))
def test_prometheus_overlays_scrape_the_processor(overlay: str) -> None:
    # Moving between Scenario C and D must not delete llm-d-async's own scrape.
    ap = _ap(overlay)
    assert ap["podMonitor"]["enabled"] is True
    assert ap["prometheusRule"]["enabled"] is True
    assert ap["grafana"]["dashboards"]["enabled"] is True


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_no_namespace_or_router_placeholders(overlay: str) -> None:
    # Redis and the router are addressed by Service name, so the default
    # values install without rendering; only POOL_NAME, SAT_CAP, PROM_URL and
    # PROJECT_ID remain, on the alternative overlays.
    text = (MT / "values" / overlay).read_text()
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "NAMESPACE" not in body and "IGW_HOST" not in body, overlay
    if overlay == "redis/quota-only.yaml":
        assert not re.search(r"POOL_NAME|SAT_CAP|PROM_URL|PROJECT_ID", body)


def test_objectives_and_pod_monitor_bind_the_router_pool() -> None:
    # The router release is named llm-d-router, so its InferencePool is too.
    objectives = [d for d in yaml.safe_load_all(
        (MT / "manifests/objectives/inferenceobjectives.yaml").read_text()) if d]
    assert len(objectives) == 6
    for o in objectives:
        assert o["spec"]["poolRef"]["name"] == "llm-d-router", o["metadata"]["name"]
        assert "namespace" not in o["metadata"], o["metadata"]["name"]  # applied with -n
    monitor = yaml.safe_load((MT / "manifests/monitoring/prometheus-vllm-podmonitor.yaml").read_text())
    relabel = monitor["spec"]["podMetricsEndpoints"][0]["relabelings"][0]
    assert relabel == {"targetLabel": "inference_pool", "replacement": "llm-d-router"}
    assert monitor["spec"]["selector"]["matchLabels"] == {"llm-d.ai/guide": "async-multitenant"}


def test_model_server_overlay_carries_the_label_the_router_selects() -> None:
    kust = yaml.safe_load((MT / "modelserver/gpu/vllm/base/kustomization.yaml").read_text())
    pairs = [lbl["pairs"] for lbl in kust["labels"]]
    assert {"llm-d.ai/guide": "async-multitenant"} in pairs
    assert kust["replicas"][0]["count"] == 1
    for values in ("flow-control-holdback.yaml", "flow-control-evictable.yaml"):
        router = yaml.safe_load((MT / "values/router" / values).read_text())
        assert router["router"]["modelServers"]["matchLabels"] == {"llm-d.ai/guide": "async-multitenant"}, values
