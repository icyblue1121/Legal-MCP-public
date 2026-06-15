from __future__ import annotations

import pytest

from legal_mcp.agent_config import AgentConfig
from legal_mcp.ai_provider import (
    AIMessage,
    AIProviderNotConfiguredError,
    AIProviderUnavailableError,
    ConfiguredAIProvider,
    NoopAIProvider,
    OpenAICompatibleProvider,
    build_ai_provider,
    provider_from_config,
)


def _config(**overrides: object) -> AgentConfig:
    base = dict(
        enabled=True,
        model="m",
        ai_provider="openai_compatible",
        ai_model="qwen-local",
        ai_base_url="http://localhost:11434/v1",
        ai_api_key=None,
        ai_json_mode="auto",
    )
    base.update(overrides)
    return AgentConfig(**base)  # type: ignore[arg-type]


def test_keyless_local_endpoint_builds_real_provider() -> None:
    provider = provider_from_config(_config())
    assert isinstance(provider, OpenAICompatibleProvider)
    # ChatOpenAI rejects an empty key; a placeholder keeps a keyless endpoint working.
    assert provider.api_key == "local"


def test_build_ai_provider_keyless_local_is_not_noop() -> None:
    provider = build_ai_provider(_config())
    assert isinstance(provider, OpenAICompatibleProvider)


def test_unconfigured_backend_is_noop_and_none() -> None:
    cfg = _config(ai_base_url=None, ai_api_key=None)
    assert provider_from_config(cfg) is None
    assert isinstance(build_ai_provider(cfg), NoopAIProvider)


def test_json_mode_auto_off_for_custom_base_url() -> None:
    # auto: custom base URL is treated as self-hosted → json mode off.
    assert provider_from_config(_config()).use_json_mode is False
    # auto: default cloud endpoint (no base URL) → json mode on.
    cloud = _config(ai_base_url=None, ai_api_key="sk-test")
    assert provider_from_config(cloud).use_json_mode is True


def test_json_mode_explicit_overrides() -> None:
    assert provider_from_config(_config(ai_json_mode="on")).use_json_mode is True
    cloud = _config(ai_base_url=None, ai_api_key="sk-test", ai_json_mode="off")
    assert provider_from_config(cloud).use_json_mode is False


def test_configured_provider_raises_not_configured(tmp_path, monkeypatch) -> None:
    # With no backend configured, ConfiguredAIProvider raises a typed "not
    # configured" error (graceful-degrade signal), not a generic failure.
    monkeypatch.setattr(
        "legal_mcp.ai_provider.load_agent_config",
        lambda _path: _config(ai_base_url=None, ai_api_key=None),
    )
    provider = ConfiguredAIProvider(tmp_path / "db.sqlite")
    with pytest.raises(AIProviderNotConfiguredError):
        provider.complete([AIMessage(role="user", content="hi")])


def test_configured_provider_wraps_backend_failure_loud(tmp_path, monkeypatch) -> None:
    # A configured-but-unreachable endpoint surfaces a loud, locatable error
    # (with the endpoint), not a silent degrade.
    class _Boom(OpenAICompatibleProvider):
        def complete(self, messages):  # type: ignore[override]
            raise ConnectionError("connection refused")

    monkeypatch.setattr(
        "legal_mcp.ai_provider.load_agent_config",
        lambda _path: _config(),
    )
    monkeypatch.setattr(
        "legal_mcp.ai_provider.provider_from_config",
        lambda _cfg: _Boom(api_key="local", model="m", base_url="http://localhost:11434/v1"),
    )
    provider = ConfiguredAIProvider(tmp_path / "db.sqlite")
    with pytest.raises(AIProviderUnavailableError) as excinfo:
        provider.complete([AIMessage(role="user", content="hi")])
    assert "http://localhost:11434/v1" in str(excinfo.value)
