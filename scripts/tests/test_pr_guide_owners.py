"""Tests for scripts/pr-guide-owners.py and .github/workflows/pr-guide-owners.yaml."""

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pr-guide-owners.yaml"
CI_GUIDES_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci-guides-check.yaml"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from github_api import ApiError  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "pr_guide_owners", SCRIPTS_DIR / "pr-guide-owners.py"
)
assert SPEC and SPEC.loader
pgo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pgo)

REPO = "llm-d/llm-d"


class FakeGitHubApi:
    """In-memory stub for scripts/github_api.api covering PR routing endpoints."""

    def __init__(
        self,
        *,
        pr: dict | None = None,
        files: list[dict] | None = None,
        reviews: list[dict] | None = None,
        events: list[dict] | None = None,
        collaborators: set[str] | None = None,
        reject_reviewers_422: set[str] | None = None,
        collaborator_error: tuple[int, str] | None = None,
    ):
        self.pr = pr or {
            "number": 42,
            "state": "open",
            "draft": False,
            "user": {"login": "contributor"},
            "requested_reviewers": [],
            "requested_teams": [],
        }
        self.files = list(files or [])
        self.reviews = list(reviews or [])
        self.events = list(events or [])
        self.collaborators = {login.lower() for login in (collaborators or set())}
        self.reject_reviewers_422 = {
            login.lower() for login in (reject_reviewers_422 or set())
        }
        self.collaborator_error = collaborator_error
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, path: str, token: str, payload: dict | None = None):
        self.calls.append((method, path, payload))
        base_path, _, query = path.partition("?")

        if method == "GET":
            if base_path == f"/repos/{REPO}/pulls/42":
                return self.pr
            if base_path == f"/repos/{REPO}/pulls/42/files":
                return self._paginate(self.files, query)
            if base_path == f"/repos/{REPO}/pulls/42/reviews":
                return self._paginate(self.reviews, query)
            if base_path == f"/repos/{REPO}/issues/42/events":
                return self._paginate(self.events, query)
            prefix = f"/repos/{REPO}/collaborators/"
            if base_path.startswith(prefix):
                if self.collaborator_error:
                    status, body = self.collaborator_error
                    raise ApiError(status, method, path, body)
                login = base_path[len(prefix) :].lower()
                if login in self.collaborators:
                    return {}
                raise ApiError(404, method, path, '{"message": "Not Found"}')

        if method == "POST" and base_path == f"/repos/{REPO}/pulls/42/requested_reviewers":
            reviewers = (payload or {}).get("reviewers") or []
            if any(login.lower() in self.reject_reviewers_422 for login in reviewers):
                raise ApiError(422, method, path, '{"message": "Review cannot be requested"}')
            return {"requested_reviewers": [{"login": r} for r in reviewers]}

        raise AssertionError(f"Unexpected API call: {method} {path} {payload}")

    @staticmethod
    def _paginate(items: list[dict], query: str) -> list[dict]:
        params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
        per_page = int(params.get("per_page", "100"))
        page = int(params.get("page", "1"))
        start = (page - 1) * per_page
        return items[start : start + per_page]

    def write_calls(self) -> list[tuple[str, str, dict | None]]:
        return [c for c in self.calls if c[0] != "GET"]


def _write_owners(repo_root: Path, rel_dir: str, approvers=(), reviewers=()) -> None:
    target = repo_root / rel_dir / "OWNERS"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"approvers": list(approvers), "reviewers": list(reviewers)}
    target.write_text(yaml.safe_dump(data), encoding="utf-8")


