import json
import os
import subprocess
import sys

from legal_mcp import db


def encode_message(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def decode_messages(data: bytes) -> list[dict]:
    messages = []
    while data:
        header, body_start = data.split(b"\r\n\r\n", 1)
        content_length = int(header.decode("ascii").split(": ", 1)[1])
        body = body_start[:content_length]
        messages.append(json.loads(body))
        data = body_start[content_length:]
    return messages


def encode_jsonl_message(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8") + b"\n"


def decode_jsonl_messages(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.splitlines() if line]


def test_stdio_server_lists_tools_and_rejects_unlisted_database_tool(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            "insert into projects (project_code, name, stage) values (?, ?, ?)",
            ("GAME-001", "Project One", "live"),
        )
        conn.commit()
    finally:
        conn.close()

    request_bytes = b"".join(
        [
            encode_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                }
            ),
            encode_message({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            encode_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            encode_message(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "get_project_fields",
                        "arguments": {
                            "project_id_or_name": "GAME-001",
                            "fields": ["project_code"],
                            "rationale": "contract review",
                        },
                    },
                }
            ),
        ]
    )

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "legal_mcp.mcp_server",
            "--db",
            str(database_path),
            "--audit-log",
            str(audit_path),
        ],
        input=request_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    responses = decode_messages(process.stdout)
    tools_response = next(response for response in responses if response.get("id") == 2)
    assert [tool["name"] for tool in tools_response["result"]["tools"]] == ["agent_query"]

    call_response = next(response for response in responses if response.get("id") == 3)
    content = json.loads(call_response["result"]["content"][0]["text"])
    assert call_response["result"]["isError"] is True
    assert content["error"]["code"] == "tool_not_exposed"


def test_stdio_server_supports_jsonl_framing(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    db.initialize_database(database_path)

    request_bytes = b"".join(
        [
            encode_jsonl_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                }
            ),
            encode_jsonl_message({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            encode_jsonl_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]
    )

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "legal_mcp.mcp_server",
            "--db",
            str(database_path),
            "--audit-log",
            str(audit_path),
        ],
        input=request_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    responses = decode_jsonl_messages(process.stdout)
    assert responses[0]["result"]["serverInfo"]["name"] == "legal-mcp"
    tools_response = next(response for response in responses if response.get("id") == 2)
    assert [tool["name"] for tool in tools_response["result"]["tools"]] == ["agent_query"]


def test_stdio_server_can_expose_only_agent_query_with_env(tmp_path) -> None:
    database_path = tmp_path / "legal.db"
    audit_path = tmp_path / "audit.jsonl"
    db.initialize_database(database_path)
    request_bytes = encode_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    env = {**os.environ, "LEGAL_MCP_AGENT_PUBLIC_ONLY": "true"}

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "legal_mcp.mcp_server",
            "--db",
            str(database_path),
            "--audit-log",
            str(audit_path),
        ],
        input=request_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=env,
    )

    responses = decode_messages(process.stdout)
    tools_response = next(response for response in responses if response.get("id") == 2)
    assert [tool["name"] for tool in tools_response["result"]["tools"]] == ["agent_query"]
