"""Offline rendering checks for the opt-in vLLM tracing component.

The shared ``vllm-deployment`` Kustomize component is composed with every
vLLM provider overlay in the tracing guides. Overlays whose model server runs
``vllm serve`` directly in a ``Deployment`` compose cleanly; the rest (shell
launchers, vendor entrypoints, ``LeaderWorkerSet``) are rejected by the
component's guard. Compatibility is probed dynamically, so a new compatible
accelerator is covered automatically without touching these tests.

Run from the repository root:

    uv run --with pytest --with pyyaml python -m pytest scripts/tests/test_modelserver_tracing.py
"""

import copy
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT = REPO_ROOT / "guides/recipes/observability/tracing/components/vllm-deployment"
TRACING_GUIDES = ["optimized-baseline", "precise-prefix-cache-routing"]
# vLLM provider overlays that cannot take the component: their model server
# does not run ``vllm serve`` directly in a kind: Deployment. Probing them
# must be rejected, so they are listed to keep the expected set explicit and
# to stay in sync with the "excluded configurations" note in the tracing docs.
EXPECTED_INCOMPATIBLE = {
    "optimized-baseline": {
        Path("gpu/vllm/ocp"),  # Shell launcher for the node-local model cache.
        Path("biren/vllm/base"),  # Vendor entrypoint wraps vllm serve.
        Path("iluvatar/vllm/base"),  # Shell launcher configures the accelerator.
        # Dynamic slices use LeaderWorkerSets instead of Deployments.
        Path("tpu/v7-dynamic-slice/vllm/2x2x1"),
        Path("tpu/v7-dynamic-slice/vllm/2x2x2"),
    },
    "precise-prefix-cache-routing": {
        Path("gpu/vllm/ocp"),  # Shell launcher for the node-local model cache.
    },
}
TRACING_ARGS = [
    "--otlp-traces-endpoint=$(OTEL_EXPORTER_OTLP_ENDPOINT)",
]
TRACING_ENV = {
    "OTEL_SERVICE_NAME": "vllm-decode",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
    "OTEL_TRACES_SAMPLER_ARG": "0.1",
}


def build(path, *, check=True):
    if shutil.which("kustomize"):
        command = ["kustomize", "build"]
    elif shutil.which("kubectl"):
        command = ["kubectl", "kustomize"]
    else:
        pytest.fail("Install kustomize or kubectl to run tracing render checks")
    result = subprocess.run(command + [str(path)], capture_output=True, text=True)
    if not check:
        return result
    assert result.returncode == 0, result.stderr
    return list(yaml.safe_load_all(result.stdout))


def modelserver(resources):
    deployments = [item for item in resources if item["kind"] == "Deployment"]
    assert len(deployments) == 1
    container = deployments[0]["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "modelserver"
    return container


def vllm_provider_overlays(guide):
    """All ``<accel>/.../vllm/<provider>`` overlay dirs (with a kustomization).

    Relative to ``guides/<guide>/modelserver``, e.g. ``gpu/vllm/base`` or
    ``tpu/v7-dynamic-slice/vllm/2x2x1``.
    """
    root = REPO_ROOT / "guides" / guide / "modelserver"
    providers = set()
    for engine in root.rglob("vllm"):
        if not engine.is_dir():
            continue
        for provider in engine.iterdir():
            if provider.is_dir() and (provider / "kustomization.yaml").is_file():
                providers.add(provider.relative_to(root))
    return sorted(providers)


def write_overlay(path, resources, *, components=(), patches=()):
    path.mkdir(parents=True, exist_ok=True)
    data = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": [os.path.relpath(resource, path) for resource in resources],
    }
    if components:
        data["components"] = [os.path.relpath(component, path) for component in components]
    if patches:
        data["patches"] = list(patches)
    (path / "kustomization.yaml").write_text(yaml.safe_dump(data))


def compose(target, guide, provider):
    """Write the docs' outer Kustomization composing a provider overlay with
    the shared component, then return its path."""
    root = REPO_ROOT / "guides" / guide / "modelserver"
    write_overlay(target, [root / provider], components=[COMPONENT])
    return target


def tracing_applied(traced):
    """True when the component enabled tracing on exactly one vLLM Deployment.

    The component only patches ``kind: Deployment`` with label
    ``llm-d.ai/role=decode``; an overlay that renders no such workload (for
    example a ``LeaderWorkerSet``) composes successfully but is left
    unchanged, so it does not count as traced.
    """
    deployments = [item for item in traced if item["kind"] == "Deployment"]
    if len(deployments) != 1:
        return False
    container = deployments[0]["spec"]["template"]["spec"]["containers"][0]
    if container.get("name") != "modelserver":
        return False
    return any(arg in TRACING_ARGS for arg in container["args"])


