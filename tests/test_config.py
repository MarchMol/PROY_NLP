"""Tests de la configuración central."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marioqa.config import FusionMethod, LLMProvider, Settings


def _settings(**kw: object) -> Settings:
    # _env_file=None: los tests no dependen del .env local
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_defaults_match_anteproyecto(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LLM_PROVIDER", "LLM_API_KEY", "EVIDENCE_TAU", "CHUNK_TOKENS"):
        monkeypatch.delenv(var, raising=False)
    s = _settings()
    assert s.llm_provider is LLMProvider.MOCK
    assert s.chunk_tokens == 300
    assert s.chunk_overlap_tokens == 50
    assert s.min_section_tokens == 80
    assert s.top_k_branch == 20
    assert s.top_k_final == 5
    assert s.fusion_method is FusionMethod.WEIGHTED
    assert (s.fusion_w_dense, s.fusion_w_lexical) == (0.6, 0.4)
    assert s.evidence_tau == pytest.approx(0.30)
    assert s.max_question_chars == 300
    assert s.max_tool_calls == 3
    assert s.sql_max_rows == 50


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNK_TOKENS", "500")
    monkeypatch.setenv("EVIDENCE_TAU", "0.42")
    s = _settings()
    assert s.chunk_tokens == 500
    assert s.evidence_tau == pytest.approx(0.42)


def test_overlap_must_be_smaller_than_window() -> None:
    with pytest.raises(ValidationError):
        _settings(chunk_tokens=100, chunk_overlap_tokens=100)


def test_anthropic_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        _settings(llm_provider="anthropic")


def test_api_key_is_secret() -> None:
    s = _settings(llm_provider="anthropic", llm_api_key="sk-test-123")
    assert "sk-test-123" not in repr(s)
    assert s.llm_api_key is not None
    assert s.llm_api_key.get_secret_value() == "sk-test-123"


def test_user_agent_includes_contact() -> None:
    assert "contacto@example.org" in _settings(wiki_contact="contacto@example.org").user_agent
    assert _settings(wiki_contact="").user_agent.startswith("MarioOdysseyRAG/")
