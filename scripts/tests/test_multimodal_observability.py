"""Tests for multimodal serving observability and troubleshooting documentation (#1982)."""

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_multimodal_readme_observability_section_exists():
    """Verify that guides/multimodal-serving/README.md contains the Observability & Troubleshooting section."""
    readme_path = REPO_ROOT / "guides" / "multimodal-serving" / "README.md"
    assert readme_path.exists(), f"Missing {readme_path}"

    content = readme_path.read_text(encoding="utf-8")
    assert "## Observability & Troubleshooting" in content
    assert "### Key metrics for this path" in content
    assert "### Common failure modes" in content


def test_multimodal_readme_key_metrics_content():
    """Verify that key metrics for multimodal serving are documented."""
    readme_path = REPO_ROOT / "guides" / "multimodal-serving" / "README.md"
    content = readme_path.read_text(encoding="utf-8")

    obs_idx = content.find("## Observability & Troubleshooting")
    cleanup_idx = content.find("## Cleanup", obs_idx)
    assert obs_idx != -1 and cleanup_idx != -1
    section_content = content[obs_idx:cleanup_idx]

    expected_metrics = [
        "vllm:mm_cache_hits_total",
        "vllm:mm_cache_queries_total",
        "vllm:num_requests_running",
        "llm_d_disagg_sidecar_encode_duration_seconds",
        "llm_d_epp_prefix_mm_predicted_cached_tokens",
        "llm_d_epp_prefix_mm_prompt_tokens",
        "vllm:request_success_total",
        "vllm:time_to_first_token_seconds",
        "vllm:inter_token_latency_seconds",
    ]
    for metric in expected_metrics:
        assert metric in section_content, f"Expected metric '{metric}' in multimodal observability section"


def test_multimodal_readme_failure_modes():
    """Verify that key failure modes for multimodal serving are documented."""
    readme_path = REPO_ROOT / "guides" / "multimodal-serving" / "README.md"
    content = readme_path.read_text(encoding="utf-8")

    obs_idx = content.find("## Observability & Troubleshooting")
    cleanup_idx = content.find("## Cleanup", obs_idx)
    section_content = content[obs_idx:cleanup_idx]

    assert "High TTFT, Prefill and Decode pools idle" in section_content
    assert "Multimodal cache hits flat" in section_content
    assert "Encode pods receive zero requests" in section_content
    assert "OOM / Pod eviction on high-resolution media" in section_content
    assert "Decode KV cache saturation" in section_content


def test_multimodal_observability_relative_links():
    """Verify that all relative links and anchor fragments in the observability section resolve."""
    readme_path = REPO_ROOT / "guides" / "multimodal-serving" / "README.md"
    content = readme_path.read_text(encoding="utf-8")

    obs_idx = content.find("## Observability & Troubleshooting")
    cleanup_idx = content.find("## Cleanup", obs_idx)
    section_content = content[obs_idx:cleanup_idx]

    link_matches = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", section_content)
    assert len(link_matches) > 0, "No markdown links found in observability section"

    def slugify(heading: str) -> str:
        s = heading.strip().lower()
        s = re.sub(r"[^\w\s-]", "", s)
        s = re.sub(r"[\s_]+", "-", s)
        return s

    for text, target in link_matches:
        if target.startswith("http://") or target.startswith("https://"):
            continue
        if target.startswith("#"):
            anchor = target[1:]
            assert f"#{anchor}" in content or any(
                slugify(line[3:]) == anchor
                for line in content.splitlines()
                if line.startswith("## ") or line.startswith("### ")
            ), f"Anchor #{anchor} not found in {readme_path}"
            continue

        target_file_part = target.split("#")[0]
        anchor_part = target.split("#")[1] if "#" in target else None

        resolved_file = (readme_path.parent / target_file_part).resolve()
        assert resolved_file.exists(), f"Broken link '{target}' (resolved to '{resolved_file}')"

        if anchor_part:
            target_content = resolved_file.read_text(encoding="utf-8")
            headings = set()
            for line in target_content.splitlines():
                if line.startswith("#"):
                    raw = line.lstrip("#").strip()
                    # Standard slugify (collapse spaces)
                    headings.add(re.sub(r"[\s_]+", "-", re.sub(r"[^\w\s-]", "", raw.lower())))
                    # GitHub-style slugify (each space becomes a hyphen, punctuation stripped)
                    headings.add(re.sub(r"[^\w\s-]", "", raw.lower()).replace(" ", "-"))
            assert anchor_part in headings, (
                f"Anchor '{anchor_part}' in link '{target}' not found among headings in {resolved_file}: {headings}"
            )


def test_promql_multimodal_section():
    """Verify that docs/operations/observability/promql.md contains Multimodal queries."""
    promql_path = REPO_ROOT / "docs" / "operations" / "observability" / "promql.md"
    assert promql_path.exists()

    content = promql_path.read_text(encoding="utf-8")
    assert "### Multimodal & Encode Disaggregation" in content
    assert "vllm:mm_cache_hits_total" in content
    assert "vllm:mm_cache_queries_total" in content
    assert "llm_d_disagg_sidecar_encode_duration_seconds" in content
    assert "llm_d_epp_prefix_mm_prompt_tokens" in content

    # Test balanced parentheses in queries
    code_matches = re.findall(r"`([^`]+)`", content)
    for code in code_matches:
        if "vllm:mm_cache" in code or "llm_d_disagg_sidecar_encode" in code:
            assert code.count("(") == code.count(")"), f"Unbalanced parentheses in query: {code}"
            assert code.count("[") == code.count("]"), f"Unbalanced brackets in query: {code}"


def test_model_server_metrics_multimodal():
    """Verify that docs/operations/observability/model-server-metrics.md documents multimodal metrics."""
    metrics_path = REPO_ROOT / "docs" / "operations" / "observability" / "model-server-metrics.md"
    assert metrics_path.exists()

    content = metrics_path.read_text(encoding="utf-8")
    assert "### vLLM Multimodal Metrics" in content
    assert "`vllm:mm_cache_queries_total`" in content
    assert "`vllm:mm_cache_hits_total`" in content


def test_foundations_readme_multimodal_link():
    """Verify that docs/well-lit-paths/foundations/README.md links to the multimodal observability section."""
    foundations_path = REPO_ROOT / "docs" / "well-lit-paths" / "foundations" / "README.md"
    assert foundations_path.exists()

    content = foundations_path.read_text(encoding="utf-8")
    assert "guides/multimodal-serving/README.md#observability--troubleshooting" in content
