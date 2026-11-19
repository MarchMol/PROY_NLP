"""Snapshots versionados del corpus crudo en `data/raw/<snapshot_id>/`."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from marioqa.ingest.mediawiki import RawPage

MANIFEST = "manifest.json"
PAGES_DIR = "pages"


class PageEntry(BaseModel):
    title: str
    requested_titles: list[str]
    pageid: int
    revid: int
    timestamp: str
    url: str
    file: str
    sha256: str


class Manifest(BaseModel):
    snapshot_id: str
    cutoff: str
    mode: Literal["latest", "as_of"]
    created_at: str
    source: str
    license: str = "CC BY-SA (Super Mario Wiki)"
    pages: list[PageEntry]
    missing: list[str]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


DEFAULT_ENTITY_TYPE = "other"


def read_seed_entities(path: Path) -> dict[str, str]:
    """Título → entity_type, según las secciones `[tipo]` del archivo semilla (orden preservado)."""
    current = DEFAULT_ENTITY_TYPE
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip() or DEFAULT_ENTITY_TYPE
            continue
        out.setdefault(line, current)
    return out


def read_seed_titles(path: Path) -> list[str]:
    """Títulos semilla (uno por línea), sin comentarios, encabezados ni duplicados."""
    return list(read_seed_entities(path))


def write_snapshot(
    raw_dir: Path,
    snapshot_id: str,
    pages: list[RawPage],
    missing: list[str],
    *,
    cutoff: str,
    mode: Literal["latest", "as_of"],
    source: str,
    overwrite: bool = False,
) -> Path:
    """Escribe un JSON por página + `manifest.json`. Devuelve la carpeta del snapshot."""
    snap_dir = raw_dir / snapshot_id
    if (snap_dir / MANIFEST).exists() and not overwrite:
        raise FileExistsError(f"El snapshot {snapshot_id} ya existe (usa --overwrite)")
    pages_dir = snap_dir / PAGES_DIR
    pages_dir.mkdir(parents=True, exist_ok=True)

    entries: list[PageEntry] = []
    for page in sorted(pages, key=lambda p: p.pageid):
        rel = f"{PAGES_DIR}/{page.pageid}.json"
        (snap_dir / rel).write_text(page.model_dump_json(indent=1), encoding="utf-8")
        entries.append(
            PageEntry(
                title=page.title,
                requested_titles=page.requested_titles,
                pageid=page.pageid,
                revid=page.revid,
                timestamp=page.timestamp,
                url=page.url,
                file=rel,
                sha256=sha256_text(page.wikitext),
            )
        )
    manifest = Manifest(
        snapshot_id=snapshot_id,
        cutoff=cutoff,
        mode=mode,
        created_at=utc_now_iso(),
        source=source,
        pages=entries,
        missing=missing,
    )
    (snap_dir / MANIFEST).write_text(manifest.model_dump_json(indent=1), encoding="utf-8")
    return snap_dir


def load_manifest(snap_dir: Path) -> Manifest:
    return Manifest.model_validate_json((snap_dir / MANIFEST).read_text(encoding="utf-8"))


def load_pages(snap_dir: Path) -> list[RawPage]:
    """Carga las páginas de un snapshot verificando el hash de cada una."""
    manifest = load_manifest(snap_dir)
    pages: list[RawPage] = []
    for entry in manifest.pages:
        page = RawPage.model_validate_json((snap_dir / entry.file).read_text(encoding="utf-8"))
        if sha256_text(page.wikitext) != entry.sha256:
            raise ValueError(f"Hash inválido en {entry.file}: el snapshot está corrupto")
        pages.append(page)
    return pages


def list_snapshots(raw_dir: Path) -> list[str]:
    """IDs de snapshots completos, en orden cronológico (los IDs empiezan con la fecha)."""
    if not raw_dir.exists():
        return []
    return sorted(p.name for p in raw_dir.iterdir() if (p / MANIFEST).exists())
