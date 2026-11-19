"""Auditoría: una línea JSONL por consulta con la cadena completa y la PII ya enmascarada.

Se enmascara TODO valor de texto del registro antes de escribir (no solo la pregunta), así un
fragmento o una respuesta con un correo o un token tampoco quedan en claro. Solo un admin lee.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from marioqa.guardrails.pii import mask_pii
from marioqa.index.access import Role


class AuditAccessDenied(PermissionError):
    pass


def mask_record(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)[0]
    if isinstance(value, dict):
        return {k: mask_record(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [mask_record(v) for v in value]
    return value


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(mask_record(record), ensure_ascii=False, default=str)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self, role: Role, limit: int = 50) -> list[dict[str, Any]]:
        """Últimos `limit` registros. Solo para el rol administrador (de la sesión)."""
        if role != "admin":
            raise AuditAccessDenied("Solo el rol administrador puede leer la auditoría")
        return list(self._tail(limit))

    def _tail(self, limit: int) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return iter(())
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        return (json.loads(line) for line in reversed(lines) if line.strip())
