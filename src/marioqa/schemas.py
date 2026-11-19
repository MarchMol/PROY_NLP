"""Contratos de datos del sistema (validación con Pydantic)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SourceRef(BaseModel):
    """Cita de una respuesta: siempre apunta a un chunk realmente recuperado."""

    model_config = ConfigDict(extra="forbid")

    source: str
    page: str
    chunk_id: str
    url: str = ""


class AnswerOutput(BaseModel):
    """Contrato de salida del anteproyecto."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    sources: list[SourceRef] = Field(default_factory=list)
    abstained: bool = False
    tool_used: str | None = None
    needs_human_review: bool = False


class GroundedDraft(BaseModel):
    """Lo que devuelve el LLM. Solo cita ids; fuente y sección las pone el sistema, no el modelo."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(description="Respuesta en español basada solo en los fragmentos.")
    cited_chunk_ids: list[str] = Field(
        description="IDs de los fragmentos que sustentan la respuesta, tal como aparecen."
    )
    insufficient_context: bool = Field(
        description="true si los fragmentos no alcanzan para responder."
    )
