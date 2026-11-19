"""Registro de versiones del corpus: la de corte más reciente es `active`, el resto `deprecated`."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from marioqa.ingest.snapshot import utc_now_iso

Status = Literal["active", "deprecated"]


class VersionInfo(BaseModel):
    version: str
    cutoff: str
    indexed_at: str
    n_chunks: int
    status: Status = "deprecated"


class VersionRegistry(BaseModel):
    versions: dict[str, VersionInfo] = {}

    @classmethod
    def load(cls, path: Path) -> VersionRegistry:
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=1), encoding="utf-8")

    def register(self, version: str, cutoff: str, n_chunks: int) -> None:
        prev = self.versions.get(version)
        self.versions[version] = VersionInfo(
            version=version,
            cutoff=cutoff,
            indexed_at=prev.indexed_at if prev and prev.n_chunks == n_chunks else utc_now_iso(),
            n_chunks=n_chunks,
            status=prev.status if prev else "deprecated",
        )

    def activate_latest(self) -> str | None:
        """Activa la versión con fecha de corte más reciente; devuelve su id."""
        if not self.versions:
            return None
        latest = max(self.versions.values(), key=lambda v: (v.cutoff, v.version)).version
        for v in self.versions.values():
            v.status = "active" if v.version == latest else "deprecated"
        return latest

    @property
    def active(self) -> str | None:
        return next((v.version for v in self.versions.values() if v.status == "active"), None)
