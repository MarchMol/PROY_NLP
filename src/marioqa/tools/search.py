"""`search_documents`: envoltorio validado del recuperador híbrido.

El rol NO es un parámetro de la herramienta: lo pone el sistema a partir de la sesión.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from marioqa.index.access import Role
from marioqa.nlp.ner import QueryAnalysis
from marioqa.retrieval.hybrid import HybridRetriever, RetrievalResult


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=300)
    top_k: int = Field(default=5, ge=1, le=10)
    entities: list[str] = Field(default_factory=list, max_length=20)
    version: str | None = Field(default=None, max_length=40)


class SearchTool:
    name = "search_documents"

    def __init__(self, retriever: HybridRetriever) -> None:
        self.retriever = retriever

    def run(
        self,
        params: SearchInput,
        role: Role,
        analysis: QueryAnalysis | None = None,
        include_deprecated: bool = False,
    ) -> RetrievalResult:
        return self.retriever.search(
            params.query,
            role,
            analysis=analysis,
            version=params.version,
            include_deprecated=include_deprecated,
            top_k=params.top_k,
        )
