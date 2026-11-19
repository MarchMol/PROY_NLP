"""Clientes de LLM: Anthropic (real) y mock (offline, determinista)."""

from marioqa.llm.base import LLMClient, LLMError, LLMResult, LLMUsage
from marioqa.llm.factory import get_llm

__all__ = ["LLMClient", "LLMError", "LLMResult", "LLMUsage", "get_llm"]
