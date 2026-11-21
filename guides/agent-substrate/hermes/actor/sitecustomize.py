"""Propagate Hermes session and llm-d tier headers directly to the llm-d Router.

Upstream Hermes Agent (`gateway/platforms/api_server.py`) uses `X-Hermes-Session-Id`
to select its local SQLite conversation store, then issues an OpenAI-compatible
chat completion via `httpx`. This lightweight `sitecustomize` hook lets Hermes
sandboxes talk directly to the llm-d Router (`MODEL_BASE_URL`) with no proxy:
  1. Answers `agent-warmup` golden-snapshot turns in-process inside `httpx` so
     no GPU/TPU capacity is needed at snapshot build time and zero TCP sockets
     are frozen into the gVisor checkpoint. Session IDs starting with
     `agent-warmup` are reserved for this. While the warm-up is still running
     (until `warmup.py` writes `WARMUP_DONE_FILE`), any request whose body
     carries the marker is answered the same way; afterwards a real
     conversation that merely mentions the marker is sent to llm-d as usual.
  2. Captures validated inbound `X-Hermes-Session-Id` and
     `x-llm-d-inference-objective` headers on the `aiohttp` request context
     (including worker-thread handoffs via `run_in_executor`) and attaches them
     to outbound `httpx` requests to llm-d.
  3. Defaults `chat_template_kwargs.enable_thinking = False` on outbound
     `/chat/completions` payloads so reasoning-default models (`Qwen/Qwen3-32B`)
     emit concise visible replies within per-turn `max_tokens` budgets.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import os
import re
import time

_WARMUP_MARKER = "agent-warmup"
_WARMUP_BYTES = _WARMUP_MARKER.encode("utf-8")
# Written by warmup.py when the warm-up turns are done. It is part of the golden
# snapshot's root filesystem, so it exists in every actor restored from it.
_WARMUP_DONE_FILE = os.environ.get("WARMUP_DONE_FILE", "/tmp/agent-warmup-done")
_SAFE_HEADER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_FORWARDED_HEADERS = (
    "x-hermes-session-id",
    "x-llm-d-inference-objective",
)
_TURN_HEADERS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "llmd_hermes_turn_headers", default={}
)


def _sanitize_headers(raw_headers) -> dict[str, str]:
    extracted: dict[str, str] = {}
    for name in _FORWARDED_HEADERS:
        val = (raw_headers.get(name) or "").strip()
        if val and _SAFE_HEADER_RE.fullmatch(val):
            extracted[name] = val
    return extracted


def _install_context_executor_propagation() -> None:
    orig_run_in_executor = asyncio.BaseEventLoop.run_in_executor

    @functools.wraps(orig_run_in_executor)
    def _wrapped_run_in_executor(self, executor, func, *args):
        ctx = contextvars.copy_context()
        return orig_run_in_executor(self, executor, ctx.run, func, *args)

    asyncio.BaseEventLoop.run_in_executor = _wrapped_run_in_executor  # type: ignore[assignment]


def _install_aiohttp_hook() -> None:
    import aiohttp.web  # type: ignore[import-not-found]

    @aiohttp.web.middleware
    async def _llmd_header_middleware(request, handler):
        headers = _sanitize_headers(request.headers)
        if not headers:
            return await handler(request)
        token = _TURN_HEADERS.set(headers)
        try:
            return await handler(request)
        finally:
            _TURN_HEADERS.reset(token)

    orig_init = aiohttp.web.Application.__init__

    @functools.wraps(orig_init)
    def _wrapped_init(self, *args, **kwargs):
        middlewares = list(kwargs.get("middlewares") or ())
        middlewares.insert(0, _llmd_header_middleware)
        kwargs["middlewares"] = middlewares
        orig_init(self, *args, **kwargs)

    aiohttp.web.Application.__init__ = _wrapped_init  # type: ignore[assignment]


def _make_warmup_response(httpx_mod, request):
    now = int(time.time())
    default_model = os.environ.get("MODEL_NAME", "Qwen/Qwen3-32B")
    if request.method == "GET" and request.url.path.endswith("/models"):
        return httpx_mod.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "object": "list",
                "data": [
                    {
                        "id": default_model,
                        "object": "model",
                        "owned_by": "llm-d",
                        "max_model_len": 65536,
                    }
                ],
            },
            request=request,
        )

    stream = False
    model = default_model
    try:
        parsed = json.loads(request.read().decode("utf-8"))
        if isinstance(parsed, dict):
            stream = bool(parsed.get("stream"))
            model = str(parsed.get("model") or default_model)
    except Exception:
        pass

    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    if stream:
        first = json.dumps(
            {
                "id": f"chatcmpl-warmup-{now}",
                "object": "chat.completion.chunk",
                "created": now,
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": "OK"}}
                ],
            }
        )
        final = json.dumps(
            {
                "id": f"chatcmpl-warmup-{now}",
                "object": "chat.completion.chunk",
                "created": now,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": usage,
            }
        )
        sse = f"data: {first}\n\ndata: {final}\n\ndata: [DONE]\n\n".encode("utf-8")
        return httpx_mod.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse,
            request=request,
        )

    return httpx_mod.Response(
        200,
        headers={"content-type": "application/json"},
        json={
            "id": f"chatcmpl-warmup-{now}",
            "object": "chat.completion",
            "created": now,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "OK"},
                }
            ],
            "usage": usage,
        },
        request=request,
    )


def _prepare_or_short_circuit(httpx_mod, request):
    turn_headers = _TURN_HEADERS.get()
    for k, v in turn_headers.items():
        if k not in request.headers:
            request.headers[k] = v

    raw_body = request.read() if request.method == "POST" else b""
    if turn_headers.get("x-hermes-session-id", "").startswith(_WARMUP_MARKER) or (
        raw_body and _WARMUP_BYTES in raw_body and not os.path.exists(_WARMUP_DONE_FILE)
    ):
        return request, _make_warmup_response(httpx_mod, request)

    if request.method == "POST" and request.url.path.endswith("/chat/completions"):
        try:
            payload = json.loads(raw_body.decode("utf-8"))
            if isinstance(payload, dict):
                ct_kwargs = payload.get("chat_template_kwargs")
                if not isinstance(ct_kwargs, dict):
                    ct_kwargs = {}
                if "enable_thinking" not in ct_kwargs:
                    ct_kwargs["enable_thinking"] = False
                    payload["chat_template_kwargs"] = ct_kwargs
                    new_body = json.dumps(payload).encode("utf-8")
                    headers = {
                        k: v
                        for k, v in request.headers.items()
                        if k.lower() != "content-length"
                    }
                    request = httpx_mod.Request(
                        method=request.method,
                        url=request.url,
                        headers=headers,
                        content=new_body,
                        extensions=request.extensions,
                    )
        except Exception:
            pass
    return request, None


def _install_httpx_hook() -> None:
    import httpx  # type: ignore[import-not-found]

    orig_send = httpx.Client.send
    orig_async_send = httpx.AsyncClient.send

    @functools.wraps(orig_send)
    def _wrapped_send(self, request, *args, **kwargs):
        request, warmup_resp = _prepare_or_short_circuit(httpx, request)
        if warmup_resp is not None:
            return warmup_resp
        return orig_send(self, request, *args, **kwargs)

    @functools.wraps(orig_async_send)
    async def _wrapped_async_send(self, request, *args, **kwargs):
        request, warmup_resp = _prepare_or_short_circuit(httpx, request)
        if warmup_resp is not None:
            return warmup_resp
        return await orig_async_send(self, request, *args, **kwargs)

    httpx.Client.send = _wrapped_send  # type: ignore[assignment]
    httpx.AsyncClient.send = _wrapped_async_send  # type: ignore[assignment]


try:
    _install_context_executor_propagation()
    _install_aiohttp_hook()
    _install_httpx_hook()
except Exception:
    # Never interfere with non-gateway Python invocations inside the container.
    pass
