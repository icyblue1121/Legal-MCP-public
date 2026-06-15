"""stdio-to-HTTP proxy for shared Legal-MCP team deployments."""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any, BinaryIO

from legal_mcp import __version__
from legal_mcp.mcp_server import _read_message, _write_message


def forward_message(
    message: dict[str, Any],
    *,
    url: str,
    token: str,
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Legal-MCP-Client-Version": __version__,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def proxy_stdio(
    *,
    url: str,
    token: str,
    timeout: float = 30,
    stdin: BinaryIO = sys.stdin.buffer,
    stdout: BinaryIO = sys.stdout.buffer,
) -> None:
    framing: str | None = None
    while True:
        read_result = _read_message(stdin, framing)
        if read_result is None:
            return
        message, framing = read_result
        response = forward_message(message, url=url, token=token, timeout=timeout)
        if response:
            _write_message(stdout, response, framing)
            stdout.flush()
