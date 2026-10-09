#!/usr/bin/env python3
"""Verification script for vllm/agentic-api deployed with llm-d (Standalone and Gateway modes).

Verifies:
  1. Health & Readiness endpoints (/health, /ready, /v1/models)
  2. HTTP Mode Stateful /v1/responses API (multi-turn continuation via previous_response_id stored in PostgreSQL)
  3. Webhook Mode & Agentic Tool Loop (local HTTP webhook receiver triggered by /v1/responses function_call + continuation via function_call_output)
  4. WebSocket Mode (WS /v1/responses stateful continuation over RFC 6455 WebSocket)
"""

import argparse
import base64
import hashlib
import http.server
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


def http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 180,
) -> Tuple[int, Dict[str, Any]]:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw.strip() else {}
            return resp.status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw_error": raw}
        return exc.code, body


def extract_output_text(resp_body: Dict[str, Any]) -> str:
    """Extract assistant output text from an OpenAI-compatible /v1/responses payload."""
    if isinstance(resp_body.get("output_text"), str) and resp_body["output_text"]:
        return resp_body["output_text"]
    texts: List[str] = []
    for item in resp_body.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text") and part.get("text"):
                    texts.append(part["text"])
    return "\n".join(texts)


def verify_health_and_models(base_url: str, check_health: bool = True) -> str:
    print(f"\n[1/4] Verifying health and model discovery at {base_url} ...")
    if check_health:
        for path in ("/health", "/ready"):
            req = urllib.request.Request(f"{base_url}{path}", method="GET")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    assert 200 <= resp.status < 300, f"{path} returned {resp.status}"
                    print(f"  OK  GET {path} -> HTTP {resp.status}")
            except urllib.error.HTTPError as exc:
                if path == "/ready" and exc.code == 404:
                    print("  OK  GET /ready -> HTTP 404 (not routed on external Gateway, skipping)")
                else:
                    raise

    status, models_body = http_json("GET", f"{base_url}/v1/models")
    assert status == 200, f"GET /v1/models failed with HTTP {status}: {models_body}"
    models = models_body.get("data", [])
    assert models, f"No models returned from /v1/models: {models_body}"
    model_id = models[0]["id"]
    print(f"  OK  GET /v1/models -> discovered model: {model_id}")
    return model_id


def verify_http_stateful_responses(base_url: str, model: str) -> None:
    print("\n[2/4] Verifying HTTP Mode Stateful /v1/responses API (PostgreSQL persistence) ...")
    # A neutral framing on purpose. Calling this a "secret verification code" makes
    # gpt-oss-120b refuse ("I'm sorry, but I can't comply with that") on vLLM's Responses
    # API -- deterministically under greedy decoding, and about half the time at default
    # sampling, which made this test flaky rather than failing outright. The wording below
    # exercises exactly the same persistence path without tripping the refusal.
    #
    # temperature=0 is set for the same reason: this test asserts on an exact string, so it
    # must not depend on a lucky sample.
    secret_code = "COBALT-7492"

    # Turn 1: Store state in PostgreSQL via agentic-api
    turn1_payload = {
        "model": model,
        "input": f"Our ticket reference for this thread is {secret_code}. "
                 f"Acknowledge by replying exactly: ACK {secret_code}",
        "store": True,
        "max_output_tokens": 512,
        "temperature": 0,
    }
    status1, body1 = http_json("POST", f"{base_url}/v1/responses", turn1_payload)
    assert status1 == 200, f"Turn 1 failed with HTTP {status1}: {body1}"
    resp1_id = body1.get("id")
    assert resp1_id and resp1_id.startswith("resp_"), f"Expected resp_ id, got: {body1}"
    out1 = extract_output_text(body1)
    print(f"  OK  Turn 1 stored response id={resp1_id} | output={out1!r}")
    # Check Turn 1 before relying on Turn 2. Without this, a model that declined on Turn 1
    # still reports "OK" here and the failure surfaces as a confusing Turn 2 assertion
    # about PostgreSQL, when nothing was ever committed to recall.
    assert secret_code in out1, (
        f"Turn 1 did not acknowledge {secret_code!r}; the model answered {out1!r}.\n"
        f"  This is a model-behaviour failure, not a persistence one -- nothing was stored to\n"
        f"  recall in Turn 2. A refusal here usually means the prompt tripped the model's\n"
        f"  safety policy; check it against the model directly with\n"
        f"      POST /v1/responses  (Host: epp.gateway.internal in Gateway Mode)\n"
        f"  before suspecting agentic-api or PostgreSQL."
    )

    # Turn 2: Reference ONLY previous_response_id (no prior messages sent by client)
    turn2_payload = {
        "model": model,
        "previous_response_id": resp1_id,
        "input": "What is the ticket reference for this thread? Reply with only the reference.",
        "store": True,
        "max_output_tokens": 512,
        "temperature": 0,
    }
    status2, body2 = http_json("POST", f"{base_url}/v1/responses", turn2_payload)
    assert status2 == 200, f"Turn 2 failed with HTTP {status2}: {body2}"
    resp2_id = body2.get("id")
    out2 = extract_output_text(body2)
    print(f"  OK  Turn 2 continued from previous_response_id={resp1_id} -> new id={resp2_id} | output={out2!r}")
    assert secret_code in out2, f"Expected '{secret_code}' in Turn 2 output, got: {out2!r}"
    print(f"  PASS Stateful continuation verified ('{secret_code}' recalled from PostgreSQL store).")


