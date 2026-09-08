"""Tests for scripts/guide.py — the emit subcommand and the flow-control
nightly contract. README staleness across converted guides is covered by
`guide.py render guides/*/ --check` (the other step of the same CI job), not
duplicated here.

Run from the repo root:

    python -m pytest scripts/tests/ -v
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDES_DIR = REPO_ROOT / "guides"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import guide  # noqa: E402


def _minimal(**overrides):
    """A minimal valid guide dict for emit_script unit tests."""
    data = {
        "name": "test-guide",
        "env": {
            "static": {
                "REPO_ROOT": "$(realpath $(git rev-parse --show-toplevel))",
                "NAMESPACE": "test-ns",
                "FLAVOR": {"default": "base", "values": ["base", "gke"]},
                "SECRET_TOKEN": {"default": "PLACEHOLDER", "sensitive": True},
            },
            "source": ["${REPO_ROOT}/guides/env.sh"],
        },
        "deploy": [{"run": "echo deploy"}],
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------
# env emission
# --------------------------------------------------------------------------


def test_env_default_emitted_verbatim():
    out = guide.emit_script(_minimal(), ["env"])
    # Command substitution in a default must survive unquoted so it runs at
    # execution time.
    assert "export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))" in out
    assert "export NAMESPACE=test-ns" in out
    assert "source ${REPO_ROOT}/guides/env.sh" in out


def test_env_override_is_shell_quoted():
    out = guide.emit_script(
        _minimal(), ["env"], {"NAMESPACE": "--set a.b=c with spaces"}
    )
    assert "export NAMESPACE='--set a.b=c with spaces'" in out


def test_sensitive_without_override_emits_no_placeholder():
    out = guide.emit_script(_minimal(), ["env"])
    assert "PLACEHOLDER" not in out
    assert "export SECRET_TOKEN" not in out
    assert "# SECRET_TOKEN is sensitive" in out


def test_sensitive_with_override_is_exported_quoted():
    out = guide.emit_script(_minimal(), ["env"], {"SECRET_TOKEN": "s3cr3t token"})
    assert "export SECRET_TOKEN='s3cr3t token'" in out
    assert "PLACEHOLDER" not in out


def test_unknown_var_override_is_an_error():
    with pytest.raises(guide.GuideError, match="NOT_DECLARED"):
        guide.emit_script(_minimal(), ["env"], {"NOT_DECLARED": "x"})


def test_override_outside_declared_values_is_an_error():
    # A typo'd override would otherwise when:-filter every gated step away
    # and emit an empty script with exit 0.
    with pytest.raises(guide.GuideError, match="gek"):
        guide.emit_script(_minimal(), ["env"], {"FLAVOR": "gek"})
    ok = guide.emit_script(_minimal(), ["env"], {"FLAVOR": "gke"})
    assert "export FLAVOR=gke" in ok


def test_yaml_null_and_bool_env_values_fail_validation():
    # YAML `VAR:` / `VAR: true` would emit `export VAR=None` / `export VAR=True`.
    data = _minimal()
    data["env"]["static"]["NULLED"] = None
    data["env"]["static"]["BOOLED"] = True
    data["env"]["static"]["BADDEFAULT"] = {"default": None}
    findings = guide.Guide.from_text(yaml_text=yaml.safe_dump(data)).check()
    msgs = "\n".join(str(f) for f in findings)
    assert "env.static.NULLED" in msgs
    assert "env.static.BOOLED" in msgs
    assert "env.static.BADDEFAULT" in msgs


def test_non_bool_sensitive_flag_fails_validation():
    # `sensitive: "true"` passed validation as non-sensitive but was treated
    # as sensitive by render and emit — the export silently vanished.
    data = _minimal()
    data["env"]["static"]["TOKEN"] = {"default": "x", "sensitive": "true"}
    findings = guide.Guide.from_text(yaml_text=yaml.safe_dump(data)).check()
    assert any("TOKEN" in str(f) and "boolean" in str(f) for f in findings)


def test_invalid_env_var_name_fails_validation():
    data = _minimal()
    data["env"]["static"]["BAD-NAME"] = "value"
    findings = guide.Guide.from_text(yaml_text=yaml.safe_dump(data)).check()
    assert any("BAD-NAME" in str(f) and "identifier" in str(f) for f in findings)

    data["env"]["static"].pop("BAD-NAME")
    data["env"]["static"]["lowercase_is_valid"] = "value"
    findings = guide.Guide.from_text(yaml_text=yaml.safe_dump(data)).check()
    assert findings.ok()


def test_provenance_records_var_names_not_values():
    out = guide.emit_script(_minimal(), ["env"], {"SECRET_TOKEN": "hunter2"})
    header = out.split("# === ")[0]
    assert "SECRET_TOKEN" in header
    assert "hunter2" not in header


# --------------------------------------------------------------------------
# step filtering
# --------------------------------------------------------------------------


def test_skip_in_filters_by_context():
    data = _minimal(
        deploy=[
            {"run": "echo always"},
            {"run": "echo not-in-ci", "skip_in": ["ci"]},
        ]
    )
    everywhere = guide.emit_script(data, ["deploy"])
    assert "echo always" in everywhere and "echo not-in-ci" in everywhere
    ci = guide.emit_script(data, ["deploy"], contexts={"ci"})
    assert "echo always" in ci and "echo not-in-ci" not in ci


def test_when_filter_uses_default_and_override():
    data = _minimal(
        deploy=[
            {"run": "echo base-only", "when": {"FLAVOR": ["base"]}},
            {"run": "echo gke-only", "when": {"FLAVOR": ["gke"]}},
        ]
    )
    by_default = guide.emit_script(data, ["deploy"])
    assert "echo base-only" in by_default and "echo gke-only" not in by_default
    overridden = guide.emit_script(data, ["deploy"], {"FLAVOR": "gke"})
    assert "echo gke-only" in overridden and "echo base-only" not in overridden


def test_when_on_sensitive_var_without_override_is_an_error():
    # Branch selection must never key off the README placeholder default.
    data = _minimal(
        deploy=[{"run": "echo gated", "when": {"SECRET_TOKEN": ["real"]}}]
    )
    with pytest.raises(guide.GuideError, match="SECRET_TOKEN"):
        guide.emit_script(data, ["deploy"])
    out = guide.emit_script(data, ["deploy"], {"SECRET_TOKEN": "real"})
    assert "echo gated" in out


def test_fully_filtered_section_is_marked_not_silent():
    data = _minimal(deploy=[{"run": "echo skipped", "skip_in": ["ci"]}])
    out = guide.emit_script(data, ["deploy"], contexts={"ci"})
    assert "echo skipped" not in out
    assert "# (no steps after skip_in/when filtering)" in out


def test_steps_join_with_blank_lines_and_no_when_comment():
    data = _minimal(deploy=[{"run": "echo one"}, {"run": "echo two"}])
    out = guide.emit_script(data, ["deploy"])
    assert "echo one\n\necho two" in out
    assert "# only when" not in out


# --------------------------------------------------------------------------
# sections and script shape
# --------------------------------------------------------------------------


def test_sections_emitted_in_cli_order():
    data = _minimal(
        deploy={"a": [{"run": "echo a"}], "b": [{"run": "echo b"}]},
        cleanup=[{"run": "echo clean"}],
    )
    out = guide.emit_script(data, ["cleanup", "deploy.b", "deploy.a"])
    assert out.index("=== cleanup ===") < out.index("=== deploy.b ===") < out.index(
        "=== deploy.a ==="
    )
    assert out.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in out


def test_unknown_section_is_an_error():
    with pytest.raises(guide.GuideError, match="no-such-section"):
        guide.emit_script(_minimal(), ["no-such-section"])


def test_emitting_parent_section_concatenates_all_subgroups():
    # Documented footgun (guides/templates/README.md): only `when:` encodes
    # exclusivity, so emitting a parent concatenates every named sub-group.
    # Locks the behavior the doc warns about — automation must pass specific
    # sub-paths for mutually exclusive modes.
    data = _minimal(
        deploy={
            "standalone": [{"run": "echo standalone"}],
            "gateway": [{"run": "echo gateway"}],
        }
    )
    out = guide.emit_script(data, ["deploy"])
    assert "echo standalone" in out
    assert "echo gateway" in out


def test_cli_refuses_invalid_yaml(tmp_path, capsys):
    bad_yaml = "name: broken\nenv: {static: {}}\n"  # missing required deploy
    bad = tmp_path / "guide.yaml"
    bad.write_text(bad_yaml)
    rc = guide.main(["emit", str(bad), "env"])
    assert rc == 1
    assert "1 error(s)" in capsys.readouterr().err
    # The specific finding, asserted via the library (Findings.report writes to
    # a stream bound at import time, which pytest capture fixtures can't see).
    findings = guide.Guide.from_text(yaml_text=bad_yaml).check()
    assert any("missing required key 'deploy'" in str(f) for f in findings)


def test_cli_emits_to_stdout(tmp_path, capsys):
    ok = tmp_path / "guide.yaml"
    ok.write_text(
        "name: ok\n"
        "env:\n  static:\n    NAMESPACE: ns\n"
        "deploy:\n  - run: echo hi\n"
    )
    rc = guide.main(["emit", str(tmp_path), "env", "deploy", "--var", "NAMESPACE=other"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "export NAMESPACE=other" in out
    assert "echo hi" in out


def test_cli_rejects_malformed_var(tmp_path, capsys):
    ok = tmp_path / "guide.yaml"
    ok.write_text("name: ok\nenv:\n  static:\n    A: b\ndeploy:\n  - run: echo hi\n")
    rc = guide.main(["emit", str(tmp_path), "env", "--var", "NOEQUALS"])
    assert rc == 2
    assert "--var must be NAME=VALUE" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["check", "render"])
def test_cli_selection_flags_still_guarded(command, tmp_path, capsys):
    # main() applies the target-selection guard only to namespaces carrying
    # the _needs_selection default set by add_common (emit has no --yaml/--md
    # and must skip it). Both halves of the guard must hold for both commands.
    rc = guide.main([command])
    assert rc == 2
    assert "nothing to do" in capsys.readouterr().err

    rc = guide.main([command, str(tmp_path), "--yaml", str(tmp_path / "guide.yaml")])
    assert rc == 2
    assert "not both" in capsys.readouterr().err


# --------------------------------------------------------------------------
# flow-control nightly contract — what nightly-deploy-gke.sh relies on
# --------------------------------------------------------------------------

NIGHTLY_SCRIPT = GUIDES_DIR / "flow-control" / "scripts" / "nightly-deploy-gke.sh"

NIGHTLY_VARS = {
    "NAMESPACE": "llm-d-nightly-flow-control-gke-gpu",
    "INFRA_PROVIDER": "gke",
    "ROUTER_VALUES": "/tmp/flow-control.ci.values.yaml",
    "EXTRA_HELM_ARGS": "--set router.monitoring.prometheus.auth.enabled=false",
}


@pytest.fixture(scope="module")
def flow_control():
    return guide.Guide.load(GUIDES_DIR / "flow-control")


def test_nightly_crds_use_env_sh_release_urls(flow_control):
    out = flow_control.emit(
        ["env", "prerequisites.crds"], variables=NIGHTLY_VARS, contexts=["ci"]
    )
    # env.sh computes GAIE_URL/ROUTER_RELEASE_URL so that `latest` resolves to
    # the releases/latest/download path shape; hand-built
    # releases/download/${VERSION} URLs 404 on `latest` (issue #2341).
    assert "gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml" in out
    assert "llm-d-router/${ROUTER_RELEASE_URL}/manifests.yaml" in out
    assert "releases/download/${GAIE_VERSION}" not in out


def test_nightly_deploy_carries_ci_overrides(flow_control):
    out = flow_control.emit(
        ["env", "deploy.standalone"], variables=NIGHTLY_VARS, contexts=["ci"]
    )
    assert "export ROUTER_VALUES=/tmp/flow-control.ci.values.yaml" in out
    assert (
        "export EXTRA_HELM_ARGS='--set router.monitoring.prometheus.auth.enabled=false'"
        in out
    )
    # The helm step consumes both hooks.
    assert "-f ${ROUTER_VALUES}" in out
    assert "${EXTRA_HELM_ARGS}" in out


def test_nightly_ci_context_drops_clone_and_secrets(flow_control):
    out = flow_control.emit(
        ["env", "prerequisites"], variables=NIGHTLY_VARS, contexts=["ci"]
    )
    assert "git clone" not in out
    assert "llm-d-hf-token" not in out
    # Non-skipped prerequisites still present.
    assert "kubectl create namespace" in out


def test_nightly_wrapper_vars_match_this_contract(flow_control):
    # The wrapper's emit() --var set and NIGHTLY_VARS above must not drift
    # apart, or these contract tests keep passing against a stale contract.
    # Every name must also be a declared env.static hook.
    names = set(re.findall(r"--var ([A-Z_]+)=", NIGHTLY_SCRIPT.read_text()))
    assert names == set(NIGHTLY_VARS)
    declared = flow_control.data["env"]["static"]
    for name in names:
        assert name in declared, f"{name} is not declared in guide.yaml env.static"


def test_nightly_modelserver_mirror_matches_guide(flow_control):
    # deploy.modelserver is the one step the wrapper hand-mirrors instead of
    # emitting: its CI-only replica sed cannot interpose in the emitted
    # kustomize | sed | apply pipe. Pin the emitted pipeline here so a
    # guide.yaml change to it fails CI and prompts the wrapper update — see
    # guides/flow-control/scripts/nightly-deploy-gke.sh.
    out = flow_control.emit(
        ["deploy.modelserver"], variables=NIGHTLY_VARS, contexts=["ci"]
    )
    assert (
        "kubectl kustomize ${REPO_ROOT}/guides/optimized-baseline"
        "/modelserver/gpu/vllm/${INFRA_PROVIDER}/" in out
    )
    assert '| sed "s/optimized-baseline/${GUIDE_NAME}/g"' in out
    assert "| kubectl apply -n ${NAMESPACE} -f -" in out
