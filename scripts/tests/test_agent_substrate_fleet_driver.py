"""Unit tests for guides/agent-substrate/scripts/hermes_fleet_driver.py."""

import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guides/agent-substrate/scripts/hermes_fleet_driver.py"


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("hermes_fleet_driver", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    """What urllib.request.urlopen returns for an HTTP 200."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


class _Opener:
    """Replays a scripted list of outcomes: bytes for a 200 body, or an exception to raise."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, req, timeout=None, context=None):
        self.requests.append(req)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://router/v1/chat/completions", code, "error", {}, io.BytesIO(b""))


def _post(driver, opener):
    return driver._post_turn("https://router/v1/chat/completions", b"{}", {"X-Hermes-Session-Id": "agent-0001-g1"}, None, opener=opener, sleep=lambda _s: None)


def _completion(content) -> str:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]})


# --- tier partitioning and deterministic session content


def test_default_tier_split_is_10_30_60(driver) -> None:
    tiers = [driver._assign_tier(i, 1000, 10, 30) for i in range(1, 1001)]
    assert tiers.count("premium-traffic") == 100
    assert tiers.count("standard-traffic") == 300
    assert tiers.count("best-effort-traffic") == 600
    # Tiers are contiguous agent ranges: premium first, then standard, then best-effort.
    assert tiers[:100] == ["premium-traffic"] * 100
    assert tiers[100:400] == ["standard-traffic"] * 300


def test_small_fleet_keeps_at_least_one_premium_agent(driver) -> None:
    tiers = [driver._assign_tier(i, 6, 10, 30) for i in range(1, 7)]
    assert tiers == ["premium-traffic", "standard-traffic"] + ["best-effort-traffic"] * 4


def test_zero_premium_percentage_assigns_no_premium_agent(driver) -> None:
    tiers = {driver._assign_tier(i, 100, 0, 30) for i in range(1, 101)}
    assert tiers == {"standard-traffic", "best-effort-traffic"}


def test_codename_is_deterministic_per_agent_and_generation(driver) -> None:
    assert driver._codename(7, 1) == driver._codename(7, 1)
    adjective, noun = driver._codename(7, 1).split("-")
    assert adjective in driver.CODE_ADJ and noun in driver.CODE_NOUN
    assert len({driver._codename(7, gen) for gen in range(1, 40)}) > 1


def test_context_notes_are_private_to_each_session(driver) -> None:
    assert driver._notes(1, 1, 50) == driver._notes(1, 1, 50)
    assert driver._notes(1, 1, 50) != driver._notes(2, 1, 50)
    assert driver._notes(1, 1, 50) != driver._notes(1, 2, 50)
    assert len(driver._notes(1, 1, 50).split()) == 50


def test_only_the_first_turn_teaches_unless_recall_only(driver) -> None:
    assert driver.teach_expected(1, recall_only=False)
    assert not driver.teach_expected(2, recall_only=False)
    assert not driver.teach_expected(1, recall_only=True)


# --- reading the reply


def test_reply_text_returns_the_assistant_content(driver) -> None:
    assert driver._reply_text(_completion("tensor-otter: hello")) == "tensor-otter: hello"


@pytest.mark.parametrize(
    "body",
    [
        _completion(None),
        json.dumps({"choices": []}),
        json.dumps({"choices": [{}]}),
        json.dumps({"choices": [{"message": None}]}),
        json.dumps({"object": "chat.completion"}),
    ],
)
def test_reply_text_is_empty_for_a_valid_response_without_text(driver, body) -> None:
    assert driver._reply_text(body) == ""


@pytest.mark.parametrize("body", ["not json", "[]", '"text"'])
def test_reply_text_is_none_when_the_body_is_not_a_json_object(driver, body) -> None:
    assert driver._reply_text(body) is None