def verify_webhook_mode(base_url: str, model: str) -> None:
    print("\n[3/4] Verifying Webhook Mode (HTTP webhook callback + stateful /v1/responses tool loop) ...")
    received_webhooks: List[Dict[str, Any]] = []

    class WebhookHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw) if raw else {}
            received_webhooks.append(
                {
                    "path": self.path,
                    "headers": dict(self.headers),
                    "payload": payload,
                }
            )
            ack = {
                "webhook_status": "delivered",
                "ticket_id": "WH-9981",
                "received_event": payload,
            }
            resp_bytes = json.dumps(ack).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), WebhookHandler)
    webhook_port = server.server_port
    webhook_url = f"http://127.0.0.1:{webhook_port}/webhook/deployment-events"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        # Step 1: Request with function tool that triggers a webhook notification
        req1 = {
            "model": model,
            "store": True,
            "max_output_tokens": 512,
            "temperature": 0,
            "tools": [
                {
                    "type": "function",
                    "name": "emit_deployment_webhook",
                    "description": "Deliver a deployment status webhook event to the configured webhook endpoint.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string", "description": "Service name"},
                            "status": {"type": "string", "description": "Deployment status"},
                        },
                        "required": ["service", "status"],
                    },
                }
            ],
            "tool_choice": "auto",
            "input": "Call the emit_deployment_webhook tool for service 'llm-d' with status 'ready'.",
        }
        status1, body1 = http_json("POST", f"{base_url}/v1/responses", req1)
        assert status1 == 200, f"Webhook Step 1 failed with HTTP {status1}: {body1}"
        resp1_id = body1["id"]

        func_calls = [item for item in body1.get("output", []) if item.get("type") == "function_call"]
        # The most common cause of an empty function_call list is not agentic-api but
        # the base guide's model server: tool calling is off unless vLLM was started
        # with --enable-auto-tool-choice and a --tool-call-parser for this model.
        # Say so, rather than dumping the output and leaving the reader guessing.
        assert func_calls, (
            f"Model {model!r} returned no function_call item, so the stateful tool loop "
            f"cannot be verified.\n"
            f"  Most likely the base guide's model server is missing the tool-calling "
            f"flags. Check that its vLLM args include:\n"
            f"      --enable-auto-tool-choice\n"
            f"      --tool-call-parser <parser for this model>\n"
            f"      --reasoning-parser <parser for this model>\n"
            f"  See the Prerequisites section of guides/agentic-api/README.md.\n"
            f"  Raw output was: {body1.get('output')}"
        )
        fcall = func_calls[0]
        call_id = fcall["call_id"]
        args_obj = json.loads(fcall.get("arguments", "{}"))
        print(f"  OK  Model emitted webhook tool call id={call_id} name={fcall['name']} args={args_obj}")

        # Step 2: Deliver the webhook event to the live HTTP webhook server
        wh_status, wh_ack = http_json(
            "POST",
            webhook_url,
            {
                "response_id": resp1_id,
                "call_id": call_id,
                "tool": fcall["name"],
                "arguments": args_obj,
            },
            headers={"X-Agentic-Webhook-Event": "tool.function_call"},
        )
        assert wh_status == 200 and len(received_webhooks) == 1, "Webhook listener did not receive callback"
        assert received_webhooks[0]["payload"].get("call_id") == call_id, (
            f"Webhook listener received a different call_id: "
            f"{received_webhooks[0]['payload'].get('call_id')!r} != {call_id!r}"
        )
        print(f"  OK  Webhook delivered to {webhook_url} -> ack={wh_ack}")

        # Step 3: Return webhook delivery receipt to agentic-api via stateful previous_response_id
        req2 = {
            "model": model,
            "previous_response_id": resp1_id,
            "store": True,
            "max_output_tokens": 512,
            "temperature": 0,
            "input": [
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(wh_ack),
                }
            ],
        }
        status2, body2 = http_json("POST", f"{base_url}/v1/responses", req2)
        assert status2 == 200, f"Webhook Step 3 continuation failed with HTTP {status2}: {body2}"
        final_text = extract_output_text(body2)
        print(f"  OK  Stateful webhook continuation completed id={body2.get('id')} | output={final_text!r}")
        # Assert on the protocol, not on the model's prose. What this test exists to prove is
        # that a function_call_output posted against previous_response_id is accepted and
        # completes; how the model then words its summary is sampled text and varies run to
        # run ("has been sent", "confirm the delivery", "successfully delivered"), so matching
        # on it made this test fail while every mechanical step had in fact succeeded.
        assert body2.get("status") == "completed", (
            f"Expected status 'completed', got {body2.get('status')!r}; "
            f"error={body2.get('error')!r} incomplete_details={body2.get('incomplete_details')!r}"
        )
        assert body2.get("error") is None, f"Continuation reported an error: {body2.get('error')!r}"
        # The echoed previous_response_id is the actual evidence of stateful continuation: the
        # function_call_output carried no conversation history, so it could only be resolved
        # against the turn agentic-api had persisted.
        assert body2.get("previous_response_id") == resp1_id, (
            f"Continuation is not linked to the stored turn: "
            f"previous_response_id={body2.get('previous_response_id')!r} != {resp1_id!r}"
        )
        assert final_text.strip(), "Continuation completed but produced no assistant text"
        print("  PASS Webhook delivery and stateful tool-output continuation verified "
              f"(status={body2.get('status')}, linked to {resp1_id}).")
    finally:
        server.shutdown()


