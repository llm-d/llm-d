"""The multitenant guide's optional coordinator files stay consistent with the guide.

values/redis/quota-only-coordinator.yaml is a full llm-d-async overlay (Helm
replaces lists, so it cannot be layered on quota-only.yaml). These tests keep it
identical to quota-only.yaml apart from the coordinator queues and pool, and keep
the coordinator config pointing at those queues with the guide's quota scheme.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MT = ROOT / "guides/batch-serving/asynchronous-processing/multitenant"
COORD_QUEUES = {"coord-standard-a", "coord-batch-a"}


def _load(path: Path):
    return yaml.safe_load(path.read_text())


def _split(values: dict) -> tuple[dict, list, list]:
    queues = values["ap"]["transportConfig"]["queues"]
    pools = values["ap"]["workerPools"]
    return values, [q for q in queues if q["queue_name"] in COORD_QUEUES], [p for p in pools if p["id"] == "coord"]


def test_coordinator_overlay_is_quota_only_plus_coordinator_entries() -> None:
    base = _load(MT / "values/redis/quota-only.yaml")
    coord = _load(MT / "values/redis/quota-only-coordinator.yaml")
    _, coord_queues, coord_pools = _split(coord)
    assert {q["queue_name"] for q in coord_queues} == COORD_QUEUES
    assert len(coord_pools) == 1 and coord_pools[0]["workers"] > 10  # above the router's maxConcurrency

    stripped = yaml.safe_load(yaml.safe_dump(coord))
    stripped["ap"]["transportConfig"]["queues"] = [
        q for q in stripped["ap"]["transportConfig"]["queues"] if q["queue_name"] not in COORD_QUEUES]
    stripped["ap"]["workerPools"] = [p for p in stripped["ap"]["workerPools"] if p["id"] != "coord"]
    assert stripped == base


def test_coordinator_queues_shape() -> None:
    _, coord_queues, _ = _split(_load(MT / "values/redis/quota-only-coordinator.yaml"))
    team_gate = next(q for q in _load(MT / "values/redis/quota-only.yaml")["ap"]["transportConfig"]["queues"]
                     if q["queue_name"] == "team-batch-a")["gate_params"]
    for q in coord_queues:
        assert q["worker_pool_id"] == "coord"
        assert "result_queue_name" not in q  # results go to each message's own mailbox
        assert q["result_ttl_seconds"] > 0
        for key in ("address", "attribute", "mode", "gating_mode", "prefix"):
            assert q["gate_params"][key] == team_gate[key], key


def test_coordinator_config_routes_to_overlay_queues() -> None:
    config = _load(MT / "manifests/coordinator/config.yaml")
    step = config["pipeline"]["steps"][0]
    assert step["type"] == "async-broker"
    params = step["params"]
    routed = {r["queue"] for r in params["routes"] if "queue" in r} | {params["default_queue"]}
    assert routed == COORD_QUEUES
    team_gate = _load(MT / "values/redis/quota-only.yaml")["ap"]["transportConfig"]["queues"][0]["gate_params"]
    assert params["quota"]["prefix"] == team_gate["prefix"]
    assert params["quota"]["attribute"] == team_gate["attribute"]
    assert config["server"]["secure_serving"] is False  # the router and clients speak plain HTTP to it


def test_coordinator_manifest_has_one_image_placeholder() -> None:
    text = (MT / "manifests/coordinator/coordinator.yaml").read_text()
    assert text.count("COORDINATOR_IMAGE") == 1
    kinds = [d["kind"] for d in yaml.safe_load_all(text)]
    assert kinds == ["ServiceAccount", "Deployment", "Service"]
