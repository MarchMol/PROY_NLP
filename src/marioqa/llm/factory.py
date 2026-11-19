"""Construye el cliente de LLM según la configuración."""

from __future__ import annotations

from marioqa.config import LLMProvider, Settings
from marioqa.llm.base import LLMClient
from marioqa.llm.mock import MockLLM


def get_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider is LLMProvider.MOCK:
        return MockLLM()
    from marioqa.llm.anthropic_client import AnthropicLLM

    assert settings.llm_api_key is not None  # garantizado por Settings
    return AnthropicLLM(
        settings.llm_api_key.get_secret_value(),
        settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        effort=settings.llm_effort,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
