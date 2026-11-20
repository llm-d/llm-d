"""Tests for scripts/pr-guide-owners.py and .github/workflows/pr-guide-owners.yaml."""

import importlib.util
import sys
import tempfile
import unittest
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
        comments: list[dict] | None = None,
        collaborators: set[str] | None = None,
        reject_reviewers_422: set[str] | None = None,
        collaborator_error: tuple[int, str] | None = None,
        cap_422_on_request: bool = False,
    ):
        self.pr = pr or {
            "number": 42,
            "state": "open",
            "draft": False,
            "user": {"login": "contributor"},
            "requested_reviewers": [],
        }
        self.files = list(files or [])
        self.reviews = list(reviews or [])
        self.events = list(events or [])
        self.comments = list(comments or [])
        self.collaborators = {login.lower() for login in (collaborators or set())}
        self.reject_reviewers_422 = {
            login.lower() for login in (reject_reviewers_422 or set())
        }
        self.collaborator_error = collaborator_error
        self.cap_422_on_request = cap_422_on_request
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
            if base_path == f"/repos/{REPO}/issues/42/comments":
                return self._paginate(self.comments, query)
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
            if self.cap_422_on_request:
                raise ApiError(
                    422,
                    method,
                    path,
                    '{"message": "Cannot request more than 15 reviewers"}',
                )
            reviewers = (payload or {}).get("reviewers") or []
            if any(login.lower() in self.reject_reviewers_422 for login in reviewers):
                raise ApiError(422, method, path, '{"message": "Unprocessable Entity"}')
            return {"requested_reviewers": [{"login": r} for r in reviewers]}

        if method == "POST" and base_path == f"/repos/{REPO}/issues/42/comments":
            comment = {
                "id": 900 + len(self.comments),
                "body": (payload or {})["body"],
                "user": {"login": "github-actions[bot]", "type": "Bot"},
            }
            self.comments.append(comment)
            return comment

        if method == "PATCH" and base_path.startswith(f"/repos/{REPO}/issues/comments/"):
            cid = int(base_path.rsplit("/", 1)[1])
            for comment in self.comments:
                if comment["id"] == cid:
                    comment["body"] = (payload or {})["body"]
                    return comment
            raise ApiError(404, method, path, '{"message": "Not Found"}')

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
    def test_repo_guides_resolution_matches_depth_2_and_nested_depth_3(self):
        self.assertEqual(
            pgo.resolve_guide_owners_dir("guides/flow-control/gaie-sim/values.yaml", REPO_ROOT),
            "guides/flow-control",
        )
        self.assertEqual(
            pgo.resolve_guide_owners_dir(
                "guides/fast-model-actuation/fast-model-actuation-keda/README.md", REPO_ROOT
            ),
            "guides/fast-model-actuation/fast-model-actuation-keda",
        )
        self.assertEqual(
            pgo.resolve_guide_owners_dir("guides/fast-model-actuation/README.md", REPO_ROOT),
            "guides/fast-model-actuation",
        )
        self.assertEqual(
            pgo.resolve_guide_owners_dir("guides/models/deepseek-v4/guide.yaml", REPO_ROOT),
            "guides/models/deepseek-v4",
        )
        self.assertEqual(
            pgo.resolve_guide_owners_dir(
                "guides/batch-serving/asynchronous-processing/multitenant/values.yaml", REPO_ROOT
            ),
            "guides/batch-serving/asynchronous-processing",
        )

    def test_ignores_top_level_prereqs_non_guides_and_never_uses_root_owners(self):
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
            self.assertEqual(
                pgo.resolve_guide_owners_dir("guides/alpha/sub/values.yaml", root),
                "guides/alpha",
            )

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
        _write_owners(self.root, "guides/alpha", approvers=["alice"], reviewers=["ro-alpha"])
        _write_owners(self.root, "guides/beta", approvers=["bob"], reviewers=["ro-beta"])
        _write_owners(self.root, "guides/gamma", approvers=["carol"])

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run(self, fake: FakeGitHubApi, *, dry_run: bool = False) -> int:
        with mock.patch.object(pgo, "api", side_effect=fake):
            return pgo.route_pr(REPO, 42, "tok", self.root, dry_run=dry_run)

    def test_partitions_collaborators_and_cc_and_excludes_author(self):
        fake = FakeGitHubApi(
            pr={
                "number": 42,
                "state": "open",
                "draft": False,
                "user": {"login": "Alice"},
                "requested_reviewers": [],
            },
            files=[{"filename": "guides/alpha/README.md"}, {"filename": "guides/beta/guide.yaml"}],
            collaborators={"alice", "bob"},
        )
        self.assertEqual(self._run(fake), 0)
        self.assertEqual(
            fake.write_calls(),
            [
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["bob"]}),
                (
                    "POST",
                    f"/repos/{REPO}/issues/42/comments",
                    {
                        "body": (
                            "<!-- guide-owners-cc -->\n"
                            "cc guide owners from `OWNERS`:\n"
                            "- `guides/alpha`: @ro-alpha\n"
                            "- `guides/beta`: @ro-beta"
                        )
                    },
                ),
            ],
        )

    def test_rename_previous_filename_routes_source_guide(self):
        fake = FakeGitHubApi(
            files=[{"filename": "docs/moved.md", "previous_filename": "guides/gamma/old.md"}],
            collaborators={"carol"},
        )
        self.assertEqual(self._run(fake), 0)
        self.assertEqual(
            fake.write_calls(),
            [("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["carol"]})],
        )

    def test_batch_422_falls_back_per_user_into_cc_comment(self):
        fake = FakeGitHubApi(
            files=[{"filename": "guides/alpha/README.md"}, {"filename": "guides/beta/README.md"}],
            collaborators={"alice", "bob"},
            reject_reviewers_422={"alice"},
        )
        self.assertEqual(self._run(fake), 0)
        writes = fake.write_calls()
        self.assertEqual(
            writes,
            [
                (
                    "POST",
                    f"/repos/{REPO}/pulls/42/requested_reviewers",
                    {"reviewers": ["alice", "bob"]},
                ),
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["alice"]}),
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["bob"]}),
                (
                    "POST",
                    f"/repos/{REPO}/issues/42/comments",
                    {
                        "body": (
                            "<!-- guide-owners-cc -->\n"
                            "cc guide owners from `OWNERS`:\n"
                            "- `guides/alpha`: @alice, @ro-alpha\n"
                            "- `guides/beta`: @ro-beta"
                        )
                    },
                ),
            ],
        )

    def test_antispam_respects_requested_reviewed_timeline_and_sticky_comment(self):
        _write_owners(
            self.root,
            "guides/delta",
            approvers=["already-req", "already-reviewed", "previously-requested"],
            reviewers=["ro-delta"],
        )
        fake = FakeGitHubApi(
            pr={
                "number": 42,
                "state": "open",
                "draft": False,
                "user": {"login": "contributor"},
                "requested_reviewers": [{"login": "Already-Req"}],
            },
            files=[{"filename": "guides/delta/README.md"}],
            reviews=[{"user": {"login": "already-reviewed"}}],
            events=[{"event": "review_requested", "requested_reviewer": {"login": "previously-requested"}}],
            comments=[
                {
                    "id": 500,
                    "body": "<!-- guide-owners-cc -->\ncc guide owners (`guides/delta/OWNERS`): @ro-delta",
                    "user": {"login": "github-actions[bot]", "type": "Bot"},
                }
            ],
            collaborators={"already-req", "already-reviewed", "previously-requested"},
        )
        self.assertEqual(self._run(fake), 0)
        self.assertEqual(fake.write_calls(), [])

    def test_synchronize_upgrades_single_guide_sticky_comment_and_ignores_spoof(self):
        fake = FakeGitHubApi(
            files=[{"filename": "guides/alpha/README.md"}, {"filename": "guides/beta/README.md"}],
            comments=[
                {
                    "id": 100,
                    "body": "<!-- guide-owners-cc -->\ncc guide owners (`guides/beta/OWNERS`): @ro-beta",
                    "user": {"login": "random-human", "type": "User"},
                },
                {
                    "id": 200,
                    "body": "<!-- guide-owners-cc -->\ncc guide owners (`guides/alpha/OWNERS`): @ro-alpha",
                    "user": {"login": "github-actions[bot]", "type": "Bot"},
                },
            ],
            collaborators={"alice", "bob"},
        )
        self.assertEqual(self._run(fake), 0)
        writes = fake.write_calls()
        self.assertEqual(
            writes,
            [
                (
                    "POST",
                    f"/repos/{REPO}/pulls/42/requested_reviewers",
                    {"reviewers": ["alice", "bob"]},
                ),
                (
                    "PATCH",
                    f"/repos/{REPO}/issues/comments/200",
                    {
                        "body": (
                            "<!-- guide-owners-cc -->\n"
                            "cc guide owners from `OWNERS`:\n"
                            "- `guides/alpha`: @ro-alpha\n"
                            "- `guides/beta`: @ro-beta"
                        )
                    },
                ),
            ],
        )

    def test_draft_and_closed_prs_make_zero_write_calls(self):
        for state, draft in [("open", True), ("closed", False)]:
            fake = FakeGitHubApi(
                pr={"number": 42, "state": state, "draft": draft, "user": {"login": "x"}},
                files=[{"filename": "guides/alpha/README.md"}],
                collaborators={"alice"},
            )
            self.assertEqual(self._run(fake), 0)
            self.assertEqual(fake.write_calls(), [])
            self.assertEqual(len(fake.calls), 1)

    def test_mass_sweep_guard_allows_5_and_skips_6(self):
        files = []
        collaborators = set()
        for idx in range(6):
            slug = f"guides/g{idx}"
            owner = f"owner{idx}"
            _write_owners(self.root, slug, approvers=[owner])
            files.append({"filename": f"{slug}/README.md"})
            collaborators.add(owner)

        fake_five = FakeGitHubApi(files=files[:5], collaborators=collaborators)
        self.assertEqual(self._run(fake_five), 0)
        self.assertEqual(
            fake_five.write_calls(),
            [
                (
                    "POST",
                    f"/repos/{REPO}/pulls/42/requested_reviewers",
                    {"reviewers": ["owner0", "owner1", "owner2", "owner3", "owner4"]},
                )
            ],
        )

        fake_six = FakeGitHubApi(files=files, collaborators=collaborators)
        self.assertEqual(self._run(fake_six), 0)
        self.assertEqual(fake_six.write_calls(), [])
        self.assertFalse(any("/collaborators/" in p for _, p, _ in fake_six.calls))

    def test_caps_at_15_total_reviewers_without_spilling_into_cc(self):
        owners = [f"collab{i:02d}" for i in range(18)]
        _write_owners(self.root, "guides/big", approvers=owners)
        fake = FakeGitHubApi(
            pr={
                "number": 42,
                "state": "open",
                "draft": False,
                "user": {"login": "contributor"},
                "requested_reviewers": [{"login": "pre-existing-1"}, {"login": "pre-existing-2"}],
            },
            files=[{"filename": "guides/big/README.md"}],
            collaborators=set(owners),
        )
        self.assertEqual(self._run(fake), 0)
        writes = fake.write_calls()
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0],
            (
                "POST",
                f"/repos/{REPO}/pulls/42/requested_reviewers",
                {"reviewers": owners[:13]},
            ),
        )

    def test_dry_run_makes_no_write_calls(self):
        fake = FakeGitHubApi(
            files=[{"filename": "guides/alpha/README.md"}],
            collaborators={"alice"},
        )
        self.assertEqual(self._run(fake, dry_run=True), 0)
        self.assertEqual(fake.write_calls(), [])

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

    def test_cross_guide_shared_owner_checked_once_case_insensitively(self):
        _write_owners(self.root, "guides/one", approvers=["Alice"])
        _write_owners(self.root, "guides/two", approvers=["alice"])
        fake = FakeGitHubApi(
            files=[{"filename": "guides/one/README.md"}, {"filename": "guides/two/README.md"}],
            collaborators={"alice"},
        )
        self.assertEqual(self._run(fake), 0)
        collab_gets = [p for m, p, _ in fake.calls if m == "GET" and "/collaborators/" in p]
        self.assertEqual(collab_gets, [f"/repos/{REPO}/collaborators/Alice"])
        self.assertEqual(
            fake.write_calls(),
            [("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["Alice"]})],
        )

    def test_non_404_collaborator_error_propagates(self):
        fake = FakeGitHubApi(
            files=[{"filename": "guides/alpha/README.md"}],
            collaborator_error=(403, '{"message": "Resource not accessible by integration"}'),
        )
        with self.assertRaises(ApiError) as ctx:
            self._run(fake)
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(fake.write_calls(), [])

    def test_team_review_requested_event_without_requested_reviewer_and_requested_teams_cap(self):
        owners = [f"collab{i:02d}" for i in range(15)]
        _write_owners(self.root, "guides/teamcap", approvers=owners)
        fake = FakeGitHubApi(
            pr={
                "number": 42,
                "state": "open",
                "draft": False,
                "user": {"login": "contributor"},
                "requested_reviewers": [{"login": "pre-req"}],
                "requested_teams": [{"slug": "router-maintainers"}, {"slug": "sig-docs"}],
            },
            files=[{"filename": "guides/teamcap/README.md"}],
            events=[{"event": "review_requested", "requested_team": {"slug": "router-maintainers"}}],
            collaborators=set(owners),
        )
        self.assertEqual(self._run(fake), 0)
        self.assertEqual(
            fake.write_calls(),
            [
                (
                    "POST",
                    f"/repos/{REPO}/pulls/42/requested_reviewers",
                    {"reviewers": owners[:12]},
                )
            ],
        )

    def test_cap_422_and_single_candidate_422_handling(self):
        cap_fake = FakeGitHubApi(
            files=[{"filename": "guides/gamma/README.md"}],
            collaborators={"carol"},
            cap_422_on_request=True,
        )
        self.assertEqual(self._run(cap_fake), 0)
        self.assertEqual(
            cap_fake.write_calls(),
            [("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["carol"]})],
        )

        single_fake = FakeGitHubApi(
            files=[{"filename": "guides/gamma/README.md"}],
            collaborators={"carol"},
            reject_reviewers_422={"carol"},
        )
        self.assertEqual(self._run(single_fake), 0)
        self.assertEqual(
            single_fake.write_calls(),
            [
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["carol"]}),
                (
                    "POST",
                    f"/repos/{REPO}/issues/42/comments",
                    {"body": "<!-- guide-owners-cc -->\ncc guide owners (`guides/gamma/OWNERS`): @carol"},
                ),
            ],
        )

    def test_updates_multiguide_sticky_comment_and_ignores_other_bots(self):
        _write_owners(self.root, "guides/alpha", approvers=["alice"], reviewers=["ro-alpha", "ro-alpha-2"])
        fake = FakeGitHubApi(
            files=[{"filename": "guides/alpha/README.md"}, {"filename": "guides/gamma/README.md"}],
            comments=[
                {
                    "id": 100,
                    "body": "<!-- guide-owners-cc -->\ncc guide owners (`guides/beta/OWNERS`): @ro-beta",
                    "user": {"login": "some-other-app[bot]", "type": "Bot"},
                },
                {
                    "id": 200,
                    "body": (
                        "<!-- guide-owners-cc -->\n"
                        "cc guide owners from `OWNERS`:\n"
                        "- `guides/alpha`: @ro-alpha\n"
                        "- `guides/beta`: @ro-beta"
                    ),
                    "user": {"login": "github-actions[bot]", "type": "Bot"},
                },
            ],
            collaborators={"alice"},
        )
        self.assertEqual(self._run(fake), 0)
        self.assertEqual(
            fake.write_calls(),
            [
                ("POST", f"/repos/{REPO}/pulls/42/requested_reviewers", {"reviewers": ["alice"]}),
                (
                    "PATCH",
                    f"/repos/{REPO}/issues/comments/200",
                    {
                        "body": (
                            "<!-- guide-owners-cc -->\n"
                            "cc guide owners from `OWNERS`:\n"
                            "- `guides/alpha`: @ro-alpha, @ro-alpha-2\n"
                            "- `guides/beta`: @ro-beta\n"
                            "- `guides/gamma`: @carol"
                        )
                    },
                ),
            ],
        )


class RepoStaticInvariantsTests(unittest.TestCase):
    def test_all_repo_guide_owners_files_parse_cleanly(self):
        owners_files = sorted((REPO_ROOT / "guides").rglob("OWNERS"))
        self.assertGreaterEqual(len(owners_files), 24)
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

    def test_workflow_uses_safe_pull_request_target_checkout_and_env_inputs(self):
        wf = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(wf["permissions"], {})
        # PyYAML parses bare `on:` as boolean True unless quoted.
        triggers = wf.get("on", wf.get(True))
        self.assertIn("pull_request_target", triggers)
        self.assertEqual(triggers["pull_request_target"]["paths"], ["guides/**"])

        job = wf["jobs"]["route-guide-owners"]
        self.assertEqual(
            job["permissions"],
            {"contents": "read", "issues": "write", "pull-requests": "write"},
        )
        checkout_step = job["steps"][0]
        self.assertEqual(
            checkout_step["with"]["ref"],
            "${{ github.event.pull_request.base.ref || github.ref_name }}",
        )
        self.assertFalse(checkout_step["with"]["persist-credentials"])
        self.assertIn("guides/**/OWNERS", checkout_step["with"]["sparse-checkout"])

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
