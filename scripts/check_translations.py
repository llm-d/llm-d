#!/usr/bin/env python3
"""Check translated documentation against the Git blob of its English source.

Translated pages live at ``translations/<locale>/docs/<source path>`` and
record the source path and blob SHA in their YAML front matter. A translation
whose source changed must either be refreshed or explicitly marked
``translation_status: stale`` until it can be refreshed.

This deliberately checks only English pages that already have translations;
it does not require every English page to be translated.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable


FRONT_MATTER = re.compile(r"\A(?:\ufeff)?---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
METADATA_KEYS = {"translation_source", "translation_source_blob", "translation_status"}
SHA1 = re.compile(r"^[0-9a-f]{40}$")
MARKDOWN_SUFFIXES = {".md", ".mdx"}


@dataclass(frozen=True)
class Finding:
    path: str
    message: str


@dataclass(frozen=True)
class StaleTranslation:
    translation: str
    source: str
    recorded_blob: str
    current_blob: str


def git_blob_sha(content: bytes) -> str:
    """Return Git's SHA-1 blob object id for file bytes (without filters)."""
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _front_matter(path: Path) -> tuple[dict[str, str], str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {}, f"cannot read front matter: {exc}"

    match = FRONT_MATTER.match(text)
    if not match:
        return {}, "missing YAML front matter"

    values: dict[str, str] = {}
    for line_number, line in enumerate(match.group(1).splitlines(), start=2):
        key_value = re.fullmatch(r"([A-Za-z0-9_-]+):[ \t]*(.*?)\s*", line)
        if not key_value:
            continue
        key, raw_value = key_value.groups()
        if key not in METADATA_KEYS:
            continue
        if key in values:
            return {}, f"duplicate {key!r} field in front matter (line {line_number})"
        if not raw_value:
            return {}, f"empty {key!r} field in front matter (line {line_number})"
        if raw_value.startswith("\""):
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError:
                return {}, f"invalid quoted {key!r} field in front matter (line {line_number})"
            if not isinstance(value, str):
                return {}, f"{key!r} must be a string (line {line_number})"
        elif raw_value.startswith("'"):
            if len(raw_value) < 2 or not raw_value.endswith("'"):
                return {}, f"invalid quoted {key!r} field in front matter (line {line_number})"
            value = raw_value[1:-1]
        else:
            value = raw_value
        values[key] = value
    return values, None


def _valid_source_path(value: str) -> bool:
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and len(path.parts) >= 2
        and path.parts[0] == "docs"
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.suffix.lower() in MARKDOWN_SUFFIXES
    )


def _translated_pages(root: Path) -> Iterable[Path]:
    translations_root = root / "translations"
    if not translations_root.is_dir():
        return ()
    return (
        path
        for path in sorted(translations_root.glob("*/docs/**/*"))
        if path.is_file() and path.suffix.lower() in MARKDOWN_SUFFIXES
    )


def check_translations(root: Path) -> tuple[list[Finding], list[StaleTranslation]]:
    """Return validation errors and explicitly marked stale translations."""
    root = root.resolve()
    errors: list[Finding] = []
    stale: list[StaleTranslation] = []

    for translated_path in _translated_pages(root):
        relative_translation = translated_path.relative_to(root / "translations")
        translation_name = (Path("translations") / relative_translation).as_posix()
        parts = relative_translation.parts
        if len(parts) < 3 or parts[1] != "docs":
            errors.append(Finding(translation_name, "expected path translations/<locale>/docs/<source path>"))
            continue

        metadata, metadata_error = _front_matter(translated_path)
        if metadata_error:
            errors.append(Finding(translation_name, metadata_error))
            continue

        source = metadata.get("translation_source")
        if not source:
            errors.append(Finding(translation_name, "missing 'translation_source' field"))
            continue
        if not _valid_source_path(source):
            errors.append(Finding(translation_name, "'translation_source' must be a docs/<path>.md or .mdx path"))
            continue
        expected_source = PurePosixPath("docs", *parts[2:]).as_posix()
        if source != expected_source:
            errors.append(
                Finding(
                    translation_name,
                    f"'translation_source' is {source!r}, but this translation path maps to {expected_source!r}",
                )
            )
            continue

        recorded_blob = metadata.get("translation_source_blob")
        if not recorded_blob or not SHA1.fullmatch(recorded_blob):
            errors.append(Finding(translation_name, "'translation_source_blob' must be a 40-character lowercase Git SHA-1 blob id"))
            continue

        status = metadata.get("translation_status", "current")
        if status not in {"current", "stale"}:
            errors.append(Finding(translation_name, "'translation_status' must be 'current' or 'stale'"))
            continue

        source_path = root / source
        if not source_path.is_file():
            errors.append(Finding(translation_name, f"English source {source!r} does not exist"))
            continue

        current_blob = git_blob_sha(source_path.read_bytes())
        if current_blob != recorded_blob:
            if status == "stale":
                stale.append(StaleTranslation(translation_name, source, recorded_blob, current_blob))
            else:
                errors.append(
                    Finding(
                        translation_name,
                        f"source {source!r} changed (recorded {recorded_blob}, current {current_blob}); "
                        "refresh the translation and source blob, or set translation_status: stale",
                    )
                )
        elif status == "stale":
            errors.append(
                Finding(
                    translation_name,
                    f"source {source!r} matches the recorded blob; remove translation_status: stale",
                )
            )

    return errors, stale


def render_report(errors: list[Finding], stale: list[StaleTranslation]) -> str:
    lines = ["## Translation source freshness", ""]
    if errors:
        lines.extend(["The translation source check found errors:", ""])
        lines.extend(f"- **{finding.path}**: {finding.message}" for finding in errors)
        lines.append("")
    if stale:
        lines.extend(["Explicitly stale translations:", ""])
        lines.extend(
            f"- `{item.translation}` follows `{item.source}` "
            f"(recorded `{item.recorded_blob}`, current `{item.current_blob}`)"
            for item in stale
        )
    if not errors and not stale:
        lines.append("All translated pages match their recorded English source blobs.")
    return "\n".join(lines) + "\n"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors, stale = check_translations(root)
    report = render_report(errors, stale)
    print(report, end="")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(report)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
