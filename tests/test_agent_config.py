from __future__ import annotations

from pathlib import Path

import pytest

from legal_mcp import db
from legal_mcp.agent_config import AgentConfig, load_agent_config


def test_load_agent_config_defaults_to_disabled_without_backend(monkeypatch) -> None:
    # Disabled means no usable backend at all: neither credentials nor a base URL.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LEGAL_MCP_AI_BASE_URL", raising=False)
    monkeypatch.delenv("LEGAL_MCP_AI_API_KEY", raising=False)
    monkeypatch.delenv("LEGAL_MCP_AGENT_MODEL", raising=False)

    config = load_agent_config()

    assert config.enabled is False
    assert config.model == "gpt-4.1-mini"


def test_keyless_local_endpoint_is_enabled(monkeypatch) -> None:
    # A self-hosted endpoint (Ollama/vLLM) needs no API key — a base URL alone
    # must count as enabled so a local model can drive the agent path.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LEGAL_MCP_AI_API_KEY", raising=False)
    monkeypatch.setenv("LEGAL_MCP_AI_BASE_URL", "http://localhost:11434/v1")

    config = load_agent_config()

    assert config.enabled is True
    assert config.ai_base_url == "http://localhost:11434/v1"
    assert config.ai_api_key is None


def test_load_agent_config_reads_openai_compatible_settings(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:4000/v1")
    monkeypatch.setenv("LEGAL_MCP_AGENT_MODEL", "local-router")
    monkeypatch.setenv("LEGAL_MCP_AGENT_PUBLIC_ONLY", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:3000")

    config = load_agent_config()

    assert config == AgentConfig(
        enabled=True,
        model="local-router",
        openai_base_url="http://localhost:4000/v1",
        ai_provider="openai_compatible",
        ai_model="local-router",
        ai_base_url="http://localhost:4000/v1",
        ai_api_key="test-key",
        public_agent_only=True,
        langfuse_enabled=True,
        langfuse_base_url="http://127.0.0.1:3000",
    )


def test_agent_config_reads_ai_provider(monkeypatch) -> None:
    monkeypatch.setenv("LEGAL_MCP_AI_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LEGAL_MCP_AI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LEGAL_MCP_AI_MODEL", "qwen-local")
    monkeypatch.setenv("LEGAL_MCP_AI_API_KEY", "local-key")

    config = load_agent_config()

    assert config.ai_provider == "openai_compatible"
    assert config.ai_base_url == "http://127.0.0.1:11434/v1"
    assert config.ai_model == "qwen-local"


def test_load_agent_config_reads_database_settings_when_env_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute(
            """
            update agent_settings
            set ai_provider = ?, ai_model = ?, ai_base_url = ?, ai_api_key = ?
            where id = 1
            """,
            ("openai_compatible", "gpt-4.1", "https://llm.example.test/v1", "stored-key"),
        )
        conn.commit()
    finally:
        conn.close()
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "LEGAL_MCP_AI_PROVIDER",
        "LEGAL_MCP_AI_MODEL",
        "LEGAL_MCP_AI_BASE_URL",
        "LEGAL_MCP_AI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_agent_config(database_path=database_path)

    assert config.ai_provider == "openai_compatible"
    assert config.ai_model == "gpt-4.1"
    assert config.ai_base_url == "https://llm.example.test/v1"
    assert config.ai_api_key == "stored-key"


def test_load_agent_config_env_overrides_database_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "legal.db"
    db.initialize_database(database_path)
    conn = db.connect(database_path)
    try:
        conn.execute("update agent_settings set ai_model = ? where id = 1", ("stored-model",))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("LEGAL_MCP_AI_MODEL", "env-model")

    config = load_agent_config(database_path=database_path)

    assert config.ai_model == "env-model"