def probe(target, guide, provider):
    """Compose a provider overlay with the component and classify it.

    Returns ``(result, applied)`` where ``result`` is the build process and
    ``applied`` is True only when the component enabled tracing on a single
    vLLM Deployment. Shell launchers and vendor entrypoints fail the
    component's guard (non-zero exit); ``LeaderWorkerSet`` overlays build
    cleanly but are left unchanged (``applied`` is False).
    """
    result = build(compose(target, guide, provider), check=False)
    if result.returncode != 0:
        return result, False
    return result, tracing_applied(list(yaml.safe_load_all(result.stdout)))


PROVIDERS = [
    (guide, provider.as_posix())
    for guide in TRACING_GUIDES
    for provider in vllm_provider_overlays(guide)
]


def test_tracing_guides_declared():
    for guide in TRACING_GUIDES:
        assert (REPO_ROOT / "guides" / guide / "modelserver").is_dir(), guide
        assert guide in EXPECTED_INCOMPATIBLE, (
            f"Add {guide!r} to EXPECTED_INCOMPATIBLE (use set() if none are incompatible)"
        )
        assert vllm_provider_overlays(guide), f"No vLLM overlays found for {guide!r}"


@pytest.mark.parametrize("guide", TRACING_GUIDES)
def test_probed_incompatible_providers_match_expected(tmp_path, guide):
    """The component must trace exactly the overlays not in EXPECTED_INCOMPATIBLE.

    This is the single source of truth for compatibility: a stale entry, a
    newly incompatible overlay, or an overlay that should trace fails here,
    while a new compatible accelerator passes without touching this table.
    """
    not_traced = set()
    for provider in vllm_provider_overlays(guide):
        target = tmp_path / provider.as_posix().replace("/", "_")
        result, applied = probe(target, guide, provider)
        if not applied:
            not_traced.add(provider)
    expected = EXPECTED_INCOMPATIBLE[guide]
    assert not_traced == expected, (
        f"{guide!r}: overlays the component does not trace "
        f"{sorted(p.as_posix() for p in not_traced)} != "
        f"expected {sorted(p.as_posix() for p in expected)}. "
        f"Update EXPECTED_INCOMPATIBLE and the tracing docs if an overlay changed."
    )


@pytest.mark.parametrize("guide,provider", PROVIDERS)
def test_tracing_composition_per_provider(tmp_path, guide, provider):
    prov = Path(provider)
    root = REPO_ROOT / "guides" / guide / "modelserver"

    if prov in EXPECTED_INCOMPATIBLE[guide]:
        target = tmp_path / "overlay"
        result, applied = probe(target, guide, prov)
        assert not applied
        # Shell launchers / vendor entrypoints trip the component guard;
        # LeaderWorkerSet overlays build cleanly but are left unchanged.
        if result.returncode != 0:
            assert "test failed" in result.stderr.lower() or "test operation" in result.stderr.lower()
        return

    baseline = build(root / prov)
    traced = build(compose(tmp_path / "overlay", guide, prov))
    original = modelserver(baseline)
    container = modelserver(traced)

    assert not any(arg.startswith("--otlp-traces-endpoint") for arg in original["args"])
    assert not any(arg.startswith("--collect-detailed-traces") for arg in original["args"])
    assert not any(env["name"].startswith("OTEL_") for env in original.get("env", []))
    assert container["args"] == original["args"] + TRACING_ARGS
    names = [env["name"] for env in container["env"]]
    assert len(names) == len(set(names))
    assert {
        env["name"]: env["value"] for env in container["env"]
        if env["name"] in TRACING_ENV
    } == TRACING_ENV

    # Removing only tracing configuration must recover every original resource.
    restored = copy.deepcopy(traced)
    restored_container = modelserver(restored)
    restored_container["args"] = restored_container["args"][:-len(TRACING_ARGS)]
    restored_container["env"] = [
        env for env in restored_container["env"] if env["name"] not in TRACING_ENV
    ]
    assert restored == baseline