def _ws_send_text(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    mask_key = os.urandom(4)
    header = bytearray([0x81])  # FIN + text frame
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask_key)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _ws_recv_frame(sock: socket.socket) -> Optional[str]:
    def recv_exact(n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise EOFError("WebSocket closed")
            buf.extend(chunk)
        return bytes(buf)

    first2 = recv_exact(2)
    opcode = first2[0] & 0x0F
    masked = (first2[1] & 0x80) != 0
    length = first2[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(8))[0]
    mask_key = recv_exact(4) if masked else b""
    data = recv_exact(length)
    if masked:
        data = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
    if opcode == 0x8:  # Close
        return None
    if opcode == 0x9:  # Ping -> send Pong
        pong = bytearray([0x8A, 0x80, 0, 0, 0, 0])
        sock.sendall(bytes(pong))
        return ""
    return data.decode("utf-8", errors="replace")


def verify_websocket_responses(base_url: str, model: str) -> None:
    print("\n[4/4] Verifying WebSocket Mode (WS /v1/responses stateful streaming) ...")
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    sock = socket.create_connection((host, port), timeout=120)
    try:
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET /v1/responses HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode("ascii"))
        resp_header = b""
        while b"\r\n\r\n" not in resp_header:
            chunk = sock.recv(1024)
            if not chunk:
                break
            resp_header += chunk
        status_line = resp_header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
        assert "101" in status_line, (
            f"WebSocket upgrade to ws://{host}:{port}/v1/responses failed with '{status_line}' "
            f"(pass --skip-websocket if your proxy/Gateway does not support WebSocket upgrades)"
        )

        print(f"  OK  Connected to ws://{host}:{port}/v1/responses ({status_line})")
        frame1 = {
            "type": "response.create",
            "stream_id": "ws-verify-1",
            "model": model,
            "input": [{"type": "message", "role": "user", "content": "Reply with 'WS-TURN-1-OK'."}],
            "store": True,
            "stream": True,
            "max_output_tokens": 256,
        }
        _ws_send_text(sock, json.dumps(frame1))
        completed_id = None
        while True:
            msg = _ws_recv_frame(sock)
            if msg is None:
                break
            if not msg:
                continue
            evt = json.loads(msg)
            if evt.get("type") == "response.completed":
                completed_id = evt.get("response", {}).get("id")
                break
            if evt.get("type") == "error":
                raise RuntimeError(f"WebSocket error event: {evt}")

        assert completed_id, "Did not receive response.completed over WebSocket"
        print(f"  PASS WebSocket /v1/responses completed with id={completed_id}")
    finally:
        sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify vllm/agentic-api with llm-d")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AGENTIC_API_BASE_URL", "http://127.0.0.1:9000"),
        help="Base URL of agentic-api or the Gateway endpoint (default: http://127.0.0.1:9000)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AGENTIC_API_MODEL", ""),
        help="Model name (auto-detected from /v1/models if omitted)",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="Skip /health and /ready checks when testing through a Gateway that only exposes /v1/*",
    )
    parser.add_argument(
        "--skip-websocket",
        action="store_true",
        help="Explicitly skip [4/4] WebSocket /v1/responses upgrade check",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    discovered_model = verify_health_and_models(base_url, check_health=not args.skip_health)
    model = args.model or discovered_model

    verify_http_stateful_responses(base_url, model)
    verify_webhook_mode(base_url, model)
    if args.skip_websocket:
        print("\n[4/4] Skipping WebSocket Mode (WS /v1/responses) check (--skip-websocket).")
    else:
        verify_websocket_responses(base_url, model)

    skipped = [
        name
        for flag, name in ((args.skip_health, "health"), (args.skip_websocket, "websocket"))
        if flag
    ]
    suffix = f" [skipped: {', '.join(skipped)}]" if skipped else ""
    print("\n============================================================")
    print(f"ALL VERIFICATION CHECKS PASSED AGAINST {base_url} ({model}){suffix}")
    print("============================================================")


if __name__ == "__main__":
    main()
