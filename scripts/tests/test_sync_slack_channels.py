"""Tests for sync-slack-channels.py.

The ``workflows:`` list in notify-slack-nightly.yaml is generated from the
nightly workflows' display names, and ``workflow_run`` matches on display
names, so renaming a nightly without regenerating the list turns alerts off
silently. This test runs under scripts/tests/ in CI (ci-guides-check.yaml),
so the invariant is enforced on every PR.
"""

import importlib.util
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parents[1]
# sync-slack-channels.py imports matrix_common as a sibling; running the script
# directly puts scripts/ on sys.path, so a test loading it by path must too.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import matrix_common as mc  # noqa: E402

MODULE_PATH = SCRIPTS_DIR / "sync-slack-channels.py"
SPEC = importlib.util.spec_from_file_location("sync_slack_channels", MODULE_PATH)
assert SPEC and SPEC.loader
ssc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ssc)


def test_slack_channel_mapping_is_valid():
    mapping = ssc.load_mapping()
    on_disk = ssc.discover_nightly_names()
    errors = ssc.validate(mapping, on_disk)
    assert errors == [], "issues in .github/slack-channels.yaml:\n" + "\n".join(errors)


def test_notify_trigger_matches_generated_list():
    mapping = ssc.load_mapping()
    on_disk = ssc.discover_nightly_names()
    expected = ssc.generate_trigger_list(mapping, on_disk)
    assert expected, "no routed nightly workflows discovered"

    content = ssc.read_notify_workflow()
    current = mc.extract_matrix(content, ssc.TRIGGER_START, ssc.TRIGGER_END)
    assert current is not None, "workflows list sentinels not found in notify-slack-nightly.yaml"
    assert current.strip("\n") == expected.strip("\n"), (
        "notify-slack-nightly.yaml workflows list is stale; run "
        "python scripts/sync-slack-channels.py --fix"
    )