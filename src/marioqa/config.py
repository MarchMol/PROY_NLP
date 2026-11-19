"""Configuración central del proyecto.

Todo parámetro ajustable (umbrales, pesos, tamaños de ventana, rutas, modelos)
vive aquí y se puede sobreescribir por variable de entorno o `.env`.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class LLMProvider(StrEnum):
    """Proveedores de LLM soportados."""

    ANTHROPIC = "anthropic"
    MOCK = "mock"


class FusionMethod(StrEnum):
    """Método de fusión de la búsqueda híbrida."""

    WEIGHTED = "weighted"
    RRF = "rrf"


class Settings(BaseSettings):
    """Parámetros del sistema; cada campo se lee de la variable de entorno homónima."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---------------------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.MOCK
    llm_model: str = "claude-haiku-5-5"
    llm_api_key: SecretStr | None = None
    llm_judge_model: str | None = None
    llm_effort: str = "low"
    llm_max_tokens: int = Field(default=2048, ge=64)
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=1, ge=0)
    # Tarifas en USD por millón de tokens (para reportar costo por consulta).
    llm_price_input_per_mtok: float = Field(default=0.10, ge=0)
    llm_price_output_per_mtok: float = Field(default=0.50, ge=0)

    # --- Fuente (MediaWiki) ------------------------------------------------
    wiki_api_url: str = "https://www.mariowiki.com/api.php"
    wiki_base_url: str = "https://www.mariowiki.com/"
    wiki_contact: str = ""
    wiki_min_interval_s: float = Field(default=1.0, ge=0.0)
    wiki_max_retries: int = Field(default=4, ge=0)
    wiki_timeout_s: float = Field(default=30.0, gt=0)

    # --- Rutas -------------------------------------------------------------
    data_dir: Path = ROOT_DIR / "data"
    raw_dir: Path = ROOT_DIR / "data" / "raw"
    processed_dir: Path = ROOT_DIR / "data" / "processed"
    internal_docs_dir: Path = ROOT_DIR / "data" / "internal"
    chroma_dir: Path = ROOT_DIR / "chroma"
    sqlite_path: Path = ROOT_DIR / "data" / "game_data.sqlite"
    logs_dir: Path = ROOT_DIR / "logs"
    seed_titles_path: Path = ROOT_DIR / "data" / "seed_titles.txt"

    # --- Chunking ----------------------------------------------------------
    chunk_tokens: int = Field(default=300, ge=50)
    chunk_overlap_tokens: int = Field(default=50, ge=0)
    min_section_tokens: int = Field(default=80, ge=0)

    # --- Embeddings / índices ---------------------------------------------
    embed_model: str = "intfloat/multilingual-e5-base"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    chroma_collection: str = "smo_chunks"

    # --- Recuperación ------------------------------------------------------
    top_k_branch: int = Field(default=20, ge=1)
    top_k_final: int = Field(default=5, ge=1, le=10)
    fusion_method: FusionMethod = FusionMethod.WEIGHTED
    fusion_w_dense: float = Field(default=0.6, ge=0.0, le=1.0)
    fusion_w_lexical: float = Field(default=0.4, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1)
    evidence_tau: float = Field(default=0.30, ge=0.0, le=1.0)

    # --- Guardrails / herramientas ----------------------------------------
    max_question_chars: int = Field(default=300, ge=1)
    max_tool_calls: int = Field(default=3, ge=1)
    tool_timeout_s: float = Field(default=10.0, gt=0)
    sql_max_rows: int = Field(default=50, ge=1)
    sql_timeout_s: float = Field(default=2.0, gt=0)

    # --- Logging -----------------------------------------------------------
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.chunk_overlap_tokens >= self.chunk_tokens:
            raise ValueError("chunk_overlap_tokens debe ser menor que chunk_tokens")
        if self.llm_provider is LLMProvider.ANTHROPIC and self.llm_api_key is None:
            raise ValueError("LLM_PROVIDER=anthropic requiere LLM_API_KEY")
        return self

    @property
    def user_agent(self) -> str:
        """User-Agent propio para la API de MediaWiki (incluye contacto si existe)."""
        contact = f"; {self.wiki_contact}" if self.wiki_contact else ""
        return f"MarioOdysseyRAG/0.1 (CC3103 UVG student project{contact})"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada)."""
    return Settings()
