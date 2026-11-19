"""Interfaz común de los clientes de LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """El LLM no respondió, rechazó la petición o devolvió algo inválido."""


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def cost_usd(self, price_in_per_mtok: float, price_out_per_mtok: float) -> float:
        return (
            self.input_tokens * price_in_per_mtok + self.output_tokens * price_out_per_mtok
        ) / 1_000_000

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass
class LLMResult(Generic[T]):
    output: T
    usage: LLMUsage
    model: str
    latency_s: float


class LLMClient(Protocol):
    model: str

    def generate(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        """Genera una salida estructurada validada contra `schema`."""
        ...
