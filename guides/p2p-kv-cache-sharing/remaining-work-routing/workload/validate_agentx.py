#!/usr/bin/env python3
"""Validate one AgentX artifact directory as benchmark evidence."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=pathlib.Path)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--expected-url", required=True)
    parser.add_argument("--expected-corpus", required=True)
    parser.add_argument("--expected-seed", type=int, required=True)
    parser.add_argument("--expected-concurrency", type=int, required=True)
    parser.add_argument("--expected-warmup", type=float, required=True)
    parser.add_argument("--expected-duration", type=float, required=True)
    parser.add_argument("--expected-max-context", type=int, required=True)
    parser.add_argument("--require-exit-code", action="store_true")
    parser.add_argument("--report", type=pathlib.Path)
    return parser.parse_args()


def load_json(path: pathlib.Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        errors.append(f"missing artifact: {path.name}")
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"invalid {path.name}: {error}")
    return None


def load_jsonl(path: pathlib.Path, errors: list[str]) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        errors.append(f"missing artifact: {path.name}")
        return []
    except OSError as error:
        errors.append(f"cannot read {path.name}: {error}")
        return []

    records: list[dict] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            errors.append(f"invalid {path.name} line {line_number}: {error}")
            continue
        if not isinstance(record, dict):
            errors.append(f"invalid {path.name} line {line_number}: expected object")
            continue
        records.append(record)
    if not records:
        errors.append(f"{path.name} contains no records")
    return records


def nested(document: object, *keys: str) -> object | None:
    value = document
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def metric(record: dict, name: str) -> object | None:
    value = nested(record, "metrics", name)
    if isinstance(value, dict):
        return value.get("value")
    return value


def require_equal(
    errors: list[str], label: str, observed: object, expected: object
) -> None:
    if observed != expected:
        errors.append(f"{label}: expected {expected!r}, got {observed!r}")


def validate_summary(
    summary: object, args: argparse.Namespace, errors: list[str]
) -> dict[str, object]:
    if not isinstance(summary, dict):
        errors.append("profile_export_aiperf.json must contain an object")
        return {}

    require_equal(
        errors,
        "scenario",
        nested(summary, "input_config", "scenario"),
        "inferencex-agentx-mvp",
    )
    require_equal(
        errors,
        "submission_valid",
        nested(summary, "metadata", "submission_valid"),
        True,
    )
    require_equal(errors, "was_cancelled", summary.get("was_cancelled"), False)
    require_equal(errors, "error_summary", summary.get("error_summary"), [])

    endpoint = nested(summary, "input_config", "endpoint")
    models = endpoint.get("model_names") if isinstance(endpoint, dict) else None
    urls = endpoint.get("urls") if isinstance(endpoint, dict) else None
    require_equal(errors, "model_names", models, [args.expected_model])
    require_equal(errors, "urls", urls, [args.expected_url])
    require_equal(
        errors,
        "public_dataset",
        nested(summary, "input_config", "input", "public_dataset"),
        args.expected_corpus,
    )
    require_equal(
        errors,
        "random_seed",
        nested(summary, "input_config", "input", "random_seed"),
        args.expected_seed,
    )
    require_equal(
        errors,
        "max_context_length",
        nested(summary, "input_config", "input", "max_context_length"),
        args.expected_max_context,
    )
    require_equal(
        errors,
        "cache_bust",
        nested(summary, "input_config", "input", "prompt", "cache_bust", "target"),
        "first_turn_prefix",
    )
    require_equal(
        errors,
        "concurrency",
        nested(summary, "input_config", "loadgen", "concurrency"),
        args.expected_concurrency,
    )
    require_equal(
        errors,
        "warmup_duration",
        nested(summary, "input_config", "loadgen", "warmup_duration"),
        args.expected_warmup,
    )
    require_equal(
        errors,
        "benchmark_duration",
        nested(summary, "input_config", "loadgen", "benchmark_duration"),
        args.expected_duration,
    )
    observed_duration = nested(summary, "benchmark_duration", "avg")
    if not isinstance(observed_duration, (int, float)):
        errors.append(
            f"observed benchmark duration is missing: {observed_duration!r}"
        )
    elif observed_duration < args.expected_duration:
        errors.append(
            "observed benchmark duration is shorter than configured: "
            f"expected at least {args.expected_duration}, got {observed_duration}"
        )
    return {
        "aiperf_version": summary.get("aiperf_version"),
        "benchmark_id": summary.get("benchmark_id"),
        "observed_duration_seconds": observed_duration,
        "submission_valid": nested(summary, "metadata", "submission_valid"),
        "error_summary": summary.get("error_summary"),
    }


def validate_records(records: list[dict], errors: list[str]) -> dict[str, object]:
    phases: defaultdict[str, int] = defaultdict(int)
    record_errors = 0
    warmup_record_errors = 0
    cancelled = 0
    overflow_skips = 0
    missing_end = 0
    missing_metrics = 0
    missing_correlation = 0
    correlations: set[str] = set()
    session_correlations: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
    correlation_sessions: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    correlation_records: defaultdict[str, int] = defaultdict(int)

    for record in records:
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            errors.append("record is missing metadata")
            continue
        phase = metadata.get("benchmark_phase")
        if not isinstance(phase, str) or not phase:
            errors.append("record is missing benchmark_phase")
            phase = "missing"
        phases[phase] += 1
        if record.get("error") is not None:
            if phase == "profiling":
                record_errors += 1
            else:
                warmup_record_errors += 1
        if metadata.get("was_cancelled") is True:
            cancelled += 1
        if metadata.get("context_overflow_skip") is True:
            overflow_skips += 1
        if metadata.get("request_end_ns") is None:
            missing_end += 1

        correlation = metadata.get("x_correlation_id")
        conversation = metadata.get("conversation_id")
        root = metadata.get("root_correlation_id")
        if not isinstance(correlation, str) or not correlation:
            missing_correlation += 1
        else:
            correlations.add(correlation)
            correlation_records[correlation] += 1
            if isinstance(conversation, str) and conversation:
                root_key = root if isinstance(root, str) and root else correlation
                session_correlations[(phase, root_key, conversation)].add(correlation)
                correlation_sessions[correlation].add((root_key, conversation))

        if phase == "profiling" and record.get("error") is None:
            if any(
                metric(record, name) is None
                for name in (
                    "input_sequence_length",
                    "request_latency",
                    "time_to_first_token",
                )
            ):
                missing_metrics += 1

    if phases.get("profiling", 0) == 0:
        errors.append("profile_export.jsonl contains no profiling records")
    if record_errors:
        errors.append(
            f"profile_export.jsonl contains {record_errors} measured-phase request errors"
        )
    if cancelled:
        errors.append(f"profile_export.jsonl contains {cancelled} cancelled requests")
    if overflow_skips:
        errors.append(
            f"profile_export.jsonl contains {overflow_skips} context-overflow skips"
        )
    if missing_end:
        errors.append(f"profile_export.jsonl contains {missing_end} unterminated records")
    if missing_metrics:
        errors.append(
            f"profile_export.jsonl contains {missing_metrics} successful profiling "
            "records without required latency/token metrics"
        )
    if missing_correlation:
        errors.append(
            f"profile_export.jsonl contains {missing_correlation} records without "
            "x_correlation_id"
        )
    unstable = sum(len(values) != 1 for values in session_correlations.values())
    if unstable:
        errors.append(f"x_correlation_id changed within {unstable} session instances")
    collisions = sum(len(values) != 1 for values in correlation_sessions.values())
    if collisions:
        errors.append(f"x_correlation_id was reused by {collisions} distinct sessions")
    if records and len(correlations) < 2:
        errors.append(
            "fewer than two x_correlation_id values observed; reject a static header"
        )
    multi_turn = sum(count > 1 for count in correlation_records.values())
    if records and multi_turn == 0:
        errors.append("no multi-turn x_correlation_id session was observed")

    return {
        "records": len(records),
        "phases": dict(sorted(phases.items())),
        "request_errors": record_errors,
        "warmup_request_errors": warmup_record_errors,
        "cancelled": cancelled,
        "context_overflow_skips": overflow_skips,
        "unterminated": missing_end,
        "missing_required_metrics": missing_metrics,
        "missing_correlation_id": missing_correlation,
        "distinct_correlation_ids": len(correlations),
        "multi_turn_correlation_ids": multi_turn,
        "unstable_session_instances": unstable,
        "correlation_id_collisions": collisions,
    }


def main() -> None:
    args = parse_args()
    errors: list[str] = []
    artifact_dir = args.artifact_dir
    report_path = args.report or artifact_dir / "validation.json"

    exit_code = None
    exit_path = artifact_dir / "aiperf_exit_code.txt"
    if exit_path.exists():
        try:
            exit_code = int(exit_path.read_text().strip())
        except (OSError, ValueError) as error:
            errors.append(f"invalid aiperf_exit_code.txt: {error}")
        else:
            if exit_code != 0:
                errors.append(f"aiperf exited with status {exit_code}")
    elif args.require_exit_code:
        errors.append("missing artifact: aiperf_exit_code.txt")

    records = load_jsonl(artifact_dir / "profile_export.jsonl", errors)
    summary = load_json(artifact_dir / "profile_export_aiperf.json", errors)
    record_summary = validate_records(records, errors)
    aiperf_summary = validate_summary(summary, args, errors)
    profiling_records = record_summary.get("phases", {}).get("profiling", 0)
    summary_request_count = nested(summary, "request_count", "avg")
    if summary_request_count != profiling_records:
        errors.append(
            "request_count: expected profile summary to match profiling records "
            f"({profiling_records}), got {summary_request_count!r}"
        )
    if args.expected_warmup > 0 and record_summary.get("phases", {}).get("warmup", 0) == 0:
        errors.append("profile_export.jsonl contains no warmup records")

    report = {
        "schema": 1,
        "valid": not errors,
        "errors": errors,
        "aiperf_exit_code": exit_code,
        "expected": {
            "model": args.expected_model,
            "url": args.expected_url,
            "corpus": args.expected_corpus,
            "seed": args.expected_seed,
            "concurrency": args.expected_concurrency,
            "warmup_seconds": args.expected_warmup,
            "duration_seconds": args.expected_duration,
            "max_context_tokens": args.expected_max_context,
        },
        "records": record_summary,
        "aiperf": aiperf_summary,
    }
    try:
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except OSError as error:
        sys.exit(f"cannot write validation report {report_path}: {error}")

    if errors:
        for error in errors:
            print(f"INVALID: {error}", file=sys.stderr)
        sys.exit(1)
    print(
        f"valid AgentX artifacts: {profiling_records} profiling records, "
        f"{record_summary['distinct_correlation_ids']} session correlations"
    )


if __name__ == "__main__":
    main()
