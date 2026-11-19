"""Test de punta a punta de `ingest` con corpus SINTÉTICO, embedder por hashing y sin red."""

from __future__ import annotations

import sqlite3

from marioqa.config import Settings
from marioqa.index.bm25 import BM25Index
from marioqa.index.embeddings import HashEmbedder
from marioqa.index.vector_store import VectorStore
from marioqa.ingest.chunking import whitespace_counter
from marioqa.ingest.pipeline import bm25_path, run_ingest
from marioqa.ingest.snapshot import write_snapshot
from tests.synthetic import synthetic_pages


def _snapshot(settings: Settings, sid: str, cutoff: str, variant: str = "") -> None:
    write_snapshot(
        settings.raw_dir,
        sid,
        synthetic_pages(variant),
        [],
        cutoff=cutoff,
        mode="latest",
        source="test",
    )


def _ingest(settings: Settings):
    return run_ingest(settings, HashEmbedder(), whitespace_counter)


def _store(settings: Settings) -> VectorStore:
    return VectorStore(settings.chroma_dir, settings.collection_name())


def test_ingest_builds_everything_and_is_idempotent(tmp_settings: Settings) -> None:
    _snapshot(tmp_settings, "20261119", "2026-11-19T00:00:00Z")
    first = _ingest(tmp_settings)
    stats = first.versions["20261119"]
    assert first.active == "20261119"
    assert stats.chunks > 0 and stats.embedded == stats.chunks
    assert first.sqlite_rows == {"kingdom": 1, "power_moon": 2, "outfit": 2, "shop_item": 2}
    assert first.bm25_docs == stats.chunks

    second = _ingest(tmp_settings)
    assert second.versions["20261119"].embedded == 0
    assert second.versions["20261119"].deleted == 0
    assert _store(tmp_settings).count() == stats.chunks


def test_player_never_sees_internal_docs(tmp_settings: Settings) -> None:
    _snapshot(tmp_settings, "20261119", "2026-11-19T00:00:00Z")
    _ingest(tmp_settings)
    emb, store = HashEmbedder(), _store(tmp_settings)
    q = emb.embed_query("umbral tau calibra banco pruebas equipo CANARIO")
    player = store.query(q, 50, "jugador")
    admin = store.query(q, 50, "admin")
    assert player and all(h.chunk.rol_min == "jugador" for h in player)
    assert any(h.chunk.entity_type == "internal" for h in admin)
    assert all("CANARIO" not in h.chunk.text for h in player)

    bm25 = BM25Index.load(bm25_path(tmp_settings))
    assert all(h.meta["rol_min"] == "jugador" for h in bm25.search("canario tau", 50, "jugador"))
    assert bm25.search("canario", 50, "admin")


def test_new_snapshot_deprecates_previous_version(tmp_settings: Settings) -> None:
    _snapshot(tmp_settings, "20250101-asof", "2025-01-01T00:00:00Z", variant="\nOld fact.")
    _ingest(tmp_settings)
    _snapshot(tmp_settings, "20261119", "2026-11-19T00:00:00Z", variant="\nNew fact.")
    report = _ingest(tmp_settings)
    assert report.active == "20261119"
    assert report.versions["20250101-asof"].embedded == 0  # ya estaba indexada

    emb, store = HashEmbedder(), _store(tmp_settings)
    q = emb.embed_query("synthetic test kingdom waterfall fact")
    assert {h.chunk.version for h in store.query(q, 50, "admin")} == {"20261119"}
    old = store.query(q, 50, "admin", version="20250101-asof", include_deprecated=True)
    assert old and all(h.chunk.status == "deprecated" for h in old)

    bm25 = BM25Index.load(bm25_path(tmp_settings))
    assert {h.meta["version"] for h in bm25.search("fact", 50, "admin")} == {"20261119"}

    con = sqlite3.connect(tmp_settings.sqlite_path)
    assert dict(con.execute("SELECT key, value FROM meta"))["version"] == "20261119"


def test_changed_chunks_are_reembedded_and_stale_deleted(tmp_settings: Settings) -> None:
    _snapshot(tmp_settings, "20261119", "2026-11-19T00:00:00Z")
    _ingest(tmp_settings)
    # Re-descarga simulada: solo cambia el artículo del reino.
    write_snapshot(
        tmp_settings.raw_dir,
        "20261119",
        synthetic_pages("\n" + "extra word. " * 80),
        [],
        cutoff="2026-11-19T00:00:00Z",
        mode="latest",
        source="test",
        overwrite=True,
    )
    stats = _ingest(tmp_settings).versions["20261119"]
    assert 0 < stats.embedded < stats.chunks  # lunas, atuendos e internos no se re-embeben
    assert _store(tmp_settings).count() == stats.chunks
