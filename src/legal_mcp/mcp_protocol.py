"""Shared JSON-RPC MCP protocol handling for Legal-MCP transports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legal_mcp import __version__
from legal_mcp.connector_config import ConnectorSetup
from legal_mcp.policy import AccessContext
from legal_mcp.tool_catalog import tool_definitions
from legal_mcp.tools import call_tool

PROTOCOL_VERSION = "2024-11-05"


def handle_message(
    message: dict[str, Any],
    *,
    database_path: str | Path,
    audit_path: str | Path,
    access_context: AccessContext | None = None,
    public_agent_only: bool = False,
    internal_debug: bool = False,
    connector_setup: ConnectorSetup | None = None,
) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "legal-mcp", "version": __version__},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": tool_definitions(
                    public_agent_only=public_agent_only,
                    internal_debug=internal_debug,
                )
            },
        }
    if method == "tools/call":
        params = message.get("params") or {}
        tool_name = params.get("name", "")
        if not _tool_is_exposed(
            tool_name,
            public_agent_only=public_agent_only,
            internal_debug=internal_debug,
        ):
            result = _error(
                "tool_not_exposed",
                "tool is not exposed by this Legal-MCP endpoint; update the client and use tools/list",
            )
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, sort_keys=True),
                        }
                    ],
                    "isError": True,
                },
            }
        result = call_tool(
            tool_name,
            params.get("arguments") or {},
            database_path=database_path,
            audit_path=audit_path,
            access_context=access_context,
            connector_setup=connector_setup,
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, sort_keys=True),
                    }
                ],
                "isError": "error" in result,
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def _tool_is_exposed(
    tool_name: Any,
    *,
    public_agent_only: bool,
    internal_debug: bool,
) -> bool:
    if not isinstance(tool_name, str):
        return False
    return tool_name in {
        tool["name"]
        for tool in tool_definitions(
            public_agent_only=public_agent_only,
            internal_debug=internal_debug,
        )
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "candidates": [], "details": {}}}
