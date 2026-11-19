"""CLI `ingest`: de los snapshots crudos a índices listos, en un solo comando e idempotente.

Pasos: (descarga si no hay snapshots) → limpieza + chunking de cada snapshot y de los
documentos internos → embeddings solo de chunks nuevos o cambiados → Chroma → versión
más reciente `active`, resto `deprecated` → BM25 → SQLite de la versión activa.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from marioqa.config import Settings, get_settings
from marioqa.gamedata.build_db import build_database
from marioqa.index.bm25 import BM25Index
from marioqa.index.embeddings import E5Embedder, Embedder
from marioqa.index.vector_store import VectorStore, doc_id
from marioqa.index.versions import VersionRegistry
from marioqa.ingest.build_chunks import chunk_pages, chunks_path, write_chunks
from marioqa.ingest.chunking import Chunk, ChunkParams, DocMeta, TokenCounter, chunk_document
from marioqa.ingest.clean import clean_markdown
from marioqa.ingest.snapshot import list_snapshots, load_manifest, load_pages, read_seed_entities
from marioqa.logging_setup import get_logger, setup_logging

log = get_logger(__name__)

EMBED_BATCH = 64
INTERNAL_ENTITY = "internal"


@dataclass
class VersionStats:
    chunks: int = 0
    embedded: int = 0
    deleted: int = 0


@dataclass
class IngestReport:
    active: str | None = None
    versions: dict[str, VersionStats] = field(default_factory=dict)
    bm25_docs: int = 0
    sqlite_rows: dict[str, int] = field(default_factory=dict)


def bm25_path(settings: Settings) -> Path:
    return settings.index_dir / f"bm25_{settings.collection_name()}.json"


def registry_path(settings: Settings) -> Path:
    return settings.index_dir / f"versions_{settings.collection_name()}.json"


def internal_chunks(
    docs_dir: Path, version: str, cutoff: str, params: ChunkParams, count: TokenCounter
) -> list[Chunk]:
    """Documentos internos sintéticos (Markdown) con `rol_min = admin`."""
    chunks: list[Chunk] = []
    for md in sorted(docs_dir.glob("*.md")):
        doc = clean_markdown(md.stem, md.read_text(encoding="utf-8"))
        slug = re.sub(r"[^a-z0-9]+", "-", md.stem.lower()).strip("-")
        meta = DocMeta(
            doc_key=f"int-{slug}",
            title=doc.title,
            url=f"internal://{md.name}",
            version=version,
            revid=0,
            cutoff=cutoff,
            entity_type=INTERNAL_ENTITY,
            rol_min="admin",
        )
        chunks.extend(chunk_document(meta, doc.sections, params, count))
    return chunks


def _sync_vectors(
    store: VectorStore, version: str, chunks: list[Chunk], embedder: Embedder
) -> VersionStats:
    """Embebe solo lo nuevo/cambiado (por hash) y borra lo que ya no existe en la versión."""
    existing = store.hashes(version)
    wanted = {doc_id(c.version, c.chunk_id): c for c in chunks}
    stale = [i for i in existing if i not in wanted]
    store.delete(stale)
    todo = [c for i, c in wanted.items() if existing.get(i) != c.hash]
    for start in range(0, len(todo), EMBED_BATCH):
        batch = todo[start : start + EMBED_BATCH]
        store.upsert(batch, embedder.embed_passages([c.text for c in batch]))
        log.info("  embeddings %s: %d/%d", version, start + len(batch), len(todo))
    return VersionStats(chunks=len(chunks), embedded=len(todo), deleted=len(stale))


def run_ingest(settings: Settings, embedder: Embedder, count: TokenCounter) -> IngestReport:
    snapshots = list_snapshots(settings.raw_dir)
    if not snapshots:
        raise FileNotFoundError(f"No hay snapshots en {settings.raw_dir}")

    params = ChunkParams(
        settings.chunk_tokens, settings.chunk_overlap_tokens, settings.min_section_tokens
    )
    entities = read_seed_entities(settings.seed_titles_path)
    store = VectorStore(settings.chroma_dir, settings.collection_name())
    registry = VersionRegistry.load(registry_path(settings))
    report = IngestReport()
    all_chunks: list[Chunk] = []
    pages_by_version = {}

    for sid in snapshots:
        snap_dir = settings.raw_dir / sid
        manifest = load_manifest(snap_dir)
        pages = load_pages(snap_dir)
        pages_by_version[sid] = (pages, manifest.cutoff)
        chunks = chunk_pages(
            pages,
            version=sid,
            cutoff=manifest.cutoff,
            entities=entities,
            params=params,
            count=count,
        )
        chunks += internal_chunks(settings.internal_docs_dir, sid, manifest.cutoff, params, count)
        write_chunks(chunks_path(settings.processed_dir, sid, params.chunk_tokens), chunks)
        log.info("Versión %s: %d chunks", sid, len(chunks))
        report.versions[sid] = _sync_vectors(store, sid, chunks, embedder)
        registry.register(sid, manifest.cutoff, len(chunks))
        all_chunks.extend(chunks)

    report.active = registry.activate_latest()
    for info in registry.versions.values():
        store.set_status(info.version, info.status)
    status = {v: info.status for v, info in registry.versions.items()}
    for c in all_chunks:
        c.status = status.get(c.version, "deprecated")
    registry.save(registry_path(settings))

    bm25 = BM25Index.from_chunks(all_chunks)
    bm25.save(bm25_path(settings))
    report.bm25_docs = len(bm25.ids)

    if report.active:
        pages, cutoff = pages_by_version[report.active]
        kingdoms = [t for t, e in entities.items() if e == "kingdom"]
        data = build_database(settings.sqlite_path, pages, kingdoms, report.active, cutoff)
        report.sqlite_rows = {
            "kingdom": len(data.kingdoms),
            "power_moon": len(data.moons),
            "outfit": len(data.outfits),
            "shop_item": len(data.shop),
        }
    return report


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--no-download", action="store_true", help="Falla si no hay snapshots")
    args = parser.parse_args(argv)
    setup_logging(settings.log_level)

    if not list_snapshots(settings.raw_dir):
        if args.no_download:
            log.error("No hay snapshots en %s", settings.raw_dir)
            return 1
        from marioqa.ingest.download import main as download_main

        log.info("No hay snapshots: descargando uno nuevo")
        if download_main([]) != 0:
            return 1

    from marioqa.ingest.chunking import hf_token_counter

    embedder = E5Embedder(settings.embed_model, batch_size=settings.embed_batch_size)
    report = run_ingest(settings, embedder, hf_token_counter(settings.embed_model))
    for v, s in report.versions.items():
        log.info(
            "%s [%s]: %d chunks, %d embebidos, %d eliminados",
            v,
            "active" if v == report.active else "deprecated",
            s.chunks,
            s.embedded,
            s.deleted,
        )
    log.info("BM25: %d docs | SQLite: %s", report.bm25_docs, report.sqlite_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
