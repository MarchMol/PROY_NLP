"""Reglas de acceso por rol/estado/versión, compartidas por todos los índices."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

Role = Literal["jugador", "admin"]
ROLE_RANK: dict[str, int] = {"jugador": 0, "admin": 1}


def readable_roles(role: Role) -> list[str]:
    """Valores de `rol_min` que `role` puede leer."""
    rank = ROLE_RANK[role]
    return [r for r, v in ROLE_RANK.items() if v <= rank]


def chroma_where(role: Role, version: str | None = None, include_deprecated: bool = False) -> dict:
    """Filtro `where` de Chroma: se aplica dentro de la consulta al índice."""
    clauses: list[dict[str, Any]] = [{"rol_min": {"$in": readable_roles(role)}}]
    if not include_deprecated:
        clauses.append({"status": "active"})
    if version:
        clauses.append({"version": version})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def is_allowed(
    meta: Mapping[str, Any],
    role: Role,
    version: str | None = None,
    include_deprecated: bool = False,
) -> bool:
    """Misma regla que `chroma_where`, evaluada en Python (BM25 y segunda validación)."""
    if meta.get("rol_min") not in readable_roles(role):
        return False
    if not include_deprecated and meta.get("status") != "active":
        return False
    return not (version and meta.get("version") != version)
