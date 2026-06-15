"""Configurable server-side AI provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from legal_mcp.agent_config import AgentConfig, load_agent_config


class AIProviderError(RuntimeError):
    """Base class for server-side AI backend failures."""


class AIProviderNotConfiguredError(AIProviderError):
    """No usable AI backend is configured (the feature is simply off)."""


class AIProviderUnavailableError(AIProviderError):
    """A backend IS configured but the request to it failed (loud, locatable)."""


@dataclass(frozen=True)
class AIMessage:
    role: str
    content: str


class AIProvider(Protocol):
    def complete(self, messages: list[AIMessage]) -> AIMessage:
        """Return one assistant message for the provided sanitized prompt."""


class NoopAIProvider:
    def complete(self, messages: list[AIMessage]) -> AIMessage:
        return AIMessage(role="assistant", content="{}")


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        use_json_mode: bool = True,
    ) -> None:
        # Self-hosted endpoints (Ollama/vLLM) need no API key, but ChatOpenAI
        # rejects an empty one — use a harmless placeholder so a keyless local
        # endpoint still works.
        self.api_key = api_key or "local"
        self.model = model
        self.base_url = base_url
        self.use_json_mode = use_json_mode

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError("langchain-openai is required for openai-compatible AI") from exc

        # temperature=0 for deterministic plans. response_format json_object asks
        # OpenAI-compatible providers (incl. DeepSeek) for a bare JSON object, but
        # many local models reject the parameter — so it is opt-in. When off we
        # lean on the prompt plus the _strip_code_fence / _extract_json_object
        # fallback parsing downstream.
        model_kwargs: dict[str, object] = {}
        if self.use_json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}
        chat = ChatOpenAI(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            temperature=0,
            model_kwargs=model_kwargs,
        )
        response = chat.invoke(
            [{"role": message.role, "content": message.content} for message in messages]
        )
        return AIMessage(role="assistant", content=_strip_code_fence(str(response.content)))


class ConfiguredAIProvider:
    """Lazy server-side provider that loads model settings only when AI is needed."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = database_path
        self._provider: AIProvider | None = None
        self._endpoint = "default OpenAI endpoint"
        self._loaded = False

    def complete(self, messages: list[AIMessage]) -> AIMessage:
        if not self._loaded:
            config = load_agent_config(self.database_path)
            self._provider = provider_from_config(config)
            self._endpoint = config.ai_base_url or "default OpenAI endpoint"
            self._loaded = True
        if self._provider is None:
            raise AIProviderNotConfiguredError("server AI provider is not configured")
        try:
            return self._provider.complete(messages)
        except AIProviderError:
            raise
        except Exception as exc:
            # A backend IS configured but the request failed. Surface it loud with
            # the endpoint so a self-hosted operator can locate the problem,
            # instead of silently degrading to a generic "unavailable".
            raise AIProviderUnavailableError(
                f"AI backend at {self._endpoint} failed: {exc}"
            ) from exc


def _strip_code_fence(content: str) -> str:
    """Remove a surrounding markdown code fence if the model added one."""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _resolve_json_mode(config: AgentConfig) -> bool:
    """Whether to request OpenAI json_object response_format.

    ``auto`` (the default) enables it only for the default cloud endpoint; any
    custom base URL is treated as a self-hosted endpoint where the parameter may
    not be supported, so it is left off and parsing falls back to fence-stripping.
    """
    mode = (config.ai_json_mode or "auto").lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    return not config.ai_base_url


def _is_configured(config: AgentConfig) -> bool:
    # Either credentials (cloud) or a base URL (keyless self-hosted endpoint).
    return bool(config.ai_api_key or config.ai_base_url)


def provider_from_config(config: AgentConfig) -> AIProvider | None:
    if config.ai_provider != "openai_compatible":
        return None
    if not _is_configured(config):
        return None
    return OpenAICompatibleProvider(
        api_key=config.ai_api_key or "",
        model=config.ai_model,
        base_url=config.ai_base_url,
        use_json_mode=_resolve_json_mode(config),
    )


def build_ai_provider(config: AgentConfig) -> AIProvider:
    if config.ai_provider == "none" or not _is_configured(config):
        return NoopAIProvider()
    if config.ai_provider == "openai_compatible":
        return OpenAICompatibleProvider(
            api_key=config.ai_api_key or "",
            model=config.ai_model,
            base_url=config.ai_base_url,
            use_json_mode=_resolve_json_mode(config),
        )
    raise ValueError(f"unsupported AI provider: {config.ai_provider}")
