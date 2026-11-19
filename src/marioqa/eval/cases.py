"""Casos del banco de pruebas (JSONL)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from marioqa.ingest.chunking import Chunk


class BenchCase(BaseModel):
    id: str
    question: str
    expected_behavior: str
    expected_sources: list[str] = Field(default_factory=list)
    expected_pages: list[str] = Field(default_factory=list)
    # Opcional: frase que debe contener el chunk para contarlo como relevante (más estricto).
    expected_text: list[str] = Field(default_factory=list)
    answerable: bool
    risk_level: Literal["bajo", "medio", "alto"] = "bajo"
    category: str = ""
    expected_tool: str | None = None
    # --- Campos del banco completo (Fase 9/10); opcionales para el formato demo ---
    group: Literal["rag", "herramientas", "seguridad"] = "rag"
    role: Literal["jugador", "admin"] = "jugador"
    # Falla simulada durante el caso: buscador caído o fragmento envenenado en la evidencia.
    simulate: Literal["search_down", "llm_down", "poison"] | None = None
    expect_abstain: bool | None = None  # por defecto: not answerable
    expect_human_review: bool | None = None
    must_not_contain: list[str] = Field(default_factory=list)
    must_contain_any: list[str] = Field(default_factory=list)
    false_premise: bool = False  # el juez verifica que no acepte la premisa falsa
    version_check: bool = False  # toda la evidencia debe ser de la versión activa
    threat: str = ""
    component: str = ""
    mitigation: str = ""

    @property
    def should_abstain(self) -> bool:
        return self.expect_abstain if self.expect_abstain is not None else not self.answerable

    def is_relevant(self, chunk: Chunk) -> bool:
        if chunk.source not in self.expected_sources:
            return False
        if self.expected_pages and not any(
            sec == page or sec.startswith(page + " >")
            for page in self.expected_pages
            for sec in chunk.sections
        ):
            return False
        return not self.expected_text or any(t in chunk.text for t in self.expected_text)

    @property
    def has_retrieval_labels(self) -> bool:
        return self.answerable and bool(self.expected_sources)


def load_cases(path: Path) -> list[BenchCase]:
    with path.open(encoding="utf-8") as f:
        return [BenchCase.model_validate_json(line) for line in f if line.strip()]
