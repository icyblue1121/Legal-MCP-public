from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from legal_mcp import db


@dataclass(frozen=True)
class AgentConfig:
    enabled: bool
    model: str
    openai_base_url: str | None = None
    ai_provider: str = "openai_compatible"
    ai_model: str = "gpt-4.1-mini"
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_json_mode: str = "auto"
    public_agent_only: bool = False
    min_client_version: str | None = None
    langfuse_enabled: bool = False
    langfuse_base_url: str | None = None


def load_agent_config(database_path: str | Path | None = None) -> AgentConfig:
    stored = _load_database_agent_settings(database_path)
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LEGAL_MCP_AGENT_MODEL", "gpt-4.1-mini")
    ai_model = os.environ.get("LEGAL_MCP_AI_MODEL", stored.get("ai_model") or model)
    ai_base_url = os.environ.get(
        "LEGAL_MCP_AI_BASE_URL",
        os.environ.get("OPENAI_BASE_URL", stored.get("ai_base_url")),
    )
    ai_api_key = os.environ.get("LEGAL_MCP_AI_API_KEY", api_key or stored.get("ai_api_key"))
    ai_provider = os.environ.get(
        "LEGAL_MCP_AI_PROVIDER", stored.get("ai_provider") or "openai_compatible"
    )
    ai_json_mode = os.environ.get("LEGAL_MCP_AI_JSON_MODE", "auto")
    return AgentConfig(
        # A backend is usable when it has either credentials (cloud) or a base URL
        # (self-hosted endpoints like Ollama need no API key). This is what lets a
        # keyless local model drive the agent path, not just cloud OpenAI.
        enabled=ai_provider != "none" and bool(ai_api_key or ai_base_url),
        model=model,
        openai_base_url=os.environ.get("OPENAI_BASE_URL"),
        ai_provider=ai_provider,
        ai_model=ai_model,
        ai_base_url=ai_base_url,
        ai_api_key=ai_api_key,
        ai_json_mode=ai_json_mode,
        public_agent_only=_truthy(os.environ.get("LEGAL_MCP_AGENT_PUBLIC_ONLY")),
        min_client_version=os.environ.get("LEGAL_MCP_MIN_CLIENT_VERSION"),
        langfuse_enabled=bool(
            os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
        ),
        langfuse_base_url=os.environ.get("LANGFUSE_BASE_URL"),
    )


def _load_database_agent_settings(database_path: str | Path | None) -> dict[str, Any]:
    if database_path is None:
        return {}
    conn = db.connect(database_path)
    try:
        row = conn.execute(
            """
            select ai_provider, ai_model, ai_base_url, ai_api_key
            from agent_settings
            where id = 1
            """
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else {}


def _truthy(value: str | None) -> bool:
    return value is not None and value.lower() in {"1", "true", "yes", "on"}
