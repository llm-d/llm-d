"""Tests for .github/labeler.yml, .github/labeler-sync.yml, and area label workflows."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
LABELER_PATH = REPO_ROOT / ".github" / "labeler.yml"
LABELER_SYNC_PATH = REPO_ROOT / ".github" / "labeler-sync.yml"
TAXONOMY_PATH = REPO_ROOT / ".github" / "area_taxonomy.md"
PROWLABELS_PATH = REPO_ROOT / ".prowlabels.yaml"
GUIDES_MANIFEST_PATH = REPO_ROOT / "docs" / "well-lit-paths" / "guides.yaml"
BUG_TEMPLATE_PATH = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug.yml"
FEATURE_TEMPLATE_PATH = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature-request.yml"
HW_TEMPLATE_PATH = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "hardware-platform-support.yml"
PR_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "pr-area-labeler.yaml"
ISSUE_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "issue-area-label.yaml"
AREA_LABELS_SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "area_labels.py"

PATH_STEP = "Apply path labels and sync docs-only"
PR_BODY_STEP = "Add area labels from PR body"
ISSUE_BODY_STEP = "Add area labels from issue body and form fields"

# The matcher under test is the one pr-area-labeler.yaml runs in production.
_spec = importlib.util.spec_from_file_location("area_labels", AREA_LABELS_SCRIPT_PATH)
area_labels = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(area_labels)
matching_labels = area_labels.matching_labels


def workflow_step(workflow: dict, name: str) -> dict:
    """Return the named step of a workflow's area-labels job."""
    return next(
        step for step in workflow["jobs"]["area-labels"]["steps"] if step.get("name") == name
    )


class LabelerTaxonomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.labeler = yaml.safe_load(LABELER_PATH.read_text(encoding="utf-8"))
        cls.labeler_sync = yaml.safe_load(LABELER_SYNC_PATH.read_text(encoding="utf-8"))
        cls.prowlabels = yaml.safe_load(PROWLABELS_PATH.read_text(encoding="utf-8"))
        cls.taxonomy_md = TAXONOMY_PATH.read_text(encoding="utf-8")
        cls.pr_wf = yaml.safe_load(PR_WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.issue_wf = yaml.safe_load(ISSUE_WORKFLOW_PATH.read_text(encoding="utf-8"))

    def evaluate_all(self, changed_files: list[str]) -> set[str]:
        return matching_labels(self.labeler, changed_files) | matching_labels(
            self.labeler_sync, changed_files
        )

    def test_any_glob_to_all_files_matches_upstream_quantifier_semantics(self) -> None:
        separate_globs_cfg = {
            "docs-only": [
                {"changed-files": [{"any-glob-to-all-files": ["foo.txt", "bar.txt"]}]}
            ]
        }
        brace_glob_cfg = {
            "docs-only": [
                {"changed-files": [{"any-glob-to-all-files": ["{foo.txt,bar.txt}"]}]}
            ]
        }
        self.assertEqual(matching_labels(separate_globs_cfg, ["foo.txt", "bar.txt"]), set())
        self.assertEqual(
            matching_labels(brace_glob_cfg, ["foo.txt", "bar.txt"]), {"docs-only"}
        )
        self.assertEqual(matching_labels(brace_glob_cfg, []), set())

    def test_label_changes_add_additive_and_sync_synced_labels(self) -> None:
        additive = {
            "area/a": [{"changed-files": [{"any-glob-to-any-file": ["a/**"]}]}],
            "area/b": [{"changed-files": [{"any-glob-to-any-file": ["b/**"]}]}],
        }
        synced = {
            "docs-only": [
                {"all": [{"changed-files": [{"any-glob-to-all-files": ["**/*.md"]}]}]}
            ]
        }
        # Additive labels that no longer match stay; unrelated labels are untouched.
        self.assertEqual(
            area_labels.label_changes(
                additive, synced, ["a/x.md"], {"area/b", "lgtm"}
            ),
            [("add", "area/a"), ("add", "docs-only")],
        )
        # A synced label is removed once a non-matching file appears.
        self.assertEqual(
            area_labels.label_changes(
                additive, synced, ["a/x.md", "a/y.sh"], {"area/a", "docs-only", "lgtm"}
            ),
            [("remove", "docs-only")],
        )
        self.assertEqual(
            area_labels.label_changes(additive, synced, ["a/x.md"], {"area/a", "docs-only"}),
            [],
        )
        self.assertEqual(
            area_labels.label_changes(additive, synced, [], {"docs-only"}),
            [("remove", "docs-only")],
        )

    def test_pr_workflow_runs_base_branch_rules_on_every_event(self) -> None:
        triggers = self.pr_wf.get("on") or self.pr_wf.get(True)
        # One workflow handles every event and runs every step, so a queued run
        # that replaces a pending one in the concurrency group loses nothing.
        self.assertEqual(
            sorted(triggers["pull_request_target"]["types"]),
            ["edited", "opened", "reopened", "synchronize"],
        )
        self.assertFalse(self.pr_wf["concurrency"]["cancel-in-progress"])
        self.assertEqual(self.pr_wf["permissions"], {})

        job = self.pr_wf["jobs"]["area-labels"]
        self.assertEqual(
            job["permissions"], {"contents": "read", "pull-requests": "write"}
        )
        for step in job["steps"]:
            self.assertNotIn("if", step)
            uses = step.get("uses", "")
            # Whole-label-set writers can restore a label removed mid-run.
            self.assertFalse(uses.startswith("actions/labeler"))
            if uses.startswith("actions/checkout"):
                # The job holds a write token: never check out the PR head.
                self.assertNotIn("ref", step["with"])
                self.assertNotIn("repository", step["with"])
                self.assertIs(step["with"]["persist-credentials"], False)
        self.assertEqual(
            [s.get("name") for s in job["steps"][1:]], [PATH_STEP, PR_BODY_STEP]
        )

        self.assertEqual(
            job["env"]["STRIP_BLOCKS"],
            self.issue_wf["jobs"]["area-labels"]["env"]["STRIP_BLOCKS"],
            "STRIP_BLOCKS must stay identical in the PR and issue workflows",
        )

    def test_prowlabels_and_taxonomy_invariants(self) -> None:
        labeler_areas = set(self.labeler.keys())
        self.assertTrue(all(lbl.startswith("area/") for lbl in labeler_areas))

        prow_list = self.prowlabels.get("area", [])
        self.assertEqual(
            prow_list,
            sorted(set(prow_list)),
            ".prowlabels.yaml area list must be sorted and unique",
        )
        prow_areas = {f"area/{name}" for name in prow_list}
        self.assertEqual(prow_areas, labeler_areas)

        documented_areas = set(
            re.findall(
                r"^\|\s*`(area/[a-z0-9-]+)`\s*\|", self.taxonomy_md, flags=re.MULTILINE
            )
        )
        self.assertEqual(documented_areas, labeler_areas)
        self.assertEqual(set(self.labeler_sync.keys()), {"docs-only"})

    def test_every_guide_and_recipe_subtree_maps_to_domain_or_infra_area(self) -> None:
        guides_dir = REPO_ROOT / "guides"
        meta_dirs = {guides_dir / "scripts", guides_dir / "templates"}

        candidate_dirs: set[Path] = set()
        for child in guides_dir.iterdir():
            if child.is_dir() and child not in meta_dirs:
                candidate_dirs.add(child)
        for marker in ("guide.yaml", "OWNERS"):
            for path in guides_dir.rglob(marker):
                if path.parent not in meta_dirs and path.parent != guides_dir:
                    candidate_dirs.add(path.parent)

        manifest = yaml.safe_load(GUIDES_MANIFEST_PATH.read_text(encoding="utf-8"))
        for section in manifest.get("sections", {}).values():
            for entry in section.get("guides", []):
                candidate_dirs.add(REPO_ROOT / entry["dir"])

        unmapped = []
        for guide_path in sorted(candidate_dirs):
            rel = guide_path.relative_to(REPO_ROOT).as_posix()
            probe = f"{rel}/manifest.yaml"
            matched = matching_labels(self.labeler, [probe])
            self.assertIn("area/guides", matched)
            specific = matched - {"area/guides", "area/docs"}
            if not specific:
                unmapped.append(rel)
        self.assertEqual(
            unmapped,
            [],
            f"Guide directories missing a domain or infra area/* mapping in .github/labeler.yml: {unmapped}",
        )

        # Domain subtrees under guides/recipes/ must map to their SIG area beyond area/infra.
        for subtree, expected_area in (
            ("guides/recipes/observability/grafana/dashboards/x.json", "area/observability"),
            ("guides/recipes/router/base.values.yaml", "area/router"),
            ("guides/recipes/autoscaling/metrics-reader/rbac.yaml", "area/autoscaling"),
        ):
            self.assertIn(expected_area, matching_labels(self.labeler, [subtree]))

    def test_every_tracked_file_matches_at_least_one_area(self) -> None:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            self.skipTest("Not inside a git checkout")
        unmatched = [
            path
            for path in proc.stdout.splitlines()
            if path and not matching_labels(self.labeler, [path])
        ]
        self.assertEqual(
            unmatched,
            [],
            f"Tracked files not covered by any area/* rule in .github/labeler.yml: {unmatched}",
        )

    def test_issue_templates_synced_with_issue_workflow(self) -> None:
        bug_tpl = yaml.safe_load(BUG_TEMPLATE_PATH.read_text(encoding="utf-8"))
        fr_tpl = yaml.safe_load(FEATURE_TEMPLATE_PATH.read_text(encoding="utf-8"))
        hw_tpl = yaml.safe_load(HW_TEMPLATE_PATH.read_text(encoding="utf-8"))

        bug_options = next(
            item for item in bug_tpl["body"] if item.get("id") == "area"
        )["attributes"]["options"]
        fr_options = next(
            item for item in fr_tpl["body"] if item.get("id") == "area"
        )["attributes"]["options"]
        self.assertEqual(
            bug_options,
            fr_options,
            "bug.yml and feature-request.yml area dropdown options must stay in sync",
        )

        script = workflow_step(self.issue_wf, ISSUE_BODY_STEP)["run"]
        for opt in fr_options:
            if opt == "Other":
                continue
            self.assertIn(
                f'"{opt}"',
                script,
                f"Dropdown option {opt!r} is missing from issue-area-label.yaml",
            )

        hw_checkboxes = [
            opt["label"]
            for opt in next(
                item
                for item in hw_tpl["body"]
                if item.get("id") == "implementation-plan"
            )["attributes"]["options"]
        ]
        for expected_cb in (
            "Documentation updates",
            "Dockerfile changes",
            "CI/CD integration",
            "Well-lit path guide creation/update",
        ):
            self.assertIn(expected_cb, hw_checkboxes)
            self.assertIn(expected_cb, script)

        # Form-opened hardware issues are detected by these rendered headings.
        hw_field_labels = [
            item.get("attributes", {}).get("label") for item in hw_tpl["body"]
        ]
        for heading in ("Support Type", "Implementation Plan"):
            self.assertIn(heading, hw_field_labels)
            self.assertIn(f"^### {heading}[[:space:]]*$", script)

        referenced_labels = set(re.findall(r"area/[a-z0-9-]+", script))
        self.assertTrue(referenced_labels.issubset(set(self.labeler.keys())))

    def test_representative_pr_file_sets(self) -> None:
        self.assertEqual(
            self.evaluate_all(["README.md", "docs/assets/flow-control.svg"]),
            {"area/docs", "docs-only"},
        )
        self.assertEqual(
            self.evaluate_all(["CONTRIBUTING.md", ".github/area_taxonomy.md"]),
            {"area/docs", "area/ci", "docs-only"},
        )
        self.assertEqual(
            self.evaluate_all(["guides/flow-control/README.md"]),
            {"area/guides", "area/router", "area/flowcontrol", "area/docs", "docs-only"},
        )
        self.assertEqual(
            self.evaluate_all(
                ["guides/flow-control/README.md", "guides/flow-control/guide.yaml"]
            ),
            {"area/guides", "area/router", "area/flowcontrol", "area/docs"},
        )
        self.assertEqual(
            self.evaluate_all(["docs/menu-config.json", "docs/well-lit-paths/guides.yaml"]),
            {"area/docs", "docs-only"},
        )
        self.assertEqual(
            self.evaluate_all([".github/workflows/typo-checker.md"]),
            {"area/ci"},
        )
        self.assertEqual(
            self.evaluate_all(["OWNERS"]),
            {"area/ci"},
        )
        self.assertEqual(
            self.evaluate_all(["guides/flow-control/OWNERS"]),
            {"area/guides", "area/router", "area/flowcontrol"},
        )
        self.assertEqual(
            self.evaluate_all(["release/README.md"]),
            {"area/ci"},
        )
        self.assertEqual(
            self.evaluate_all(
                ["docs/infrastructure/providers/aks/gpu-operator.helmfile.yaml"]
            ),
            {"area/infra"},
        )
        self.assertEqual(
            self.evaluate_all(["docker/Dockerfile.cuda", "patches/nvshmem.patch"]),
            {"area/docker"},
        )
        self.assertEqual(
            self.evaluate_all(["docker/scripts/snapshot/restore.py"]),
            {"area/docker", "area/autoscaling"},
        )
        self.assertEqual(
            self.evaluate_all(["guides/wide-ep/monitoring/prometheus-scrape-configs.yaml"]),
            {"area/guides", "area/disaggregation", "area/observability"},
        )
        self.assertEqual(
            self.evaluate_all(["guides/coord-disaggregation/benchmark-results/run.json"]),
            {"area/guides", "area/disaggregation", "area/benchmarking"},
        )
        self.assertEqual(
            self.evaluate_all([".markdownlint.json", "_typos.toml"]),
            {"area/ci", "docs-only"},
        )
        self.assertEqual(
            self.evaluate_all(["DCO", "LICENSE"]),
            {"area/docs", "docs-only"},
        )
        self.assertEqual(
            self.evaluate_all(["proposals/coordinator.md"]),
            {"area/docs", "area/proposals", "area/disaggregation", "docs-only"},
        )

    def _run_workflow_shell(
        self,
        workflow: dict,
        body_env_name: str,
        num_env_name: str,
        body: str,
        current_labels: list[str],
    ) -> tuple[list[str], str]:
        step = PR_BODY_STEP if body_env_name == "PR_BODY" else ISSUE_BODY_STEP
        added, _, output = self._run_step(
            workflow, step, {body_env_name: body, num_env_name: "42"}, current_labels
        )
        return added, output

    def _run_step(
        self,
        workflow: dict,
        step_name: str,
        step_env: dict[str, str],
        current_labels: list[str],
        changed_files: tuple[str, ...] = (),
        existing_labels: list[str] | None = None,
    ) -> tuple[list[str], list[str], str]:
        """Run one workflow step's shell against a mock gh; return (added, removed, output)."""
        job = workflow["jobs"]["area-labels"]
        strip_blocks = job["env"]["STRIP_BLOCKS"]
        run_script = workflow_step(workflow, step_name)["run"]
        valid_labels = (
            sorted([*self.labeler.keys(), *self.labeler_sync.keys()])
            if existing_labels is None
            else existing_labels
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            added_log = tmp / "added.txt"
            added_log.write_text("", encoding="utf-8")
            removed_log = tmp / "removed.txt"
            removed_log.write_text("", encoding="utf-8")
            files_file = tmp / "files.txt"
            files_file.write_text("".join(f"{f}\n" for f in changed_files), encoding="utf-8")
            existing_file = tmp / "existing.txt"
            existing_file.write_text("\n".join(valid_labels) + "\n", encoding="utf-8")
            current_file = tmp / "current.txt"
            current_file.write_text(
                "\n".join(current_labels) + ("\n" if current_labels else ""),
                encoding="utf-8",
            )

            mock_gh = tmp / "gh"
            mock_gh.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'if [[ "$1 $2" == "label list" ]]; then\n'
                f'  cat "{existing_file}"\n'
                'elif [[ "$1" == "api" ]]; then\n'
                f'  cat "{files_file}"\n'
                'elif [[ "$2" == "view" ]]; then\n'
                f'  cat "{current_file}"\n'
                'elif [[ "$2" == "edit" ]]; then\n'
                '  while [[ $# -gt 0 ]]; do\n'
                '    if [[ "$1" == "--add-label" ]]; then\n'
                f'      printf "%s\\n" "$2" >> "{added_log}"\n'
                "      shift 2\n"
                '    elif [[ "$1" == "--remove-label" ]]; then\n'
                f'      printf "%s\\n" "$2" >> "{removed_log}"\n'
                "      shift 2\n"
                "    else\n"
                "      shift\n"
                "    fi\n"
                "  done\n"
                "fi\n",
                encoding="utf-8",
            )
            mock_gh.chmod(mock_gh.stat().st_mode | stat.S_IXUSR)

            env = os.environ.copy()
            env["PATH"] = f"{tmp}:{env.get('PATH', '')}"
            env["STRIP_BLOCKS"] = strip_blocks
            env["GH_TOKEN"] = "mock-token"
            env["REPO"] = "llm-d/llm-d"
            env.update(step_env)

            # Match GitHub Actions' default bash --noprofile --norc -eo pipefail shell.
            proc = subprocess.run(
                ["bash", "-eo", "pipefail", "-c", run_script],
                env=env,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            added = added_log.read_text(encoding="utf-8").split()
            removed = removed_log.read_text(encoding="utf-8").split()
            return added, removed, proc.stdout + proc.stderr

    @unittest.skipUnless(shutil.which("yq"), "yq is required to run the path-label step")
    def test_path_step_adds_and_removes_labels_one_at_a_time(self) -> None:
        env = {"PR_NUMBER": "42"}
        # Docs PR: adds only what is missing and never touches unrelated labels.
        added, removed, _ = self._run_step(
            self.pr_wf,
            PATH_STEP,
            env,
            current_labels=["area/guides", "lgtm"],
            changed_files=("guides/flow-control/README.md",),
        )
        self.assertEqual(
            added, ["area/docs", "area/flowcontrol", "area/router", "docs-only"]
        )
        self.assertEqual(removed, [])

        # A manifest lands on the PR: docs-only goes, additive labels stay.
        added, removed, _ = self._run_step(
            self.pr_wf,
            PATH_STEP,
            env,
            current_labels=["area/benchmarking", "area/docs", "docs-only", "lgtm"],
            changed_files=("docs/README.md", "docs/operations/validation/run.sh"),
        )
        self.assertEqual((added, removed), ([], ["docs-only"]))

        # A label missing from the repository is skipped with a warning.
        added, removed, output = self._run_step(
            self.pr_wf,
            PATH_STEP,
            env,
            current_labels=[],
            changed_files=("guides/rl/README.md",),
            existing_labels=["area/guides", "area/docs", "docs-only"],
        )
        self.assertEqual((added, removed), (["area/docs", "area/guides", "docs-only"], []))
        self.assertIn(
            "::warning::Label 'area/rl' does not exist in llm-d/llm-d, skipping", output
        )

    def test_area_labels_cli_reads_json_configs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "additive.json").write_text(json.dumps(self.labeler), encoding="utf-8")
            (tmp / "sync.json").write_text(json.dumps(self.labeler_sync), encoding="utf-8")
            (tmp / "files").write_text("proposals/coordinator.md\n", encoding="utf-8")
            (tmp / "current").write_text("area/docs\nkind/feature\n", encoding="utf-8")
            proc = subprocess.run(
                [
                    "python3",
                    str(AREA_LABELS_SCRIPT_PATH),
                    "--additive", str(tmp / "additive.json"),
                    "--sync", str(tmp / "sync.json"),
                    "--files", str(tmp / "files"),
                    "--current", str(tmp / "current"),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertEqual(
            proc.stdout.splitlines(),
            ["add area/disaggregation", "add area/proposals", "add docs-only"],
        )

    def test_pr_body_directive_commonmark_fences_and_escaping(self) -> None:
        body = (
            "```--enable-prefix-caching``` is an inline triple-backtick code span at BOL.\n\n"
            "<!--\n/area docker\n-->\n"
            "````markdown\n"
            "```yaml\n"
            "/area batch\n"
            "```\n"
            "/area rl\n"
            "````\n"
            "/area Router area/flowcontrol\r\n"
            "/area observability\n"
            "/area guides\n"
            "/area bad%0A::error::injected\n"
        )
        added, output = self._run_workflow_shell(
            self.pr_wf,
            "PR_BODY",
            "PR_NUMBER",
            body,
            current_labels=["area/guides"],
        )
        self.assertEqual(added, ["area/flowcontrol", "area/observability", "area/router"])
        self.assertIn(
            "::warning::Unknown label 'area/bad%250a::error::injected', skipping",
            output,
        )

    def test_issue_forms_bug_dropdown_hw_headings_and_large_body_sigpipe(self) -> None:
        # 1. Feature Request with >70 KB body (verifies zero SIGPIPE under -eo pipefail)
        large_logs = "pod log line 1234567890\n" * 3500
        fr_body = (
            "### Feature Area\n\n"
            "Flow Control\n\n"
            "### Problem Statement\n\n"
            f"```--flag``` at column 0.\n{large_logs}\n"
            "/area Docs\n"
        )
        added, _ = self._run_workflow_shell(
            self.issue_wf,
            "ISSUE_BODY",
            "ISSUE_NUMBER",
            fr_body,
            current_labels=["enhancement", "triage"],
        )
        self.assertEqual(added, ["area/docs", "area/flowcontrol", "area/router"])

        # 2. Bug Report ("### Area" heading + new SIG option, and "_No response_" no-op)
        bug_body = (
            "### What happened?\n\nCrash.\n\n"
            "### Version\n\nv0.9.0\n\n"
            "### Area\n\n"
            "Batch / Async Inference\n\n"
            "### Relevant log output\n\n"
            "```shell\n/area docker\n```\n"
        )
        bug_added, _ = self._run_workflow_shell(
            self.issue_wf,
            "ISSUE_BODY",
            "ISSUE_NUMBER",
            bug_body,
            current_labels=["bug", "triage"],
        )
        self.assertEqual(bug_added, ["area/batch"])

        empty_bug_body = "### What happened?\n\nx\n\n### Area\n\n_No response_\n"
        empty_added, _ = self._run_workflow_shell(
            self.issue_wf,
            "ISSUE_BODY",
            "ISSUE_NUMBER",
            empty_bug_body,
            current_labels=["bug", "triage"],
        )
        self.assertEqual(empty_added, [])

        # 3. Realistic Hardware/Platform Support output (no type:markdown header,
        # only ["documentation"] label, and quoted checkbox outside Implementation Plan)
        hw_body = (
            "### Support Type\n\n"
            "Contributing support\n\n"
            "### Component Type\n\n"
            "Hardware Accelerator\n\n"
            "### Implementation Plan\n\n"
            "- [X] Documentation updates\n"
            "- [x] Dockerfile changes\n"
            "- [ ] CI/CD integration\n"
            "- [X] Well-lit path guide creation/update\n\n"
            "### Additional Information\n\n"
            "- [x] CI/CD integration was discussed elsewhere but not checked above.\n"
        )
        hw_added, _ = self._run_workflow_shell(
            self.issue_wf,
            "ISSUE_BODY",
            "ISSUE_NUMBER",
            hw_body,
            current_labels=["documentation"],
        )
        self.assertEqual(hw_added, ["area/docker", "area/docs", "area/guides", "area/infra"])


if __name__ == "__main__":
    unittest.main()