def test_collector_and_sampling_can_be_overridden_without_replacing_args(tmp_path):
    base = REPO_ROOT / "guides/optimized-baseline/modelserver/gpu/vllm/base"
    endpoint = "http://otel-collector.observability.svc.cluster.local:4317"
    patch = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "decode"},
        "spec": {"template": {"spec": {"containers": [{
            "name": "modelserver",
            "env": [
                {"name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": endpoint},
                {"name": "OTEL_TRACES_SAMPLER_ARG", "value": "1.0"},
            ],
        }]}}},
    }
    overlay = tmp_path / "overlay"
    write_overlay(overlay, [base], components=[COMPONENT], patches=[{
        "target": {"kind": "Deployment", "labelSelector": "llm-d.ai/role=decode"},
        "patch": yaml.safe_dump(patch),
    }])
    container = modelserver(build(overlay))
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == endpoint
    assert env["OTEL_TRACES_SAMPLER_ARG"] == "1.0"
    # The env override replaces only sampling/endpoint; the component still
    # appends the tracing argument exactly once.
    plain_args = modelserver(build(compose(tmp_path / "plain", "optimized-baseline",
                                           Path("gpu/vllm/base"))))["args"]
    assert container["args"] == plain_args


def test_component_replaces_existing_tracing_env_without_leaving_value_from(tmp_path):
    resources = build(REPO_ROOT / "guides/optimized-baseline/modelserver/gpu/vllm/base")
    original = modelserver(resources)
    original["env"].extend({
        "name": name,
        "valueFrom": {"secretKeyRef": {"name": "tracing-settings", "key": name}},
    } for name in TRACING_ENV)
    source = tmp_path / "source"
    source.mkdir()
    resource = source / "resources.yaml"
    resource.write_text(yaml.safe_dump_all(resources))
    write_overlay(source, [resource])
    traced = tmp_path / "traced"
    write_overlay(traced, [source], components=[COMPONENT])
    container = modelserver(build(traced))
    entries = [env for env in container["env"] if env["name"] in TRACING_ENV]
    assert len(entries) == len(TRACING_ENV)
    assert all(set(env) == {"name", "value"} for env in entries)
    assert {env["name"]: env["value"] for env in entries} == TRACING_ENV
    assert [env for env in container["env"] if env["name"] not in TRACING_ENV] == [
        env for env in original["env"] if env["name"] not in TRACING_ENV
    ]


def test_component_leaves_other_roles_and_workload_kinds_unchanged(tmp_path):
    resources = build(REPO_ROOT / "guides/optimized-baseline/modelserver/gpu/vllm/base")
    decode = next(item for item in resources if item["kind"] == "Deployment")
    prefill = copy.deepcopy(decode)
    prefill["metadata"]["name"] = "prefill"
    prefill["metadata"]["labels"]["llm-d.ai/role"] = "prefill"
    prefill["spec"]["selector"]["matchLabels"]["llm-d.ai/role"] = "prefill"
    prefill["spec"]["template"]["metadata"]["labels"]["llm-d.ai/role"] = "prefill"
    resources.append(prefill)
    for path in [
        "guides/optimized-baseline/modelserver/tpu/v7-dynamic-slice/vllm/2x2x1/lws.yaml",
        "guides/pd-disaggregation/modelserver/gpu/vllm/base/disaggregatedset.yaml",
    ]:
        workload = yaml.safe_load((REPO_ROOT / path).read_text())
        workload["metadata"].setdefault("labels", {})["llm-d.ai/role"] = "decode"
        resources.append(workload)

    source = tmp_path / "resources.yaml"
    source.write_text(yaml.safe_dump_all(resources))
    write_overlay(tmp_path, [source], components=[COMPONENT])
    traced = build(tmp_path)
    def key(item):
        return item["apiVersion"], item["kind"], item["metadata"]["name"]

    original_by_key = {key(item): item for item in resources}
    traced_by_key = {key(item): item for item in traced}
    assert traced_by_key.keys() == original_by_key.keys()
    for resource_key, original in original_by_key.items():
        if resource_key != key(decode):
            assert traced_by_key[resource_key] == original
    container = traced_by_key[key(decode)]["spec"]["template"]["spec"]["containers"][0]
    assert container["args"] == decode["spec"]["template"]["spec"]["containers"][0]["args"] + TRACING_ARGS


@pytest.mark.parametrize("name,command", [
    ("modelserver", ["bash", "-c"]),
    ("modelserver", ["python3", "-m", "sglang.launch_server"]),
    ("another-container", ["vllm", "serve"]),
])
def test_component_rejects_incompatible_container_layout(tmp_path, name, command):
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "decode", "labels": {"llm-d.ai/role": "decode"}},
        "spec": {"template": {"spec": {"containers": [{
            "name": name, "command": command, "args": ["model"], "env": [],
        }]}}},
    }
    resource = tmp_path / "deployment.yaml"
    resource.write_text(yaml.safe_dump(deployment))
    write_overlay(tmp_path, [resource], components=[COMPONENT])
    result = build(tmp_path, check=False)
    assert result.returncode != 0
    assert "test failed" in result.stderr.lower() or "test operation" in result.stderr.lower()