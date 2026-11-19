"""Piezas comunes de las herramientas: errores, lista blanca por rol, presupuesto y timeout."""

from __future__ import annotations

import concurrent.futures as cf
from collections.abc import Callable
from typing import TypeVar

from marioqa.index.access import Role

R = TypeVar("R")

SEARCH_DOCUMENTS = "search_documents"
QUERY_GAME_DATA = "query_game_data"
REINDEX_CORPUS = "reindex_corpus"

# Lista blanca de herramientas que el AGENTE puede invocar, por rol de la sesión.
# `reindex_corpus` no aparece para ningún rol: es una acción de CLI con confirmación humana.
AGENT_TOOLS: dict[str, frozenset[str]] = {
    "jugador": frozenset({SEARCH_DOCUMENTS, QUERY_GAME_DATA}),
    "admin": frozenset({SEARCH_DOCUMENTS, QUERY_GAME_DATA}),
}


class ToolError(RuntimeError):
    """La herramienta falló, no está disponible o recibió parámetros inválidos."""


class ToolNotAllowed(ToolError):
    """La herramienta no está en la lista blanca del rol."""


class ToolBudgetExceeded(ToolError):
    """Se superó el máximo de llamadas a herramientas por consulta."""


class ToolBudget:
    """Cuenta las llamadas a herramientas de UNA consulta y aplica la lista blanca."""

    def __init__(self, role: Role, max_calls: int) -> None:
        self.role = role
        self.max_calls = max_calls
        self.calls: list[str] = []

    def authorize(self, tool: str) -> None:
        if tool not in AGENT_TOOLS.get(self.role, frozenset()):
            raise ToolNotAllowed(f"La herramienta {tool!r} no está permitida para {self.role}")
        if len(self.calls) >= self.max_calls:
            raise ToolBudgetExceeded(f"Máximo de {self.max_calls} llamadas a herramientas")
        self.calls.append(tool)

    @property
    def remaining(self) -> int:
        return self.max_calls - len(self.calls)


_POOL = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")


def run_with_timeout(fn: Callable[[], R], timeout_s: float, name: str) -> R:
    """Ejecuta `fn` con tiempo límite. Si vence, la herramienta se da por no disponible."""
    future = _POOL.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    except cf.TimeoutError as exc:
        future.cancel()
        raise ToolError(f"{name} no respondió en {timeout_s:.1f} s") from exc
