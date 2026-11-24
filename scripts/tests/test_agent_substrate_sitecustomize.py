"""Unit tests for guides/agent-substrate/hermes/actor/sitecustomize.py.

The module patches `httpx` and `aiohttp` when it is imported. Neither library
is installed in CI, so each test imports it against a small stand-in `httpx`
that models only what the hook touches.
"""

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "guides/agent-substrate/hermes/actor/sitecustomize.py"
LLMD = "http://agent-substrate-epp.llm-d-agent-substrate.svc.cluster.local/v1"


class _Url:
    def __init__(self, raw: str):
        self.raw = raw
        self.path = "/" + raw.split("://", 1)[-1].split("/", 1)[-1]


class _Request:
    def __init__(self, method, url, headers=None, content=b"", extensions=None, async_only=False):
        self.method = method
        self.url = url if isinstance(url, _Url) else _Url(url)
        self.headers = dict(headers or {})
        self.extensions = extensions or {}
        self._content = content
        self._async_only = async_only

    def read(self) -> bytes:
        if self._async_only:
            # What httpx does when an async byte stream is read synchronously.
            raise RuntimeError("Attempted to call a sync iterator on an async stream.")
        return self._content

    async def aread(self) -> bytes:
        return self._content


class _Response:
    def __init__(self, status_code, headers=None, json=None, content=None, request=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.json_body = json
        self.content = content
        self.request = request


def _fake_httpx(sent: list) -> types.ModuleType:
    module = types.ModuleType("httpx")

    class Client:
        def send(self, request, *args, **kwargs):
            sent.append(request)
            return _Response(200, json={"upstream": True}, request=request)

    class AsyncClient:
        async def send(self, request, *args, **kwargs):
            sent.append(request)
            return _Response(200, json={"upstream": True}, request=request)

    module.Client, module.AsyncClient = Client, AsyncClient
    module.Request, module.Response = _Request, _Response
    return module


@pytest.fixture
def hook(monkeypatch, tmp_path):
    """The module imported against the stand-in httpx, with the warm-up still running."""
    sent: list = []
    monkeypatch.setitem(sys.modules, "httpx", _fake_httpx(sent))
    monkeypatch.setitem(sys.modules, "aiohttp", None)  # import aiohttp.web -> ModuleNotFoundError
    monkeypatch.setenv("WARMUP_DONE_FILE", str(tmp_path / "agent-warmup-done"))
    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", asyncio.BaseEventLoop.run_in_executor)
    spec = importlib.util.spec_from_file_location("llmd_sitecustomize_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.sent = sent
    module.done_file = tmp_path / "agent-warmup-done"
    return module


def _chat(body: dict, **kwargs) -> _Request:
    return _Request("POST", f"{LLMD}/chat/completions", {"content-type": "application/json", "content-length": "1"}, json.dumps(body).encode(), **kwargs)


def _send(hook, request, turn_headers=None):
    token = hook._TURN_HEADERS.set(turn_headers or {})
    try:
        return sys.modules["httpx"].Client().send(request)
    finally:
        hook._TURN_HEADERS.reset(token)


def _asend(hook, request, turn_headers=None):
    async def run():
        token = hook._TURN_HEADERS.set(turn_headers or {})
        try:
            return await sys.modules["httpx"].AsyncClient().send(request)
        finally:
            hook._TURN_HEADERS.reset(token)

    return asyncio.run(run())


TURN = {"x-hermes-session-id": "agent-0007-g1", "x-llm-d-inference-objective": "premium-traffic"}


# --- which inbound headers are forwarded


def test_only_the_session_and_tier_headers_are_extracted(hook) -> None:
    inbound = {"x-hermes-session-id": " agent-0007-g1 ", "x-llm-d-inference-objective": "premium-traffic", "authorization": "Bearer secret"}
    assert hook._sanitize_headers(inbound) == TURN


@pytest.mark.parametrize("value", ["agent 0007", "agent-0007\r\nx-injected: 1", "a" * 129, "", "   "])
def test_unsafe_header_values_are_dropped(hook, value) -> None:
    assert hook._sanitize_headers({"x-hermes-session-id": value}) == {}


# --- outbound requests to llm-d


def test_turn_headers_are_attached_to_the_outbound_request(hook) -> None:
    _send(hook, _chat({"model": "m", "messages": []}), TURN)
    (sent,) = hook.sent
    assert sent.headers["x-hermes-session-id"] == "agent-0007-g1"
    assert sent.headers["x-llm-d-inference-objective"] == "premium-traffic"


def test_headers_already_on_the_request_are_not_overwritten(hook) -> None:
    request = _chat({"model": "m", "messages": []})
    request.headers["x-hermes-session-id"] = "explicit-session"
    _send(hook, request, TURN)
    assert hook.sent[0].headers["x-hermes-session-id"] == "explicit-session"


def test_thinking_is_disabled_by_default_on_chat_completions(hook) -> None:
    _send(hook, _chat({"model": "m", "messages": []}), TURN)
    (sent,) = hook.sent
    assert json.loads(sent.read())["chat_template_kwargs"] == {"enable_thinking": False}
    # The body was rewritten, so the stale Content-Length must not be carried over.
    assert "content-length" not in {k.lower() for k in sent.headers}
    assert sent.headers["x-hermes-session-id"] == "agent-0007-g1"


def test_an_explicit_thinking_setting_is_kept(hook) -> None:
    original = _chat({"model": "m", "messages": [], "chat_template_kwargs": {"enable_thinking": True}})
    _send(hook, original, TURN)
    assert hook.sent[0] is original


def test_other_chat_template_kwargs_are_preserved(hook) -> None:
    _send(hook, _chat({"model": "m", "messages": [], "chat_template_kwargs": {"foo": 1}}), TURN)
    assert json.loads(hook.sent[0].read())["chat_template_kwargs"] == {"foo": 1, "enable_thinking": False}


def test_a_body_that_is_not_json_is_forwarded_unchanged(hook) -> None:
    original = _Request("POST", f"{LLMD}/chat/completions", {}, b"not json")
    _send(hook, original, TURN)
    assert hook.sent[0] is original


def test_requests_to_other_paths_are_not_rewritten(hook) -> None:
    original = _Request("POST", f"{LLMD}/embeddings", {}, json.dumps({"input": "x"}).encode())
    _send(hook, original, TURN)
    assert hook.sent[0] is original and original.headers["x-hermes-session-id"] == "agent-0007-g1"


# --- golden-snapshot warm-up turns are answered in-process


def test_a_warmup_session_is_answered_without_calling_llmd(hook) -> None:
    response = _send(hook, _chat({"model": "Qwen/Qwen3-32B", "messages": []}), {"x-hermes-session-id": "agent-warmup-a"})
    assert hook.sent == []
    assert response.status_code == 200
    assert response.json_body["model"] == "Qwen/Qwen3-32B"
    assert response.json_body["choices"][0]["message"]["content"] == "OK"


def test_a_streaming_warmup_turn_gets_an_sse_answer(hook) -> None:
    response = _send(hook, _chat({"model": "m", "stream": True, "messages": []}), {"x-hermes-session-id": "agent-warmup-a"})
    assert hook.sent == []
    assert response.headers["content-type"] == "text/event-stream"
    assert response.content.endswith(b"data: [DONE]\n\n")


def test_warmup_model_listing_reports_a_64k_context(hook) -> None:
    response = _send(hook, _Request("GET", f"{LLMD}/models"), {"x-hermes-session-id": "agent-warmup-a"})
    assert hook.sent == []
    assert response.json_body["data"][0]["max_model_len"] == 65536


def test_the_body_marker_short_circuits_only_while_warmup_is_running(hook) -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": "agent-warmup: say OK"}]}
    _send(hook, _chat(body), TURN)
    assert hook.sent == []

    hook.done_file.write_text("")
    _send(hook, _chat(body), TURN)
    assert len(hook.sent) == 1


# --- the async client


def test_async_send_reads_the_body_from_an_async_stream(hook) -> None:
    request = _chat({"model": "m", "messages": []}, async_only=True)
    _asend(hook, request, TURN)
    (sent,) = hook.sent
    assert json.loads(sent.read())["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent.headers["x-llm-d-inference-objective"] == "premium-traffic"


def test_async_warmup_turn_is_answered_from_an_async_stream(hook) -> None:
    request = _chat({"model": "Qwen/Qwen3-32B", "stream": True, "messages": []}, async_only=True)
    response = _asend(hook, request, {"x-hermes-session-id": "agent-warmup-b"})
    assert hook.sent == []
    assert response.headers["content-type"] == "text/event-stream"


def test_async_get_requests_do_not_read_a_body(hook) -> None:
    request = _Request("GET", f"{LLMD}/models", async_only=True)
    _asend(hook, request, TURN)
    assert hook.sent == [request]


# --- installing the hooks


def test_a_missing_optional_library_is_tolerated_silently(hook, capsys) -> None:
    # The fixture imported the module with aiohttp absent; httpx is still hooked.
    assert capsys.readouterr().err == ""
    assert sys.modules["httpx"].Client.send.__name__ == "send"
    _send(hook, _chat({"model": "m", "messages": []}), TURN)
    assert len(hook.sent) == 1


def test_a_hook_that_fails_to_install_is_reported_on_stderr(monkeypatch, tmp_path, capsys) -> None:
    broken = types.ModuleType("httpx")  # no Client / AsyncClient: the API the hook expects is gone
    monkeypatch.setitem(sys.modules, "httpx", broken)
    monkeypatch.setitem(sys.modules, "aiohttp", None)
    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", asyncio.BaseEventLoop.run_in_executor)
    spec = importlib.util.spec_from_file_location("llmd_sitecustomize_broken", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not raise
    err = capsys.readouterr().err
    assert "[llmd-sitecustomize] hook setup failed" in err and "Client" in err


def test_executor_calls_keep_the_turn_headers(hook) -> None:
    async def run():
        token = hook._TURN_HEADERS.set(TURN)
        try:
            return await asyncio.get_running_loop().run_in_executor(None, hook._TURN_HEADERS.get)
        finally:
            hook._TURN_HEADERS.reset(token)

    assert asyncio.run(run()) == TURN