class OwnersResolutionTests(unittest.TestCase):
    def test_nearest_ancestor_resolution_across_depth_2_3_and_4(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_owners(root, "guides/alpha", approvers=["alice"])
            _write_owners(root, "guides/alpha/sub-guide", approvers=["sub-alice"])
            _write_owners(root, "guides/models/model-x", approvers=["model-owner"])
            _write_owners(root, "guides/batch/async", approvers=["batch-owner"])

            self.assertEqual(
                pgo.resolve_guide_owners_dir("guides/alpha/gaie-sim/values.yaml", root),
                "guides/alpha",
            )
            self.assertEqual(
                pgo.resolve_guide_owners_dir("guides/alpha/README.md", root),
                "guides/alpha",
            )
            self.assertEqual(
                pgo.resolve_guide_owners_dir("guides/alpha/sub-guide/README.md", root),
                "guides/alpha/sub-guide",
            )
            self.assertEqual(
                pgo.resolve_guide_owners_dir("guides/models/model-x/guide.yaml", root),
                "guides/models/model-x",
            )
            self.assertEqual(
                pgo.resolve_guide_owners_dir(
                    "guides/batch/async/nested/values.yaml", root
                ),
                "guides/batch/async",
            )

    def test_ignores_top_level_prereqs_traversal_and_never_uses_root_owners(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "OWNERS").write_text("approvers:\n- rootowner\n", encoding="utf-8")
            (root / "guides").mkdir()
            (root / "guides" / "OWNERS").write_text("approvers:\n- guidesroot\n", encoding="utf-8")
            _write_owners(root, "guides/alpha", approvers=["Alice"], reviewers=["alice", "Bob"])

            self.assertIsNone(pgo.resolve_guide_owners_dir("README.md", root))
            self.assertIsNone(pgo.resolve_guide_owners_dir("OWNERS", root))
            self.assertIsNone(pgo.resolve_guide_owners_dir("guides/README.md", root))
            self.assertIsNone(pgo.resolve_guide_owners_dir("guides/prereqs/gateway/README.md", root))
            self.assertIsNone(pgo.resolve_guide_owners_dir("guides/brand-new-guide/README.md", root))
            self.assertIsNone(pgo.resolve_guide_owners_dir("guides/../OWNERS", root))
            self.assertIsNone(pgo.resolve_guide_owners_dir("guides/alpha/../beta/README.md", root))

    def test_load_guide_owners_orders_approvers_first_and_dedupes_case_insensitively(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_owners(
                root,
                "guides/alpha",
                approvers=["Alice", "Carol", "bad login!"],
                reviewers=["alice", "Bob", "CAROL"],
            )
            self.assertEqual(
                pgo.load_guide_owners(root / "guides" / "alpha" / "OWNERS"),
                ["Alice", "Carol", "Bob"],
            )


class RoutingBehaviorTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        _write_owners(self.root, "guides/alpha", approvers=["alice"], reviewers=["ext-alpha"])
        _write_owners(self.root, "guides/beta", approvers=["bob"], reviewers=["ext-beta"])
        _write_owners(self.root, "guides/gamma", approvers=["carol"])

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run(self, fake: FakeGitHubApi, *, dry_run: bool = False) -> tuple[int, str]:
        buf = io.StringIO()
        with mock.patch.object(pgo, "api", side_effect=fake):
            with contextlib.redirect_stdout(buf):
                rc = pgo.route_pr(REPO, 42, "tok", self.root, dry_run=dry_run)
        return rc, buf.getvalue()

    def test_live_mode_posts_one_reviewer_each_skips_collaborators_get_and_excludes_author(self):
        fake = FakeGitHubApi(
            pr={
                "number": 42,
                "state": "open",
                "draft": False,
                "user": {"login": "Alice"},
                "requested_reviewers": [],
                "requested_teams": [],
            },
            files=[{"filename": "guides/alpha/README.md"}, {"filename": "guides/beta/guide.yaml"}],
            reject_reviewers_422={"ext-alpha", "ext-beta"},
        )
        rc, out = self._run(fake)
        self.assertEqual(rc, 0)
        self.assertFalse(any("/collaborators/" in p or "/comments" in p for _, p, _ in fake.calls))
        # Round-robin order: guides/alpha[0] (alice, excluded), guides/beta[0] (bob),
        # guides/alpha[1] (ext-alpha), guides/beta[1] (ext-beta).
        self.assertEqual(
            fake.write_calls(),
            [
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["bob"]}),
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["ext-alpha"]}),
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["ext-beta"]}),
            ],
        )
        self.assertIn("requested reviewers ['bob']", out)
        self.assertIn("skipped non-collaborator or unrequestable guide owners: ext-alpha, ext-beta", out)

    def test_422_does_not_consume_remaining_slots(self):
        _write_owners(self.root, "guides/slots", approvers=["ext-1", "collab-1", "collab-2"])
        fake = FakeGitHubApi(
            pr={
                "number": 42,
                "state": "open",
                "draft": False,
                "user": {"login": "contributor"},
                "requested_reviewers": [{"login": f"pre-{i}"} for i in range(14)],
                "requested_teams": [],
            },
            files=[{"filename": "guides/slots/README.md"}],
            reject_reviewers_422={"ext-1"},
        )
        rc, out = self._run(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(
            fake.write_calls(),
            [
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["ext-1"]}),
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["collab-1"]}),
            ],
        )
        self.assertIn("skipped non-collaborator or unrequestable guide owners: ext-1", out)
        self.assertIn("capped reviewer requests at 15 total (skipping collab-2)", out)

    def test_rename_previous_filename_routes_source_guide(self):
        fake = FakeGitHubApi(
            files=[{"filename": "docs/moved.md", "previous_filename": "guides/gamma/old.md"}],
        )
        rc, _ = self._run(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(
            fake.write_calls(),
            [("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["carol"]})],
        )

    def test_antispam_respects_requested_reviewed_and_timeline_events(self):
        _write_owners(
            self.root,
            "guides/delta",
            approvers=["already-req", "already-reviewed", "previously-requested", "fresh-owner"],
        )
        fake = FakeGitHubApi(
            pr={
                "number": 42,
                "state": "open",
                "draft": False,
                "user": {"login": "contributor"},
                "requested_reviewers": [{"login": "Already-Req"}],
                "requested_teams": [],
            },
            files=[{"filename": "guides/delta/README.md"}],
            reviews=[{"user": {"login": "already-reviewed"}}],
            events=[
                {"event": "review_requested", "requested_reviewer": {"login": "previously-requested"}},
                {"event": "review_requested", "requested_team": {"slug": "sig-docs"}},
            ],
        )
        rc, _ = self._run(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(
            fake.write_calls(),
            [("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["fresh-owner"]})],
        )

    def test_cross_guide_shared_owner_requested_once_case_insensitively(self):
        _write_owners(self.root, "guides/one", approvers=["Alice"])
        _write_owners(self.root, "guides/two", approvers=["alice"])
        fake = FakeGitHubApi(
            files=[{"filename": "guides/one/README.md"}, {"filename": "guides/two/README.md"}],
        )
        rc, _ = self._run(fake)
        self.assertEqual(rc, 0)
        self.assertEqual(
            fake.write_calls(),
            [("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["Alice"]})],
        )

    def test_round_robin_cap_interleaves_across_guides_and_counts_teams(self):
        _write_owners(self.root, "guides/g1", approvers=[f"g1-{i}" for i in range(10)])
        _write_owners(self.root, "guides/g2", approvers=[f"g2-{i}" for i in range(10)])
        fake = FakeGitHubApi(
            pr={
                "number": 42,
                "state": "open",
                "draft": False,
                "user": {"login": "contributor"},
                "requested_reviewers": [{"login": "pre-req"}],
                "requested_teams": [{"slug": "router-maintainers"}, {"slug": "sig-docs"}],
            },
            files=[{"filename": "guides/g1/README.md"}, {"filename": "guides/g2/README.md"}],
        )
        rc, out = self._run(fake)
        self.assertEqual(rc, 0)
        requested = [p["reviewers"][0] for _, _, p in fake.write_calls()]
        # 15 - 1 user - 2 teams = 12 slots -> 6 from g1 and 6 from g2 interleaved.
        self.assertEqual(
            requested,
            [
                "g1-0", "g2-0", "g1-1", "g2-1", "g1-2", "g2-2",
                "g1-3", "g2-3", "g1-4", "g2-4", "g1-5", "g2-5",
            ],
        )
        self.assertIn("capped reviewer requests at 15 total (skipping g1-6", out)

    def test_draft_and_closed_prs_make_zero_write_calls(self):
        for state, draft in [("open", True), ("closed", False)]:
            fake = FakeGitHubApi(
                pr={"number": 42, "state": state, "draft": draft, "user": {"login": "x"}},
                files=[{"filename": "guides/alpha/README.md"}],
            )
            rc, _ = self._run(fake)
            self.assertEqual(rc, 0)
            self.assertEqual(fake.write_calls(), [])
            self.assertEqual(len(fake.calls), 1)

    def test_mass_sweep_guard_allows_5_and_skips_6(self):
        files = []
        for idx in range(6):
            slug = f"guides/g{idx}"
            _write_owners(self.root, slug, approvers=[f"owner{idx}"])
            files.append({"filename": f"{slug}/README.md"})

        fake_five = FakeGitHubApi(files=files[:5])
        rc5, _ = self._run(fake_five)
        self.assertEqual(rc5, 0)
        self.assertEqual(
            [p["reviewers"][0] for _, _, p in fake_five.write_calls()],
            ["owner0", "owner1", "owner2", "owner3", "owner4"],
        )

        fake_six = FakeGitHubApi(files=files)
        rc6, _ = self._run(fake_six)
        self.assertEqual(rc6, 0)
        self.assertEqual(fake_six.write_calls(), [])

    def test_dry_run_checks_collaborators_and_makes_no_write_calls(self):
        fake = FakeGitHubApi(
            files=[{"filename": "guides/alpha/README.md"}],
            collaborators={"alice"},
        )
        rc, out = self._run(fake, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(fake.write_calls(), [])
        self.assertIn("would request reviewers ['alice'] (dry run)", out)
        self.assertIn("would skip non-collaborator guide owners: ext-alpha (dry run)", out)

    def test_api_list_paginates_across_multiple_pages(self):
        fake = FakeGitHubApi(
            files=[{"filename": f"docs/f{i}.md"} for i in range(105)],
        )
        with mock.patch.object(pgo, "api", side_effect=fake):
            items = pgo.api_list(f"/repos/{REPO}/pulls/42/files", "tok")
        self.assertEqual(len(items), 105)
        self.assertEqual(
            [p for _, p, _ in fake.calls],
            [
                f"/repos/{REPO}/pulls/42/files?per_page=100&page=1",
                f"/repos/{REPO}/pulls/42/files?per_page=100&page=2",
            ],
        )


class MainErrorPolicyTests(unittest.TestCase):
    def _run_main_with_error(self, exc: BaseException) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict("os.environ", {"GITHUB_TOKEN": "tok"}),
            mock.patch.object(sys, "argv", ["pr-guide-owners.py", "--pr", "42"]),
            mock.patch.object(pgo, "route_pr", side_effect=exc),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            rc = pgo.main()
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_fails_loudly_on_401_and_permission_403_even_if_path_mentions_rate_limit(self):
        for exc in [
            ApiError(401, "GET", "/repos/llm-d/llm-d/pulls/42", '{"message": "Bad credentials"}'),
            ApiError(
                403,
                "POST",
                "/repos/llm-d/rate limit/pulls/42/requested_reviewers",
                '{"message": "Resource not accessible by integration"}',
            ),
        ]:
            rc, stdout, stderr = self._run_main_with_error(exc)
            self.assertEqual(rc, 1)
            self.assertEqual(stdout, "")
            self.assertIn("error: guide-owner routing failed for PR #42", stderr)

    def test_warns_and_exits_0_on_rate_limits_transient_http_and_network_errors(self):
        for exc in [
            ApiError(
                403,
                "POST",
                "/repos/llm-d/llm-d/pulls/42/requested_reviewers",
                '{"message": "You have exceeded a secondary rate limit"}',
            ),
            ApiError(
                403,
                "GET",
                "/repos/llm-d/llm-d/pulls/42",
                '{"message": "You have triggered an abuse detection mechanism"}',
            ),
            ApiError(429, "GET", "/repos/llm-d/llm-d/pulls/42", '{"message": "Too Many Requests"}'),
            ApiError(502, "GET", "/repos/llm-d/llm-d/pulls/42/files", "Bad Gateway"),
            ApiError(404, "GET", "/repos/llm-d/llm-d/pulls/42", '{"message": "Not Found"}'),
            urllib.error.URLError("timed out"),
        ]:
            rc, stdout, stderr = self._run_main_with_error(exc)
            self.assertEqual(rc, 0, f"Expected exit 0 for {exc!r}")
            self.assertEqual(stderr, "")
            self.assertIn("::warning::guide-owner routing skipped for PR #42", stdout)


class RepoStaticInvariantsTests(unittest.TestCase):
    def test_all_repo_guide_owners_files_parse_cleanly(self):
        owners_files = sorted((REPO_ROOT / "guides").rglob("OWNERS"))
        self.assertTrue(owners_files)
        for owners_file in owners_files:
            raw = yaml.safe_load(owners_file.read_text(encoding="utf-8")) or {}
            self.assertIsInstance(raw, dict, f"{owners_file} must be a mapping")
            parsed = pgo.load_guide_owners(owners_file)
            self.assertTrue(parsed, f"{owners_file} has no valid GitHub logins")
            for role in ("approvers", "reviewers"):
                entries = raw.get(role)
                if entries is None:
                    continue
                self.assertIsInstance(entries, list, f"{owners_file} {role} must be a list")
                for login in entries:
                    self.assertRegex(
                        str(login).strip(),
                        pgo.GITHUB_LOGIN_RE,
                        f"Invalid GitHub login {login!r} in {owners_file}",
                    )

    def test_workflow_uses_safe_pull_request_target_checkout_pins_and_permissions(self):
        wf = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(wf["permissions"], {})
        # PyYAML parses bare `on:` as boolean True unless quoted.
        triggers = wf.get("on", wf.get(True))
        self.assertIn("pull_request_target", triggers)
        self.assertEqual(triggers["pull_request_target"]["paths"], ["guides/**"])

        job = wf["jobs"]["route-guide-owners"]
        self.assertEqual(
            job["permissions"],
            {"contents": "read", "issues": "read", "pull-requests": "write"},
        )
        checkout_step = job["steps"][0]
        self.assertEqual(
            checkout_step["with"]["ref"],
            "${{ github.event.pull_request.base.ref || github.ref_name }}",
        )
        self.assertFalse(checkout_step["with"]["persist-credentials"])
        self.assertIn("guides/**/OWNERS", checkout_step["with"]["sparse-checkout"])

        pip_step = job["steps"][2]
        self.assertIn("'pyyaml==6.*'", pip_step["run"])

        run_step = job["steps"][-1]
        self.assertNotIn("${{", run_step["run"])
        self.assertIn("PR_NUMBER", run_step["env"])

    def test_ci_guides_check_watches_workflow_and_script(self):
        ci_wf = yaml.safe_load(CI_GUIDES_WORKFLOW_PATH.read_text(encoding="utf-8"))
        triggers = ci_wf.get("on", ci_wf.get(True))
        watched = triggers["pull_request"]["paths"]
        self.assertIn(".github/workflows/pr-guide-owners.yaml", watched)
        self.assertIn("scripts/pr-guide-owners.py", watched)


if __name__ == "__main__":
    unittest.main()
