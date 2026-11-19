"""CLI: descarga los artículos semilla y guarda un snapshot versionado.

python -m marioqa.ingest.download                      # revisión actual
python -m marioqa.ingest.download --as-of 2025-01-01   # foto histórica (casos de versión)
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from marioqa.config import get_settings
from marioqa.ingest.mediawiki import MediaWikiClient, RawPage
from marioqa.ingest.snapshot import read_seed_titles, utc_now_iso, write_snapshot
from marioqa.logging_setup import get_logger, setup_logging

log = get_logger(__name__)


def _parse_as_of(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def download(
    client: MediaWikiClient,
    titles: list[str],
    *,
    as_of: str | None = None,
    expand: bool = True,
) -> tuple[list[RawPage], list[str]]:
    """Descarga (y opcionalmente expande) las páginas; devuelve (páginas, títulos faltantes)."""
    if as_of is None:
        result = client.fetch_latest(titles)
        pages, missing = result.pages, result.missing
    else:
        by_id: dict[int, RawPage] = {}
        missing = []
        for title in titles:
            page = client.fetch_as_of(title, as_of)
            if page is None:
                missing.append(title)
            elif page.pageid in by_id:
                by_id[page.pageid].requested_titles.extend(page.requested_titles)
            else:
                by_id[page.pageid] = page
        pages = list(by_id.values())

    if expand:
        for i, page in enumerate(pages, 1):
            log.info("Expandiendo plantillas %d/%d: %s", i, len(pages), page.title)
            page.expanded_wikitext = client.expand_templates(page.title, page.wikitext)
    return pages, missing


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--titles", type=Path, default=settings.seed_titles_path)
    parser.add_argument(
        "--as-of", help="Fecha/hora ISO; descarga la revisión vigente en ese momento"
    )
    parser.add_argument("--snapshot-id", help="Por defecto: AAAAMMDD de la fecha de corte")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-expand", action="store_true", help="No expandir plantillas")
    args = parser.parse_args(argv)
    setup_logging(settings.log_level)

    titles = read_seed_titles(args.titles)
    as_of = _parse_as_of(args.as_of) if args.as_of else None
    cutoff = as_of or utc_now_iso()
    snapshot_id = args.snapshot_id or cutoff[:10].replace("-", "")
    if as_of:
        snapshot_id = args.snapshot_id or f"{snapshot_id}-asof"

    log.info("Descargando %d títulos (corte %s) → snapshot %s", len(titles), cutoff, snapshot_id)
    with MediaWikiClient(
        settings.wiki_api_url,
        settings.wiki_base_url,
        settings.user_agent,
        min_interval_s=settings.wiki_min_interval_s,
        max_retries=settings.wiki_max_retries,
        timeout_s=settings.wiki_timeout_s,
    ) as client:
        pages, missing = download(client, titles, as_of=as_of, expand=not args.no_expand)

    snap_dir = write_snapshot(
        settings.raw_dir,
        snapshot_id,
        pages,
        missing,
        cutoff=cutoff,
        mode="as_of" if as_of else "latest",
        source=settings.wiki_api_url,
        overwrite=args.overwrite,
    )
    log.info("Snapshot listo: %s (%d páginas, %d faltantes)", snap_dir, len(pages), len(missing))
    if missing:
        log.warning("Títulos no encontrados: %s", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
