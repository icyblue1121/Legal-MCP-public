"""Local JSONL audit logging for MCP tool calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_PATH = Path.home() / ".legal-mcp" / "audit.jsonl"


def write_audit_record(
    *,
    tool_name: str,
    rationale: str | None,
    source_client: str | None,
    arguments: dict[str, Any],
    result_status: str,
    error_code: str | None,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
) -> None:
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name,
        "rationale": rationale,
        "source_client": source_client,
        "arguments_summary": summarize_arguments(arguments),
        "result_status": result_status,
        "error_code": error_code,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def summarize_arguments(arguments: dict[str, Any]) -> str:
    safe_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"rationale", "source_client"}
    }
    summary = json.dumps(safe_arguments, ensure_ascii=False, sort_keys=True)
    if len(summary) > 300:
        return summary[:297] + "..."
    return summary
