"""Validación de permisos: segunda comprobación, independiente del filtro aplicado en el índice."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from marioqa.index.access import Role, is_allowed
from marioqa.ingest.chunking import Chunk


@dataclass
class PermissionCheck:
    allowed: list[Chunk] = field(default_factory=list)
    denied: list[Chunk] = field(default_factory=list)


def validate_evidence(
    chunks: Sequence[Chunk],
    role: Role,
    version: str | None = None,
    include_deprecated: bool = False,
) -> PermissionCheck:
    """Separa la evidencia que el rol de la SESIÓN puede leer de la que no.

    Si el filtro del índice funcionó, `denied` siempre viene vacío; si no, esto lo ataja
    antes de que el chunk llegue al generador.
    """
    out = PermissionCheck()
    for c in chunks:
        meta = {"rol_min": c.rol_min, "status": c.status, "version": c.version}
        target = out.allowed if is_allowed(meta, role, version, include_deprecated) else out.denied
        target.append(c)
    return out
