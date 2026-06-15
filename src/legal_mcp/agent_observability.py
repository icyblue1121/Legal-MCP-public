from __future__ import annotations

import os
from typing import Any


def langfuse_callbacks() -> list[Any]:
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return []
    if not os.environ.get("LANGFUSE_BASE_URL"):
        return []

    try:
        from langfuse.langchain import CallbackHandler
    except ModuleNotFoundError as exc:
        if exc.name == "langchain" or "langchain" in str(exc):
            return []
        raise

    return [CallbackHandler()]


def flush_langfuse() -> None:
    try:
        from langfuse import get_client
    except ModuleNotFoundError:
        return
    get_client().flush()


def build_trace_metadata(
    *,
    thread_id: str,
    tool_name: str | None,
    status: str,
    user_id: str | None = None,
    result: dict[str, Any] | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    # v0.4.6 §B: ``thread_id`` is the client *conversation* id and becomes the
    # Langfuse session that groups every turn; ``turn_id`` (one agent_query
    # invocation) is the per-turn discriminator carried on the trace, so two turns
    # of one conversation are one session with two distinct traces.
    metadata = {
        "thread_id": thread_id,
        "status": status,
        "feature": "agent_query",
        "langfuse_session_id": thread_id,
        "langfuse_trace_name": (
            f"legal-mcp-agent-query:{turn_id}" if turn_id else "legal-mcp-agent-query"
        ),
        "langfuse_tags": ["legal-mcp", "agent_query", status],
    }
    if turn_id is not None:
        metadata["turn_id"] = turn_id
    if tool_name is not None:
        metadata["tool_name"] = tool_name
    if user_id is not None:
        metadata["langfuse_user_id"] = user_id
    if result and isinstance(result.get("error"), dict):
        metadata["error_code"] = result["error"].get("code")
    return metadata
