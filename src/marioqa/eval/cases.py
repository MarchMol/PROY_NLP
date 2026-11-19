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
