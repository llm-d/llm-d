"""Tests for source blob freshness checks on translated documentation.

Run from the repository root with:

    python -m pytest scripts/tests/test_check_translations.py -v
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_translations  # noqa: E402


SOURCE = "docs/getting-started/README.mdx"
TRANSLATION = "translations/zh-Hans/docs/getting-started/README.mdx"


def _write_page(root, source_content=b"English source\n", *, recorded_blob=None, status=None, source=SOURCE):
    source_path = root / source
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source_content)

    translation_path = root / TRANSLATION
    translation_path.parent.mkdir(parents=True, exist_ok=True)
    recorded_blob = recorded_blob or check_translations.git_blob_sha(source_content)
    front_matter = [
        "---",
        f"translation_source: {source}",
        f"translation_source_blob: {recorded_blob}",
    ]
    if status is not None:
        front_matter.append(f"translation_status: {status}")
    front_matter.append("---")
    translation_path.write_text("\n".join(front_matter) + "\n\n# 中文译文\n", encoding="utf-8")
    return translation_path


def test_git_blob_sha_matches_git_hash_object(tmp_path):
    source = tmp_path / "source.md"
    source.write_bytes(b"line one\nline two\n")

    expected = subprocess.check_output(["git", "hash-object", str(source)], text=True).strip()

    assert check_translations.git_blob_sha(source.read_bytes()) == expected


def test_current_translation_passes(tmp_path):
    _write_page(tmp_path)

    errors, stale = check_translations.check_translations(tmp_path)

    assert errors == []
    assert stale == []


def test_changed_source_without_stale_marker_fails(tmp_path):
    _write_page(tmp_path, source_content=b"Original English\n")
    (tmp_path / SOURCE).write_bytes(b"Updated English\n")

    errors, stale = check_translations.check_translations(tmp_path)

    assert stale == []
    assert len(errors) == 1
    assert "changed" in errors[0].message
    assert "translation_status: stale" in errors[0].message


def test_changed_source_with_stale_marker_passes_and_is_reported(tmp_path):
    _write_page(tmp_path, source_content=b"Original English\n", status="stale")
    (tmp_path / SOURCE).write_bytes(b"Updated English\n")

    errors, stale = check_translations.check_translations(tmp_path)
    report = check_translations.render_report(errors, stale)

    assert errors == []
    assert len(stale) == 1
    assert "Explicitly stale translations" in report
    assert TRANSLATION in report
    assert SOURCE in report


def test_report_includes_marked_stale_pages_even_when_another_page_fails(tmp_path):
    original_source = b"Original English\n"
    _write_page(tmp_path, source_content=original_source, status="stale")
    (tmp_path / SOURCE).write_bytes(b"Updated English\n")
    second_translation = tmp_path / "translations/ja/docs/getting-started/README.mdx"
    second_translation.parent.mkdir(parents=True, exist_ok=True)
    second_translation.write_text(
        "---\n"
        f"translation_source: {SOURCE}\n"
        f"translation_source_blob: {check_translations.git_blob_sha(original_source)}\n"
        "---\n\n# 翻訳\n",
        encoding="utf-8",
    )

    errors, stale = check_translations.check_translations(tmp_path)
    report = check_translations.render_report(errors, stale)

    assert len(errors) == 1
    assert len(stale) == 1
    assert "translation source check found errors" in report
    assert TRANSLATION in report


def test_stale_marker_must_be_removed_after_source_is_updated(tmp_path):
    _write_page(tmp_path, status="stale")

    errors, stale = check_translations.check_translations(tmp_path)

    assert stale == []
    assert len(errors) == 1
    assert "remove translation_status: stale" in errors[0].message


def test_untranslated_english_page_does_not_require_translation(tmp_path):
    (tmp_path / "docs/architecture/example.md").parent.mkdir(parents=True)
    (tmp_path / "docs/architecture/example.md").write_text("English only\n", encoding="utf-8")

    errors, stale = check_translations.check_translations(tmp_path)

    assert errors == []
    assert stale == []


@pytest.mark.parametrize(
    ("recorded_blob", "status", "expected_error"),
    [
        ("not-a-blob", None, "40-character lowercase"),
        ("0" * 40, "future", "must be 'current' or 'stale'"),
    ],
)
def test_invalid_metadata_fails(tmp_path, recorded_blob, status, expected_error):
    _write_page(tmp_path, recorded_blob=recorded_blob, status=status)

    errors, stale = check_translations.check_translations(tmp_path)

    assert stale == []
    assert len(errors) == 1
    assert expected_error in errors[0].message


def test_translation_source_must_match_its_path(tmp_path):
    _write_page(tmp_path, source="docs/another-page.md")

    errors, stale = check_translations.check_translations(tmp_path)

    assert stale == []
    assert len(errors) == 1
    assert "maps to" in errors[0].message


def test_missing_english_source_fails(tmp_path):
    _write_page(tmp_path)
    (tmp_path / SOURCE).unlink()

    errors, stale = check_translations.check_translations(tmp_path)

    assert stale == []
    assert len(errors) == 1
    assert "does not exist" in errors[0].message
