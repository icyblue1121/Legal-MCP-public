"""Minimal stdio MCP server for Legal-MCP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import BinaryIO, TextIO

from legal_mcp.agent_config import load_agent_config
from legal_mcp.audit import DEFAULT_AUDIT_PATH
from legal_mcp.cli import DEFAULT_DATABASE_PATH
from legal_mcp.connector_config import ConnectorSetup
from legal_mcp.mcp_protocol import handle_message
from legal_mcp.policy import AccessContext
from legal_mcp.startup import require_startup_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="legal-mcp serve")
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--audit-log", type=Path, default=DEFAULT_AUDIT_PATH)
    parser.add_argument("--update-check-url")
    parser.add_argument(
        "--agent-public-only",
        action="store_true",
        help="Expose only the agent_query tool in tools/list.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serve(
        args.db,
        args.audit_log,
        sys.stdin.buffer,
        sys.stdout.buffer,
        sys.stderr,
        update_check_url=args.update_check_url,
        public_agent_only=True if args.agent_public_only else None,
    )
    return 0


def serve(
    database_path: str | Path,
    audit_path: str | Path,
    stdin: BinaryIO,
    stdout: BinaryIO,
    stderr: TextIO,
    *,
    update_check_url: str | None = None,
    public_agent_only: bool | None = None,
    connector_setup: ConnectorSetup | None = None,
) -> None:
    require_startup_checks(database_path, remote_url=update_check_url)
    resolved_public_agent_only = (
        load_agent_config(database_path).public_agent_only
        if public_agent_only is None
        else public_agent_only
    )
    framing: str | None = None
    while True:
        read_result = _read_message(stdin, framing)
        if read_result is None:
            return
        message, framing = read_result
        response = handle_message(
            message,
            database_path=database_path,
            audit_path=audit_path,
            # The stdio server is the local trusted operator with no network
            # identity layer. Make that full access explicit rather than relying on
            # the fail-closed ``access_context is None`` default.
            access_context=AccessContext.local_operator(),
            public_agent_only=resolved_public_agent_only,
            connector_setup=connector_setup,
        )
        if response is not None:
            _write_message(stdout, response, framing)
            stdout.flush()


def _read_message(stdin: BinaryIO, framing: str | None = None) -> tuple[dict, str] | None:
    if framing == "jsonl":
        return _read_jsonl_message(stdin)
    if framing == "content-length":
        return _read_content_length_message(stdin)

    first_line = stdin.readline()
    if first_line == b"":
        return None
    if first_line.lstrip().startswith(b"{"):
        return json.loads(first_line), "jsonl"
    return _read_content_length_message(stdin, first_line=first_line)

def _read_jsonl_message(stdin: BinaryIO) -> tuple[dict, str] | None:
    line = stdin.readline()
    if line == b"":
        return None
    return json.loads(line), "jsonl"


def _read_content_length_message(
    stdin: BinaryIO,
    *,
    first_line: bytes | None = None,
) -> tuple[dict, str] | None:
    headers = {}
    line = first_line
    while True:
        if line is None:
            line = stdin.readline()
        if line == b"":
            return None
        if line in {b"\r\n", b"\n"}:
            break
        name, value = line.decode("ascii").strip().split(":", 1)
        headers[name.lower()] = value.strip()
        line = None

    content_length = int(headers["content-length"])
    body = stdin.read(content_length)
    if not body:
        return None
    return json.loads(body), "content-length"


def _write_message(stdout: BinaryIO, message: dict, framing: str = "content-length") -> None:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if framing == "jsonl":
        stdout.write(body + b"\n")
    else:
        stdout.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)


if __name__ == "__main__":
    raise SystemExit(main())
