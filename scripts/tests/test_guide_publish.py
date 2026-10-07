"""Tests for the llm-d.ai publishing additions to scripts/guide.py: the
``support:`` accelerator x model-server matrix (validation, repo cross-checks,
variant rendering, support table), ``set-branch`` and ``check-manifest``.

Run from the repo root:

    python -m pytest scripts/tests/ -v
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import guide  # noqa: E402


def _guide(**overrides):
    """A minimal valid guide with a support matrix."""
    data = {
        "name": "sup-guide",
        "env": {
            "static": {
                "NAMESPACE": "ns",
                "ACCELERATOR_TYPE": {"default": "gpu", "values": ["gpu", "tpu", "hpu"]},
                "MODEL_SERVER": {"default": "vllm", "values": ["vllm", "sglang"]},
                "MODEL": {"default": "m/one"},
            }
        },
        "support": {
            "engines": {"vllm": "vLLM", "sglang": "SGLang"},
            "accelerators": {
                "gpu": {"label": "NVIDIA GPU", "engines": {"vllm": "validated", "sglang": "community"}},
                "tpu": {"label": "TPU", "model": "m/two", "engines": {"vllm": "community"}},
                "hpu": {
                    "label": "Gaudi",
                    "engines": {"vllm": {"status": "unsupported", "issue": "https://x/1"}},
                },
            },
        },
        "deploy": {
            "modelserver": [
                {"run": "echo common"},
                {"when": {"ACCELERATOR_TYPE": ["gpu"]}, "run": "echo gpu"},
                {"when": {"ACCELERATOR_TYPE": ["tpu"]}, "run": "echo tpu"},
                {"run": "echo after"},
            ]
        },
    }
    data.update(overrides)
    return data


def _errors(findings):
    return [str(f) for f in findings]


# --------------------------------------------------------------------------
# support: schema
# --------------------------------------------------------------------------


def test_valid_support_matrix_passes():
    assert guide.check_yaml(_guide()).ok()


def test_unsupported_needs_issue():
    g = _guide()
    g["support"]["accelerators"]["hpu"]["engines"]["vllm"] = {"status": "unsupported"}
    assert any("tracking `issue:`" in e for e in _errors(guide.check_yaml(g)))


def test_unknown_status_and_engine_rejected():
    g = _guide()
    g["support"]["accelerators"]["gpu"]["engines"] = {"vllm": "maybe", "trtllm": "validated"}
    errs = _errors(guide.check_yaml(g))
    assert any("status must be one of" in e for e in errs)
    assert any("engines.trtllm" in e for e in errs)


def test_accelerator_vocabulary_must_match_values():
    g = _guide()
    del g["support"]["accelerators"]["tpu"]
    g["support"]["accelerators"]["npu"] = {"label": "NPU", "engines": {"vllm": "community"}}
    errs = _errors(guide.check_yaml(g))
    assert any("missing entry for ACCELERATOR_TYPE value 'tpu'" in e for e in errs)
    assert any("support.accelerators.npu: not in" in e for e in errs)


def test_default_pair_must_be_supported():
    g = _guide()
    g["env"]["static"]["MODEL_SERVER"]["default"] = "sglang"
    g["env"]["static"]["ACCELERATOR_TYPE"]["default"] = "tpu"
    assert any("default pairing tpu/sglang" in e for e in _errors(guide.check_yaml(g)))


def test_when_targeting_unsupported_accelerator_rejected():
    g = _guide()
    g["deploy"]["modelserver"].append({"when": {"ACCELERATOR_TYPE": ["hpu"]}, "run": "echo hpu"})
    assert any("no supported engine: hpu" in e for e in _errors(guide.check_yaml(g)))


def test_when_targeting_unsupported_pair_rejected():
    g = _guide()
    g["deploy"]["modelserver"].append(
        {"when": {"ACCELERATOR_TYPE": ["tpu"], "MODEL_SERVER": ["sglang"]}, "run": "echo x"}
    )
    assert any("unsupported pairing(s) tpu/sglang" in e for e in _errors(guide.check_yaml(g)))


# --------------------------------------------------------------------------
# support: repo cross-checks
# --------------------------------------------------------------------------


def _repo(tmp_path, overlays, workflows=()):
    (tmp_path / ".git").mkdir()
    gdir = tmp_path / "guides" / "sup-guide"
    for o in overlays:
        (gdir / "modelserver" / o).mkdir(parents=True)
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name, accel in workflows:
        (wf / name).write_text(
            f"with:\n  accelerator_type: ${{{{ inputs.accelerator_type || '{accel}' }}}}\n"
        )
    return gdir


def test_repo_checks_pass_when_consistent(tmp_path):
    gdir = _repo(
        tmp_path,
        ["gpu/vllm", "gpu/sglang", "tpu/vllm"],
        [("nightly-e2e-sup-guide-gke-acc-gpu-vllm-x.yaml", "gpu")],
    )
    assert guide.check_support_repo(_guide(), gdir).ok()


def test_repo_checks_nonstandard_workflow_name(tmp_path):
    # Names outside nightly-e2e-<guide>-<prov>-acc-<acc>-<engine>-x.yaml count
    # through their accelerator_type / backend_type input defaults.
    gdir = _repo(tmp_path, ["gpu/vllm", "gpu/sglang", "tpu/vllm"])
    wf = tmp_path / ".github" / "workflows"

    def write(name, accel, engine):
        (wf / name).write_text(
            f"with:\n  accelerator_type: ${{{{ inputs.accelerator_type || '{accel}' }}}}\n"
            f"  backend_type: ${{{{ inputs.backend_type || '{engine}' }}}}\n"
        )

    write("nightly-e2e-sup-guide-gke-cpu-gpu-vllm-native.yaml", "gpu", "vllm")
    write("nightly-e2e-other-guide-gke-cpu-tpu-vllm-native.yaml", "tpu", "vllm")
    assert guide.check_support_repo(_guide(), gdir).ok()

    write("nightly-e2e-sup-guide-gke-cpu-tpu-vllm-native.yaml", "tpu", "vllm")
    errs = _errors(guide.check_support_repo(_guide(), gdir))
    assert any("runs tpu/vllm nightly" in e for e in errs)


def test_tiered_prefix_cache_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "tiered-prefix-cache")
    assert g.check().ok(), _errors(g.check())


def test_repo_checks_missing_overlay_and_stray_overlay(tmp_path):
    gdir = _repo(
        tmp_path,
        ["gpu/vllm", "tpu/vllm", "tpu/sglang"],
        [("nightly-e2e-sup-guide-gke-acc-gpu-vllm-x.yaml", "gpu")],
    )
    errs = _errors(guide.check_support_repo(_guide(), gdir))
    assert any("no overlay at modelserver/gpu/sglang/" in e for e in errs)
    assert any("overlay modelserver/tpu/sglang/ exists" in e for e in errs)


def test_repo_checks_nightly_must_match_validated(tmp_path):
    gdir = _repo(
        tmp_path,
        ["gpu/vllm", "gpu/sglang", "tpu/vllm"],
        [
            ("nightly-e2e-sup-guide-gke-acc-tpu-vllm-x.yaml", "tpu"),
            ("nightly-e2e-sup-guide-gke-acc-gpu-trtllm-x.yaml", "gpu"),
        ],
    )
    errs = _errors(guide.check_support_repo(_guide(), gdir))
    assert any("runs tpu/vllm nightly but support marks it community" in e for e in errs)
    assert any("gpu.engines.vllm: marked validated but no nightly" in e for e in errs)
    assert any("targets engine 'trtllm' which is not in" in e for e in errs)


def test_repo_checks_hyphenated_provider(tmp_path):
    gdir = _repo(
        tmp_path,
        ["gpu/vllm", "gpu/sglang", "tpu/vllm"],
        [("nightly-e2e-sup-guide-amd-ci-acc-gpu-vllm-x.yaml", "gpu")],
    )
    assert guide.check_support_repo(_guide(), gdir).ok()


def test_optimized_baseline_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "optimized-baseline")
    assert g.check().ok(), _errors(g.check())


def test_precise_prefix_cache_routing_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "precise-prefix-cache-routing")
    assert g.check().ok(), _errors(g.check())


def test_pd_disaggregation_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "pd-disaggregation")
    assert g.check().ok(), _errors(g.check())


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def test_variant_group_rendering():
    out = guide.render_steps(_guide()["deploy"]["modelserver"], _guide())
    assert "<!-- variants:start -->" in out and "<!-- variants:end -->" in out
    assert '<details open data-when="ACCELERATOR_TYPE=gpu">' in out
    assert '<details data-when="ACCELERATOR_TYPE=tpu">' in out
    assert "<summary><b>TPU</b></summary>" in out
    # Only the non-default variant is hidden from the README-scraping runner.
    gpu_part, tpu_part = out.split('data-when="ACCELERATOR_TYPE=tpu"')
    assert guide.CICD_SKIP_START not in gpu_part
    assert guide.CICD_SKIP_START in tpu_part
    # A blank line ends the HTML block so the trailing fence still renders.
    assert "<!-- variants:end -->\n\n```bash\necho after\n```" in out
    assert "# only when" not in out


def test_single_variant_step_renders_bare_fence():
    g = _guide()
    steps = [s for s in g["deploy"]["modelserver"] if "when" in s]
    default = next(s for s in steps if guide._when_matches_defaults(s["when"], g))
    other = next(s for s in steps if not guide._when_matches_defaults(s["when"], g))
    out = guide.render_steps(default, g)
    assert "variants:start" not in out and "<details" not in out
    assert "# only when" not in out and guide.CICD_SKIP_START not in out
    out = guide.render_steps(other, g)
    assert "variants:start" not in out and "<details" not in out
    assert out.startswith(guide.CICD_SKIP_START)


def test_guides_without_support_render_unchanged():
    g = _guide()
    del g["support"]
    out = guide.render_steps(g["deploy"]["modelserver"], g)
    assert "variants:start" not in out
    assert "# only when ACCELERATOR_TYPE=gpu:" in out


def test_support_table():
    out = guide.render_support_table(_guide())
    lines = out.splitlines()
    assert lines[0] == "| Accelerator | `ACCELERATOR_TYPE` | Served model | vLLM | SGLang |"
    assert "| NVIDIA GPU | `gpu` | — | ✅ validated | 🟡 community |" in lines
    assert "| TPU | `tpu` | `m/two` | 🟡 community | — |" in lines
    assert "| Gaudi | `hpu` | — | ❌ [not supported](https://x/1) | — |" in lines


def test_support_table_notes_column():
    g = _guide()
    g["support"]["accelerators"]["tpu"]["notes"] = "GKE TPU, see [below](#tpu)"
    lines = guide.render_support_table(g).splitlines()
    assert lines[0].endswith("| SGLang | Notes |")
    assert "| TPU | `tpu` | `m/two` | 🟡 community | — | GKE TPU, see [below](#tpu) |" in lines
    assert "| NVIDIA GPU | `gpu` | — | ✅ validated | 🟡 community |  |" in lines
    assert guide.check_yaml(g).ok()


def test_support_notes_must_be_one_line_without_pipes():
    g = _guide()
    g["support"]["accelerators"]["tpu"]["notes"] = "a | b"
    assert any(".notes: must be a single line" in e for e in _errors(guide.check_yaml(g)))

def test_support_marker_renders_into_readme():
    md = "# T\n\n<!-- guide:support start -->\n<!-- guide:support end -->\n"
    g = guide.Guide.from_text(yaml.safe_dump(_guide(), sort_keys=False), md)
    assert g.check().ok(), _errors(g.check())
    assert "| NVIDIA GPU |" in g.render()


# --------------------------------------------------------------------------
# set-branch
# --------------------------------------------------------------------------


def test_set_branch_text():
    text = (
        "env:\n  static:\n    BRANCH: main\n    BENCHMARK_REF: main\n"
        "prerequisites:\n  clone:\n    - run: |\n        export BRANCH=main\n"
    )
    out = guide.set_branch_text(text, "release-0.8")
    assert "    BRANCH: release-0.8\n" in out
    assert "export BRANCH=release-0.8" in out
    assert "BENCHMARK_REF: main" in out


def test_set_branch_rejects_bad_ref():
    with pytest.raises(guide.GuideError):
        guide.set_branch_text("BRANCH: main", "main; rm -rf /")


def test_set_branch_cli_rerenders(tmp_path):
    gdir = tmp_path / "g"
    gdir.mkdir()
    (gdir / "guide.yaml").write_text(
        "name: g\nenv:\n  static:\n    BRANCH: main\ndeploy:\n  - run: echo ${BRANCH}\n"
    )
    (gdir / "README.md").write_text("<!-- guide:env.static start -->\n<!-- guide:env.static end -->\n")
    assert guide.main(["set-branch", "release-1.0", str(gdir)]) == 0
    assert "export BRANCH=release-1.0" in (gdir / "README.md").read_text()


# --------------------------------------------------------------------------
# check-manifest
# --------------------------------------------------------------------------


def _manifest_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    g = tmp_path / "guides" / "a"
    (g / "bench").mkdir(parents=True)
    (g / "guide.yaml").write_text("name: a\n")
    (g / "README.md").write_text("# A\n")
    (g / "bench" / "README.md").write_text("# B\n")
    return tmp_path


def _manifest(**entry):
    e = {"dir": "guides/a", "slug": "a", "title": "A", "position": 1,
         "pages": [{"from": "bench/README.md", "to": "bench", "title": "B"}]}
    e.update(entry)
    return {"version": 1, "sections": {"foundations": {"target": "well-lit-paths/foundations", "guides": [e]}}}


def test_manifest_ok(tmp_path):
    assert guide.check_manifest(_manifest(), _manifest_repo(tmp_path)).ok()


def test_manifest_errors(tmp_path):
    root = _manifest_repo(tmp_path)
    m = _manifest(dir="guides/missing", slug="Bad Slug", title="")
    m["sections"]["foundations"]["guides"][0]["pages"][0]["from"] = "nope.md"
    errs = _errors(guide.check_manifest(m, root))
    assert any("guides/missing/guide.yaml does not exist" in e for e in errs)
    assert any(".slug:" in e for e in errs)
    assert any(".title: required" in e for e in errs)
    assert any("nope.md does not exist" in e for e in errs)


def test_manifest_duplicate_slug(tmp_path):
    root = _manifest_repo(tmp_path)
    m = _manifest()
    entry = dict(m["sections"]["foundations"]["guides"][0], dir="guides/a")
    m["sections"]["foundations"]["guides"].append(entry)
    errs = _errors(guide.check_manifest(m, root))
    assert any("duplicate slug 'a'" in e for e in errs)
    assert any("already published" in e for e in errs)


def test_repo_manifest_is_valid():
    path = REPO_ROOT / guide.DEFAULT_MANIFEST
    data = yaml.safe_load(path.read_text())
    assert guide.check_manifest(data, REPO_ROOT).ok()


def test_p2p_kv_cache_sharing_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "p2p-kv-cache-sharing")
    assert g.check().ok(), _errors(g.check())


def test_predicted_latency_routing_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "predicted-latency-routing")
    assert g.check().ok(), _errors(g.check())


def test_multimodal_serving_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "multimodal-serving")
    assert g.check().ok(), _errors(g.check())
    # The guide has no top-level modelserver/, so check() cannot see the
    # overlays: assert every TOPOLOGY / accelerator / provider the README
    # documents resolves to an overlay (the paths deploy.topology exports).
    root = REPO_ROOT / "guides" / "multimodal-serving"
    agg = root / "aggregation" / "modelserver"
    edisagg = root / "e-disaggregation" / "modelserver"
    overlays = [agg / "gpu" / "vllm" / p for p in ("base", "gke")]
    overlays += [agg / "xpu" / "vllm" / "base", agg / "tpu" / "v7" / "vllm" / "gke"]
    overlays += [
        edisagg / "gpu" / "vllm" / t / p
        for t in ("e-pd", "e-p-d")
        for p in ("base", "gke", "coreweave")
    ]
    missing = [str(o) for o in overlays if not (o / "kustomization.yaml").is_file()]
    assert not missing, missing


def test_omni_serving_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "omni-serving")
    assert g.check().ok(), _errors(g.check())


def test_wide_ep_matrix_matches_repo():
    g = guide.Guide.load(REPO_ROOT / "guides" / "wide-ep")
    assert g.check().ok(), _errors(g.check())
    # Every INFRA_PROVIDER the support table documents per accelerator resolves
    # to an overlay, and every accelerator has the router/<ACCELERATOR>.values.yaml
    # that deploy.router_values layers on router/wide-ep.values.yaml.
    root = REPO_ROOT / "guides" / "wide-ep"
    providers = {"gpu": ("base", "gke", "coreweave"), "amd": ("base", "amd-ci"), "xpu": ("base",)}
    overlays = [root / "modelserver" / a / "vllm" / p for a, ps in providers.items() for p in ps]
    missing = [str(o) for o in overlays if not (o / "kustomization.yaml").is_file()]
    missing += [str(root / "router" / f"{a}.values.yaml") for a in providers if not (root / "router" / f"{a}.values.yaml").is_file()]
    assert not missing, missing


def test_env_comment_renders_inline():
    lines = guide._env_static_lines(
        {
            "MODEL": {"default": "m", "comment": "set me"},
            "ENGINE": {"default": "a", "values": ["a", "b"], "comment": "pick one"},
        }
    )
    assert lines[0][2] == "export MODEL=m # set me"
    assert lines[1][2] == "export ENGINE=a # options: a, b; pick one"


# --------------------------------------------------------------------------
# check-manifest: Operations sub-category sections
# --------------------------------------------------------------------------


def _ops_manifest(**entry):
    m = _manifest(**entry)
    m["sections"] = {"operations-scaling": {"target": "operations/scaling",
                                            "guides": m["sections"]["foundations"]["guides"]}}
    return m


def test_manifest_operations_subsection_readme_only(tmp_path):
    root = _manifest_repo(tmp_path)
    (root / "guides" / "a" / "guide.yaml").unlink()
    assert guide.check_manifest(_ops_manifest(), root).ok()
    # Foundations guides still need guide.yaml.
    errs = _errors(guide.check_manifest(_manifest(), root))
    assert any("guides/a/guide.yaml does not exist" in e for e in errs)


def test_manifest_section_names(tmp_path):
    root = _manifest_repo(tmp_path)
    m = _ops_manifest()
    m["sections"]["ops-misc"] = m["sections"].pop("operations-scaling")
    assert any("unknown section" in e for e in _errors(guide.check_manifest(m, root)))


def test_manifest_slug_unique_across_sections(tmp_path):
    root = _manifest_repo(tmp_path)
    (root / "guides" / "b").mkdir()
    (root / "guides" / "b" / "README.md").write_text("# B\n")
    m = _ops_manifest()
    m["sections"]["operations-traffic"] = {
        "target": "operations/traffic",
        "guides": [{"dir": "guides/b", "slug": "a", "title": "B", "position": 1}],
    }
    errs = _errors(guide.check_manifest(m, root))
    assert any("unique across sections" in e for e in errs)
