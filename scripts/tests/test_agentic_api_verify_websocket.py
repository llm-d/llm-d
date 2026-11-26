"""Check WebSocket transport selection in the agentic-api verification script."""

import importlib.util
import json
import ssl
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


VERIFY_PATH = Path(__file__).resolve().parents[2] / "guides/agentic-api/verify.py"
SPEC = importlib.util.spec_from_file_location("agentic_api_verify", VERIFY_PATH)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)

UPGRADE_RESPONSE = b"HTTP/1.1 101 Switching Protocols\r\n\r\n"
COMPLETED_EVENT = json.dumps({"type": "response.completed", "response": {"id": "response-1"}})


class WebSocketTransportTest(unittest.TestCase):
    def run_verification(self, base_url: str):
        raw_socket = MagicMock()
        raw_socket.recv.return_value = UPGRADE_RESPONSE
        tls_socket = MagicMock()
        tls_socket.recv.return_value = UPGRADE_RESPONSE
        context = MagicMock()
        context.wrap_socket.return_value = tls_socket

        with (
            patch.object(verify.socket, "create_connection", return_value=raw_socket) as connect,
            patch.object(verify.ssl, "create_default_context", return_value=context) as default_context,
            patch.object(verify, "_ws_recv_frame", return_value=COMPLETED_EVENT),
        ):
            verify.verify_websocket_responses(base_url, "test-model")

        return raw_socket, tls_socket, context, connect, default_context

    def test_https_uses_verified_tls_and_sni(self):
        raw, tls, context, connect, default_context = self.run_verification("https://gateway.example")
        connect.assert_called_once_with(("gateway.example", 443), timeout=120)
        default_context.assert_called_once_with()
        context.wrap_socket.assert_called_once_with(raw, server_hostname="gateway.example")
        raw.sendall.assert_not_called()
        self.assertIn(b"Upgrade: websocket", tls.sendall.call_args_list[0].args[0])
        tls.close.assert_called_once_with()

    def test_http_keeps_plain_socket(self):
        raw, _, _, connect, default_context = self.run_verification("http://gateway.example:9000")
        connect.assert_called_once_with(("gateway.example", 9000), timeout=120)
        default_context.assert_not_called()
        self.assertIn(b"Upgrade: websocket", raw.sendall.call_args_list[0].args[0])
        raw.close.assert_called_once_with()

    def test_tls_handshake_error_closes_socket(self):
        raw = MagicMock()
        context = MagicMock()
        context.wrap_socket.side_effect = ssl.SSLError("handshake failed")
        with (
            patch.object(verify.socket, "create_connection", return_value=raw),
            patch.object(verify.ssl, "create_default_context", return_value=context),
            self.assertRaises(ssl.SSLError),
        ):
            verify.verify_websocket_responses("https://gateway.example", "test-model")
        raw.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
