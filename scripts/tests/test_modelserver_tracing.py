"""Offline rendering checks for the opt-in vLLM tracing overlays.

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
GUIDE_VARIANTS = {
    "optimized-baseline": [
        "gpu/vllm/base", "gpu/vllm/gke",
        "amd/vllm/base", "amd/vllm/amd-ci",
        "cpu/vllm/base", "cpu/vllm/gke",
        "xpu/vllm/base", "npu/vllm/base", "metax/vllm/base",
        "tpu/v6/vllm/base", "tpu/v6/vllm/gke",
        "tpu/v7/vllm/base", "tpu/v7/vllm/gke",
    ],
    "precise-prefix-cache-routing": [
        "gpu/vllm/base", "gpu/vllm/gke",
        "amd/vllm/base", "cpu/vllm/base", "cpu/vllm/gke",
        "xpu/vllm/base",
        "tpu/v6/vllm/base", "tpu/v6/vllm/gke",
        "tpu/v7/vllm/base", "tpu/v7/vllm/gke",
    ],
}
TRACING_ARGS = [
    "--otlp-traces-endpoint=$(OTEL_EXPORTER_OTLP_ENDPOINT)",
    "--collect-detailed-traces=all",
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


@pytest.mark.parametrize(
    "guide,variant",
    [(guide, variant) for guide, variants in GUIDE_VARIANTS.items() for variant in variants],
)
def test_tracing_preserves_complete_provider_configuration(guide, variant):
    root = REPO_ROOT / "guides" / guide / "modelserver"
    baseline = build(root / variant)
    traced = build(root / "tracing" / variant)
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


def test_collector_and_sampling_can_be_overridden_without_replacing_args(tmp_path):
    overlay = REPO_ROOT / "guides/optimized-baseline/modelserver/tracing/gpu/vllm/base"
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
    write_overlay(tmp_path, [overlay], patches=[{
        "target": {"kind": "Deployment", "labelSelector": "llm-d.ai/role=decode"},
        "patch": yaml.safe_dump(patch),
    }])
    container = modelserver(build(tmp_path))
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == endpoint
    assert env["OTEL_TRACES_SAMPLER_ARG"] == "1.0"
    assert container["args"] == modelserver(build(overlay))["args"]


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
