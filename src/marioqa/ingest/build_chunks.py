"""CLI: snapshot crudo → chunks limpios en `data/processed/<snapshot>/chunks_w<N>.jsonl`.

python -m marioqa.ingest.build_chunks                       # último snapshot, ventana de config
python -m marioqa.ingest.build_chunks --chunk-tokens 500    # para comparar 200/300/500
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections.abc import Iterable
from pathlib import Path

from marioqa.config import get_settings
from marioqa.ingest.chunking import Chunk, ChunkParams, DocMeta, TokenCounter, chunk_document
from marioqa.ingest.clean import clean_wikitext
from marioqa.ingest.mediawiki import RawPage
from marioqa.ingest.snapshot import (
    DEFAULT_ENTITY_TYPE,
    list_snapshots,
    load_manifest,
    load_pages,
    read_seed_entities,
)
from marioqa.logging_setup import get_logger, setup_logging

log = get_logger(__name__)

# Páginas cuyo contenido principal solo existe tras expandir plantillas (ver D-007).
EXPANDED_PREFIXES = ("List of Power Moons",)


def page_wikitext(page: RawPage) -> str:
    if page.title.startswith(EXPANDED_PREFIXES) and page.expanded_wikitext:
        return page.expanded_wikitext
    return page.wikitext


def entity_type_for(page: RawPage, entities: dict[str, str]) -> str:
    for title in [page.title, *page.requested_titles]:
        if title in entities:
            return entities[title]
    return DEFAULT_ENTITY_TYPE


def chunk_pages(
    pages: Iterable[RawPage],
    *,
    version: str,
    cutoff: str,
    entities: dict[str, str],
    params: ChunkParams,
    count: TokenCounter,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in sorted(pages, key=lambda p: p.pageid):
        doc = clean_wikitext(page.title, page_wikitext(page))
        meta = DocMeta(
            doc_key=f"p{page.pageid}",
            title=page.title,
            url=page.url,
            version=version,
            revid=page.revid,
            cutoff=cutoff,
            entity_type=entity_type_for(page, entities),
        )
        chunks.extend(chunk_document(meta, doc.sections, params, count))
    return chunks


def write_chunks(path: Path, chunks: list[Chunk]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")


def read_chunks(path: Path) -> list[Chunk]:
    with path.open(encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]


def chunks_path(processed_dir: Path, snapshot_id: str, chunk_tokens: int) -> Path:
    return processed_dir / snapshot_id / f"chunks_w{chunk_tokens}.jsonl"


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--snapshot", help="ID del snapshot (por defecto, el más reciente)")
    parser.add_argument("--chunk-tokens", type=int, default=settings.chunk_tokens)
    parser.add_argument("--overlap-tokens", type=int, default=None)
    parser.add_argument("--min-section-tokens", type=int, default=settings.min_section_tokens)
    args = parser.parse_args(argv)
    setup_logging(settings.log_level)

    snapshots = list_snapshots(settings.raw_dir)
    if not snapshots:
        log.error("No hay snapshots en %s; corre primero ingest-download", settings.raw_dir)
        return 1
    snapshot_id = args.snapshot or snapshots[-1]
    snap_dir = settings.raw_dir / snapshot_id
    manifest = load_manifest(snap_dir)

    # Solape = 15 % de la ventana si no se indica, como en el anteproyecto (300 → ~50).
    overlap = (
        args.overlap_tokens
        if args.overlap_tokens is not None
        else (
            settings.chunk_overlap_tokens
            if args.chunk_tokens == settings.chunk_tokens
            else round(args.chunk_tokens * 0.15)
        )
    )
    params = ChunkParams(args.chunk_tokens, overlap, args.min_section_tokens)

    from marioqa.ingest.chunking import hf_token_counter

    count = hf_token_counter(settings.embed_model)
    chunks = chunk_pages(
        load_pages(snap_dir),
        version=snapshot_id,
        cutoff=manifest.cutoff,
        entities=read_seed_entities(settings.seed_titles_path),
        params=params,
        count=count,
    )
    out = chunks_path(settings.processed_dir, snapshot_id, params.chunk_tokens)
    write_chunks(out, chunks)

    sizes = [c.n_tokens for c in chunks]
    log.info(
        "%d chunks → %s | tokens: media %.0f, mediana %.0f, máx %d | ventana %d, solape %d",
        len(chunks),
        out,
        statistics.mean(sizes),
        statistics.median(sizes),
        max(sizes),
        params.chunk_tokens,
        params.overlap_tokens,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