def test_recall_ignores_case_spacing_and_punctuation(driver) -> None:
    assert driver._recalled("tensor-otter", "My codename is Tensor Otter.")
    assert driver._recalled("tensor-otter", "**TENSOR-OTTER**: prefix caching reuses KV blocks")
    assert not driver._recalled("tensor-otter", "I do not have a codename.")


# --- retry rules: a turn that may have completed is never sent twice


def test_a_200_response_is_returned_without_retrying(driver) -> None:
    opener = _Opener([_completion("ok").encode()])
    assert _post(driver, opener) == _completion("ok")
    assert len(opener.requests) == 1


@pytest.mark.parametrize("body", [_completion(None), json.dumps({"choices": []}), "not json"])
def test_a_200_without_usable_text_is_not_resent(driver, body) -> None:
    opener = _Opener([body.encode(), _completion("second answer").encode()])
    assert _post(driver, opener) == body
    assert len(opener.requests) == 1


def test_a_200_whose_body_cannot_be_read_is_not_resent(driver) -> None:
    class _Broken(_Response):
        def read(self) -> bytes:
            raise ConnectionResetError("reset while reading the body")

    class _BrokenOpener(_Opener):
        def __call__(self, req, timeout=None, context=None):
            self.requests.append(req)
            return _Broken(b"")

    opener = _BrokenOpener([])
    assert _post(driver, opener) is None
    assert len(opener.requests) == 1


def test_a_5xx_is_retried_until_it_succeeds(driver) -> None:
    opener = _Opener([_http_error(503), _http_error(500), _completion("ok").encode()])
    assert _post(driver, opener) == _completion("ok")
    assert len(opener.requests) == 3


def test_a_connection_error_is_retried(driver) -> None:
    opener = _Opener([ConnectionRefusedError("refused"), _completion("ok").encode()])
    assert _post(driver, opener) == _completion("ok")
    assert len(opener.requests) == 2


def test_gives_up_after_four_attempts(driver) -> None:
    opener = _Opener([_http_error(503)] * 6)
    assert _post(driver, opener) is None
    assert len(opener.requests) == 4


@pytest.mark.parametrize("code", [400, 401, 404, 429, 504])
def test_a_4xx_or_504_is_not_retried(driver, code) -> None:
    opener = _Opener([_http_error(code), _completion("ok").encode()])
    assert _post(driver, opener) is None
    assert len(opener.requests) == 1


@pytest.mark.parametrize("error", [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))])
def test_a_timeout_is_not_retried(driver, error) -> None:
    opener = _Opener([error, _completion("ok").encode()])
    assert _post(driver, opener) is None
    assert len(opener.requests) == 1


def test_the_request_carries_the_session_headers_and_body(driver) -> None:
    opener = _Opener([_completion("ok").encode()])
    _post(driver, opener)
    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert req.data == b"{}"
    assert req.get_header("X-hermes-session-id") == "agent-0001-g1"


# --- reporting


def test_report_counts_errors_and_recall_per_tier(driver, capsys) -> None:
    rows = [
        {"objective": "premium-traffic", "ok": True, "e2e_ms": 100.0, "recall_checked": True, "recall_ok": True},
        {"objective": "premium-traffic", "ok": True, "e2e_ms": 300.0, "recall_checked": True, "recall_ok": False},
        {"objective": "premium-traffic", "ok": False, "e2e_ms": 5.0, "recall_checked": False, "recall_ok": False},
        {"objective": "best-effort-traffic", "ok": True, "e2e_ms": 50.0, "recall_checked": False, "recall_ok": True},
    ]
    driver._print_report("Turn 2/2", rows)
    lines = {line.split()[0]: line.split() for line in capsys.readouterr().out.splitlines() if "-traffic" in line}
    assert lines["premium-traffic"][1:3] == ["3", "1"]
    assert lines["premium-traffic"][-1] == "1/2"
    assert lines["best-effort-traffic"][-1] == "n/a"
    assert "standard-traffic" not in lines
