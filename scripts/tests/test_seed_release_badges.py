"""Tests for the "never run" placeholder badge seeder.

The invariants worth protecting here are the ones whose failure is silent:
a badge file name that disagrees with what the matrix points at, a placeholder
whose JSON shape drifts from what llm-d-infra writes, a matrix_type guard that
lets "nightly" through, and the create-if-absent property that stops a
placeholder from overwriting a real result.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parents[1]
# seed-release-badges.py imports matrix_common as a sibling; running the script
# directly puts scripts/ on sys.path, so a test loading it by path must too.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import matrix_common as mc  # noqa: E402

MODULE_PATH = SCRIPTS_DIR / "seed-release-badges.py"
SPEC = importlib.util.spec_from_file_location("seed_release_badges", MODULE_PATH)
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)


# ---------------------------------------------------------------------------
# badge_label extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ('      badge_label: "VLLM GPU"', "VLLM GPU"),
        ('      badge_label: "VLLM ROCM MORI WIDE-EP"', "VLLM ROCM MORI WIDE-EP"),
        ('      badge_label: "VLLM GPU Queue"', "VLLM GPU Queue"),
        ("      badge_label: VLLM GPU", "VLLM GPU"),
        ('      badge_label: "VLLM GPU"   ', "VLLM GPU"),
    ],
)
def test_extract_badge_label_handles_quotes_spaces_and_hyphens(tmp_path, line, expected):
    workflow = tmp_path / "nightly-e2e-x-ibm-acc-gpu-vllm-x.yaml"
    workflow.write_text(f"jobs:\n  update-badge:\n    with:\n      badge_name: x-ocp\n{line}\n")
    assert mc._extract_badge_label(workflow) == expected


def test_extract_badge_label_absent(tmp_path):
    workflow = tmp_path / "w.yaml"
    workflow.write_text("jobs:\n  update-badge:\n    with:\n      badge_name: x-ocp\n")
    assert mc._extract_badge_label(workflow) is None


def test_every_lane_in_the_matrix_has_a_label():
    labels = mc.matrix_badge_labels()
    assert labels, "no badges discovered"
    assert all(label.strip() for label in labels.values())


# ---------------------------------------------------------------------------
# Badge file naming — must agree with llm-d-infra and with the rendered table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "matrix_type, expected",
    [
        ("nightly", "flow-control-gke.json"),
        ("", "flow-control-gke.json"),
        ("release-0.10", "flow-control-gke_release-0.10.json"),
        ("release-0.9", "flow-control-gke_release-0.9.json"),
    ],
)
def test_badge_file_name_suffix_rule(matrix_type, expected):
    assert mc.badge_file_name("flow-control-gke", matrix_type) == expected


@pytest.mark.parametrize("matrix_type", ["nightly", "", "release-0.10"])
def test_badge_endpoint_ends_in_the_badge_file_name(matrix_type):
    """The endpoint the table renders and the file the seeder writes cannot diverge."""
    name = mc.badge_file_name("flow-control-gke", matrix_type)
    assert mc.badge_endpoint("flow-control-gke", matrix_type).endswith(f"/{name}")


def test_seeded_set_matches_the_rendered_release_matrix():
    """The seeder must cover exactly the cells sync-release-matrix.py renders.

    Anything else and the table still shows "resource not found" somewhere, or the
    seeder writes files nobody reads.
    """
    spec = importlib.util.spec_from_file_location(
        "sync_release_matrix", SCRIPTS_DIR / "sync-release-matrix.py"
    )
    assert spec and spec.loader
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)

    series = "release-0.10"
    section = sync.generate_section(mc.discover_workflows(), "v0.10.0", series)

    rendered = {
        part.split(".json")[0] + ".json"
        for part in section.split("/badges/")[1:]
    }
    seeded = {mc.badge_file_name(name, series) for name in mc.matrix_badge_labels()}
    assert seeded == rendered


# ---------------------------------------------------------------------------
# Placeholder payload
# ---------------------------------------------------------------------------


def test_placeholder_json_matches_reusable_update_badge_shape():
    """Golden copy of the heredoc in llm-d-infra reusable-update-badge.yaml.

    Same fields in the same order, so replacing a placeholder with a real result is
    a two-line diff rather than a rewrite.
    """
    assert seed.placeholder_json("VLLM GPU", "never run", "lightgrey") == (
        "{\n"
        '  "schemaVersion": 1,\n'
        '  "label": "VLLM GPU",\n'
        '  "message": "never run",\n'
        '  "color": "lightgrey"\n'
        "}\n"
    )


def test_placeholder_defaults_are_never_run_grey():
    assert seed.PLACEHOLDER_MESSAGE == "never run"
    assert seed.PLACEHOLDER_COLOR == "lightgrey"


# ---------------------------------------------------------------------------
# The matrix_type guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "matrix_type", ["nightly", "main", "", "release-0.99.1", "release-x.y", "Release-0.10"]
)
def test_main_refuses_a_non_release_matrix_type(monkeypatch, capsys, matrix_type):
    monkeypatch.setattr(
        sys, "argv", ["seed-release-badges.py", "--matrix-type", matrix_type]
    )
    monkeypatch.setattr(
        seed, "api", lambda *a, **k: pytest.fail("the guard must run before any API call")
    )
    assert seed.main() == 1
    assert "must look like" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Create-if-absent, and the retry that preserves it
# ---------------------------------------------------------------------------


class FakeApi:
    """Stands in for seed.api against a gh-pages branch holding `present`."""

    def __init__(self, present, fail_ref_updates=0, appears_on_retry=()):
        self.present = set(present)
        self.fail_ref_updates = fail_ref_updates
        self.appears_on_retry = list(appears_on_retry)
        self.head = "head0"
        self.trees_posted = []
        self.ref_updates = 0
        self.truncated = False

    def __call__(self, method, path, token, payload=None):
        if method == "GET" and "/git/ref/heads/" in path:
            return {"object": {"sha": self.head}}

        if method == "GET" and "/git/trees/" in path:
            return {
                "sha": f"tree-{self.head}",
                "truncated": self.truncated,
                "tree": [
                    {"path": f"badges/{name}", "type": "blob"} for name in sorted(self.present)
                ],
            }

        if method == "POST" and path.endswith("/git/trees"):
            self.trees_posted.append(
                {entry["path"].removeprefix("badges/") for entry in payload["tree"]}
            )
            assert payload["base_tree"] == f"tree-{self.head}"
            return {"sha": "newtree"}

        if method == "POST" and path.endswith("/git/commits"):
            assert payload["parents"] == [self.head]
            return {"sha": "newcommit"}

        if method == "PATCH" and "/git/refs/heads/" in path:
            self.ref_updates += 1
            if self.ref_updates <= self.fail_ref_updates:
                # A lane pushed its own badge while we were building the commit.
                self.present.update(self.appears_on_retry)
                self.head = f"head{self.ref_updates}"
                raise seed.ApiError(422, method, path, "Update is not a fast forward")
            return {}

        raise AssertionError(f"unexpected call: {method} {path}")


WANTED = {
    "a_release-0.10.json": seed.placeholder_json("A", "never run", "lightgrey"),
    "b_release-0.10.json": seed.placeholder_json("B", "never run", "lightgrey"),
    "c_release-0.10.json": seed.placeholder_json("C", "never run", "lightgrey"),
}


def test_seed_only_creates_the_absent_badges(monkeypatch):
    fake = FakeApi(present={"a_release-0.10.json"})
    monkeypatch.setattr(seed, "api", fake)

    created = seed.seed("o/r", "gh-pages", "t", WANTED, retries=3)

    assert created == ["b_release-0.10.json", "c_release-0.10.json"]
    assert fake.trees_posted == [{"b_release-0.10.json", "c_release-0.10.json"}]


def test_seed_is_a_no_op_when_every_badge_exists(monkeypatch):
    fake = FakeApi(present=set(WANTED))
    monkeypatch.setattr(seed, "api", fake)

    assert seed.seed("o/r", "gh-pages", "t", WANTED, retries=3) == []
    assert fake.trees_posted == []
    assert fake.ref_updates == 0


def test_retry_rereads_so_a_badge_written_meanwhile_is_not_overwritten(monkeypatch):
    """The point of retrying from a fresh read rather than replaying the commit."""
    monkeypatch.setattr(seed.time, "sleep", lambda _: None)
    fake = FakeApi(
        present={"a_release-0.10.json"},
        fail_ref_updates=1,
        appears_on_retry={"b_release-0.10.json"},
    )
    monkeypatch.setattr(seed, "api", fake)

    created = seed.seed("o/r", "gh-pages", "t", WANTED, retries=3)

    assert created == ["c_release-0.10.json"]
    assert fake.trees_posted == [
        {"b_release-0.10.json", "c_release-0.10.json"},
        {"c_release-0.10.json"},
    ]


def test_seed_gives_up_after_the_retry_budget(monkeypatch):
    monkeypatch.setattr(seed.time, "sleep", lambda _: None)
    fake = FakeApi(present=set(), fail_ref_updates=99)
    monkeypatch.setattr(seed, "api", fake)

    with pytest.raises(seed.ApiError) as excinfo:
        seed.seed("o/r", "gh-pages", "t", WANTED, retries=3)

    assert excinfo.value.status == 422
    assert fake.ref_updates == 3


def test_a_truncated_tree_is_refused_rather_than_guessed(monkeypatch):
    """A truncated listing makes present files look absent, i.e. overwritable."""
    fake = FakeApi(present=set(WANTED))
    fake.truncated = True
    monkeypatch.setattr(seed, "api", fake)

    with pytest.raises(RuntimeError, match="truncated"):
        seed.seed("o/r", "gh-pages", "t", WANTED, retries=1)


def test_dry_run_writes_nothing(monkeypatch, capsys):
    fake = FakeApi(present={mc.badge_file_name(n, "release-0.10") for n in list(mc.matrix_badge_labels())[:1]})
    monkeypatch.setattr(seed, "api", fake)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["seed-release-badges.py", "--matrix-type", "release-0.10", "--dry-run"]
    )

    assert seed.main() == 0
    assert fake.trees_posted == []
    assert fake.ref_updates == 0

    out = capsys.readouterr().out
    total = len(mc.matrix_badge_labels())
    assert f"would seed {total - 1} of {total}" in out


def test_main_requires_a_token(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["seed-release-badges.py", "--matrix-type", "release-0.10"]
    )
    assert seed.main() == 1
    assert "GITHUB_TOKEN" in capsys.readouterr().err
