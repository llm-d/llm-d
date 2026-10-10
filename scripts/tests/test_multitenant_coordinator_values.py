"""The multitenant guide's optional coordinator stays consistent with the guide.

The coordinator (README step 4) is one more producer of llm-d-async traffic:
its queued tenants are written to the guide's own team queues and must be
treated exactly like requests published there directly. These tests keep the
coordinator config pointing at those queues with matching tiers and quota
gates, and keep the team queues able to return results to the coordinator.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MT = ROOT / "guides/batch-serving/asynchronous-processing/multitenant"
REDIS_VALUES = ["quota-only.yaml", "saturation-prometheus.yaml", "tier-priority-admission.yaml"]


def _load(path: Path):
    return yaml.safe_load(path.read_text())


def _broker() -> dict:
    config = _load(MT / "manifests/coordinator/config.yaml")
    step = config["pipeline"]["steps"][0]
    assert step["type"] == "async-broker"
    return step["params"]


def _team_queues(values_file: str = "quota-only.yaml") -> dict[str, dict]:
    values = _load(MT / "values/redis" / values_file)
    return {q["queue_name"]: q for q in values["ap"]["transportConfig"]["queues"]}


def test_queued_tenants_use_the_team_queues_with_matching_tiers() -> None:
    params = _broker()
    queues = _team_queues()
    routed = {r["tenant"]: r for r in params["routes"] if "queue" in r}
    assert routed, "the coordinator routes no tenant to a queue"
    for tenant, route in routed.items():
        assert route["queue"] in queues, f"{tenant} routes to {route['queue']}, not a team queue"
        assert queues[route["queue"]]["labels"]["tier"] == route["tier"], tenant
    assert params["default_queue"] in queues
    assert queues[params["default_queue"]]["labels"]["tier"] == params["default_tier"]
    # Every team queue is reachable over HTTP, so the coordinator covers the same tiers.
    assert {r["queue"] for r in routed.values()} == set(queues)


def test_coordinator_quota_matches_the_team_queue_gates() -> None:
    params = _broker()
    queues = _team_queues()
    quota = params["quota"]
    for route in params["routes"]:
        if "queue" not in route:
            continue
        gate = queues[route["queue"]]["gate_params"]
        assert quota["prefix"] == gate["prefix"]
        assert quota["attribute"] == gate["attribute"]
        assert str(quota["limits"][route["tenant"]]) == gate["limit"], route["tenant"]


def test_realtime_tenant_is_live_only_and_never_overflow() -> None:
    params = _broker()
    realtime = next(r for r in params["routes"] if r["tenant"] == "realtime")
    assert "queue" not in realtime and realtime["tier"] == "interactive"
    assert "realtime" not in params["quota"]["limits"]  # no quota, so always reserved


@pytest.mark.parametrize("values_file", REDIS_VALUES)
def test_team_queues_return_results_to_the_coordinator(values_file: str) -> None:
    # A per-queue result_queue_name takes precedence over the result key each
    # coordinator request carries, so its wait and enqueue modes would never see
    # a result. Requests published straight to Redis carry none and fall back to
    # the transport-level list.
    transport = _load(MT / "values/redis" / values_file)["ap"]["transportConfig"]
    assert transport["result_queue_name"] == "results-list"
    for name, q in _team_queues(values_file).items():
        assert "result_queue_name" not in q, f"{values_file}: {name}"
        assert q["result_ttl_seconds"] > 0, f"{values_file}: {name}"


def test_coordinator_config_shape() -> None:
    config = _load(MT / "manifests/coordinator/config.yaml")
    params = _broker()
    assert config["server"]["secure_serving"] is False  # the router and clients speak plain HTTP to it
    # The headers the README documents, pinned so a change in the coordinator's
    # defaults cannot silently turn every request into unclassified passthrough.
    assert params["mode_header"] == "x-llm-d-async-mode"
    assert params["tenant_header"] == "x-llm-d-tenant"


def test_coordinator_overlay() -> None:
    text = (MT / "manifests/coordinator/coordinator.yaml").read_text()
    # The image placeholder the shared coordinator image component replaces.
    assert text.count("image: REPLACE_COORDINATOR_IMAGE") == 1
    kinds = [d["kind"] for d in yaml.safe_load_all(text)]
    assert kinds == ["ServiceAccount", "Deployment", "Service"]
    deploy = next(d for d in yaml.safe_load_all(text) if d["kind"] == "Deployment")
    assert deploy["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] == "llm-d-coordinator-config"
    kust = _load(MT / "manifests/coordinator/kustomization.yaml")
    assert kust["configMapGenerator"] == [
        {"name": "llm-d-coordinator-config", "files": ["coordinator.yaml=config.yaml"]}]
    assert [c.rsplit("/", 2)[-2:] for c in kust["components"]] == [["coordinator", "nightly"]]


def test_coordinator_addresses_router_and_redis_by_service_name() -> None:
    config = _load(MT / "manifests/coordinator/config.yaml")
    assert config["gateway"]["address"] == "http://llm-d-router-epp:80"
    assert _broker()["redis_url"] == "redis://redis:6379"
