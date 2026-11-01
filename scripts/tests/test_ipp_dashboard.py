"""Validate IPP dashboards against the source metric and label contract.

IPP source: llm-d-inference-payload-processor commit
373c6b25056a848be71792395ce4add108d9d682, pkg/metrics/metrics.go.
"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = ROOT / "guides/recipes/observability/grafana/dashboards"
DASHBOARD = json.loads((DASHBOARD_DIR / "llm-d-inference-payload-processor.json").read_text(encoding="utf-8"))
CONTRACT = {
    "ipp_info": ("gauge", {"commit", "build_ref"}),
    "ipp_success_total": ("counter", set()),
    "ipp_body_field_not_found_total": ("counter", {"field"}),
    "ipp_body_field_empty_total": ("counter", {"field"}),
    "ipp_plugin_duration_seconds": ("histogram", {"extension_point", "plugin_type", "plugin_name"}),
    "ipp_model_selector_e2e_duration_seconds": ("histogram", set()),
    "ipp_model_selector_attempt_total": ("counter", {"status"}),
    "ipp_request_ttft_seconds": ("histogram", {"model"}),
}
TARGET_LABELS = {"namespace", "pod"}
SELECTOR = re.compile(r"\b(ipp_[a-z0-9_]+)\{([^}]*)\}")


def queries():
    for panel in DASHBOARD["panels"]:
        for target in panel.get("targets", []):
            yield panel, target


def base_metric(name):
    for suffix in ("_bucket", "_sum", "_count"):
        if name.endswith(suffix) and name[:-len(suffix)] in CONTRACT:
            return name[:-len(suffix)]
    return name


def test_dashboard_queries_use_known_families_and_valid_labels():
    seen = set()
    for panel, target in queries():
        selectors = SELECTOR.findall(target["expr"])
        assert len(selectors) == 1, panel["title"]
        name, matchers = selectors[0]
        family = base_metric(name)
        assert family in CONTRACT, name
        seen.add(family)
        labels = set(re.findall(r"(\w+)\s*(?:=~|!~|!=|=)", matchers))
        assert labels <= CONTRACT[family][1] | TARGET_LABELS, panel["title"]
        assert 'namespace=~"$namespace"' in matchers
        assert 'pod=~"$pod"' in matchers
        for grouping in re.findall(r"\bby\s*\(([^)]+)\)", target["expr"]):
            grouped_labels = {label.strip() for label in grouping.split(",")}
            assert grouped_labels <= CONTRACT[family][1] | TARGET_LABELS | {"le"}, panel["title"]
    assert seen == set(CONTRACT)


def test_histograms_preserve_bucket_boundaries_and_counters_use_rates():
    for panel, target in queries():
        name, _ = SELECTOR.findall(target["expr"])[0]
        kind = CONTRACT[base_metric(name)][0]
        if kind == "histogram":
            assert name.endswith("_bucket")
            assert "histogram_quantile(0.99," in target["expr"]
            assert re.search(r"sum by \(le(?:,|\))", target["expr"])
        if kind in ("counter", "histogram"):
            assert "rate(ipp_" in target["expr"], panel["title"]
            assert "[$__rate_interval]" in target["expr"]


def test_model_filter_is_only_applied_to_ttft():
    for panel, target in queries():
        if "ipp_request_ttft_seconds_bucket" in target["expr"]:
            assert 'model=~"$model"' in target["expr"]
            assert "sum by (le, model)" in target["expr"]
        else:
            assert "$model" not in target["expr"], panel["title"]
    # Neither processing success nor selector attempts is a total inference
    # counter; combining them into an error ratio would be misleading.
    for _, target in queries():
        assert not ("ipp_success_total" in target["expr"] and
                    "ipp_model_selector_attempt_total" in target["expr"])


def test_datasource_and_target_variables_are_usable_for_provisioned_dashboards():
    variables = {var["name"]: var for var in DASHBOARD["templating"]["list"]}
    assert variables["DS_PROMETHEUS"]["type"] == "datasource"
    assert variables["DS_PROMETHEUS"]["query"] == "prometheus"
    assert variables["namespace"]["query"] == "label_values(ipp_info, namespace)"
    assert 'namespace=~"$namespace"' in variables["pod"]["query"]
    assert "ipp_request_ttft_seconds_count" in variables["model"]["query"]
    for name in ("namespace", "pod", "model"):
        assert variables[name]["includeAll"] is True
        assert variables[name]["allValue"] == ".*"
    for panel, target in queries():
        assert panel["datasource"] == {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
        assert target["datasource"] == panel["datasource"]


def test_dashboard_has_no_import_id_or_uid_collision_and_has_unique_panel_ids():
    assert DASHBOARD["id"] is None
    assert len({panel["id"] for panel in DASHBOARD["panels"]}) == len(DASHBOARD["panels"])
    for path in DASHBOARD_DIR.glob("*.json"):
        other = json.loads(path.read_text(encoding="utf-8"))
        if path.name != "llm-d-inference-payload-processor.json":
            assert other.get("uid") != DASHBOARD["uid"], path
    for panel in DASHBOARD["panels"]:
        grid = panel["gridPos"]
        assert 0 <= grid["x"] < 24 and grid["x"] + grid["w"] <= 24


def test_metric_reference_matches_source_types_and_intrinsic_labels():
    reference = (ROOT / "docs/operations/observability/ipp-metrics.md").read_text(encoding="utf-8")
    rows = {}
    for line in reference.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            continue
        names = re.findall(r"\bipp_[a-z0-9_]+\b", cells[0])
        if len(names) == 1 and names[0] in CONTRACT:
            kind = cells[1].strip("*` ").lower()
            labels = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", cells[2])) - {"None", "none"}
            rows[names[0]] = kind, labels
    for name, (kind, labels) in CONTRACT.items():
        assert rows.get(name) == (kind, labels), name
