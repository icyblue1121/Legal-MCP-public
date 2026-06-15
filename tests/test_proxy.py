from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch

from legal_mcp import __version__
from legal_mcp.proxy import forward_message, proxy_stdio


class _FakeResponse:
    status = 200

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_forward_message_posts_json_rpc_to_remote_mcp() -> None:
    remote_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": "list_projects"}]},
    }

    with patch("legal_mcp.proxy.urllib.request.urlopen", return_value=_FakeResponse(remote_response)) as urlopen:
        response = forward_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            url="http://legal.internal:8765/mcp",
            token="secret-token",
            timeout=5,
        )

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://legal.internal:8765/mcp"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["X-legal-mcp-client-version"] == __version__
    assert json.loads(request.data.decode("utf-8"))["method"] == "tools/list"
    assert response == remote_response


def test_forward_message_preserves_json_rpc_error_payload() -> None:
    remote_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "method not found"},
    }

    with patch("legal_mcp.proxy.urllib.request.urlopen", return_value=_FakeResponse(remote_response)):
        response = forward_message(
            {"jsonrpc": "2.0", "id": 1, "method": "missing"},
            url="http://legal.internal:8765/mcp",
            token="secret-token",
            timeout=5,
        )

    assert response["error"]["code"] == -32601


def test_proxy_stdio_does_not_write_empty_http_response_for_notification() -> None:
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    stdin = BytesIO(json.dumps(notification).encode("utf-8") + b"\n")
    stdout = BytesIO()

    with patch("legal_mcp.proxy.forward_message", return_value={}):
        proxy_stdio(
            url="http://legal.internal:8765/mcp",
            token="secret-token",
            stdin=stdin,
            stdout=stdout,
        )

    assert stdout.getvalue() == b""
