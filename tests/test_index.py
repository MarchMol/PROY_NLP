"""Tests de índices: reglas de acceso, Chroma, BM25 y registro de versiones (datos SINTÉTICOS)."""

from __future__ import annotations

from pathlib import Path

from marioqa.index.access import chroma_where, is_allowed, readable_roles
from marioqa.index.bm25 import BM25Index, tokenize
from marioqa.index.embeddings import HashEmbedder
from marioqa.index.vector_store import VectorStore, doc_id
from marioqa.index.versions import VersionRegistry
from marioqa.ingest.chunking import Chunk


def chunk(
    cid: str, text: str, *, version: str = "v1", rol: str = "jugador", status: str = "active"
) -> Chunk:
    return Chunk(
        chunk_id=cid,
        text=text,
        source="S",
        url="u",
        page="P",
        sections=["P", "Q"],
        version=version,
        revid=1,
        cutoff="c",
        hash=f"h-{cid}-{text}",
        entity_type="kingdom",
        status=status,
        rol_min=rol,
        n_tokens=len(text.split()),
        position=0,
    )


CHUNKS = [
    chunk("a", "waterfall power moon cascade"),
    chunk("b", "samurai armor crazy cap bowser"),
    chunk("c", "internal secret calibration notes", rol="admin"),
    chunk("a", "waterfall power moon cascade old", version="v0", status="deprecated"),
]


# ------------------------------------------------------------------ acceso
def test_roles_and_filters() -> None:
    assert readable_roles("jugador") == ["jugador"]
    assert set(readable_roles("admin")) == {"jugador", "admin"}
    assert chroma_where("jugador") == {
        "$and": [{"rol_min": {"$in": ["jugador"]}}, {"status": "active"}]
    }
    meta = {"rol_min": "admin", "status": "active", "version": "v1"}
    assert not is_allowed(meta, "jugador")
    assert is_allowed(meta, "admin", version="v1")
    assert not is_allowed({**meta, "status": "deprecated"}, "admin")
    assert is_allowed({**meta, "status": "deprecated"}, "admin", include_deprecated=True)


# -------------------------------------------------------------- vectorial
def _store(tmp_path: Path) -> tuple[VectorStore, HashEmbedder]:
    emb = HashEmbedder()
    store = VectorStore(tmp_path / "chroma", "test_chunks_w60")
    store.upsert(CHUNKS, emb.embed_passages([c.text for c in CHUNKS]))
    return store, emb


def test_vector_store_filters_inside_query(tmp_path: Path) -> None:
    store, emb = _store(tmp_path)
    q = emb.embed_query("internal secret calibration waterfall samurai")
    player = {h.doc_id for h in store.query(q, 10, "jugador")}
    admin = {h.doc_id for h in store.query(q, 10, "admin")}
    assert player == {doc_id("v1", "a"), doc_id("v1", "b")}
    assert admin == player | {doc_id("v1", "c")}
    old = store.query(q, 10, "admin", version="v0", include_deprecated=True)
    assert [h.doc_id for h in old] == [doc_id("v0", "a")]


def test_vector_store_roundtrips_metadata_and_scores(tmp_path: Path) -> None:
    store, emb = _store(tmp_path)
    (top,) = store.query(emb.embed_query("samurai armor crazy cap bowser"), 1, "jugador")
    assert top.chunk.chunk_id == "b" and top.chunk.sections == ["P", "Q"]
    assert 0.99 <= top.score <= 1.0001


def test_vector_store_status_and_hashes(tmp_path: Path) -> None:
    store, emb = _store(tmp_path)
    assert store.hashes("v1")[doc_id("v1", "b")] == CHUNKS[1].hash
    assert store.set_status("v1", "deprecated") == 3
    assert store.set_status("v1", "deprecated") == 0
    assert store.query(emb.embed_query("waterfall"), 10, "admin") == []
    store.delete([doc_id("v0", "a")])
    assert store.count() == 3


# -------------------------------------------------------------------- BM25
def test_tokenize_lowercases_strips_accents_and_stopwords() -> None:
    assert tokenize("¿Dónde está el Atuendo de Samurái? Bowser's") == [
        "donde",
        "esta",
        "atuendo",
        "samurai",
        "bowser's",
    ]


def test_bm25_search_respects_access_and_persists(tmp_path: Path) -> None:
    idx = BM25Index.from_chunks(CHUNKS)
    hits = idx.search("secret calibration waterfall", 10, "jugador")
    assert [h.doc_id for h in hits] == [doc_id("v1", "a")]
    assert {h.doc_id for h in idx.search("secret calibration", 10, "admin")} == {doc_id("v1", "c")}
    assert idx.search("zzz nada", 10, "admin") == []

    path = tmp_path / "bm25.json"
    idx.set_status("v0", "active")
    idx.save(path)
    loaded = BM25Index.load(path)
    assert {h.doc_id for h in loaded.search("waterfall", 10, "jugador")} == {
        doc_id("v1", "a"),
        doc_id("v0", "a"),
    }


# --------------------------------------------------------------- versiones
def test_registry_activates_latest_cutoff(tmp_path: Path) -> None:
    reg = VersionRegistry()
    reg.register("20261119", "2026-11-19T00:00:00Z", 10)
    reg.register("20250101-asof", "2025-01-01T00:00:00Z", 9)
    assert reg.activate_latest() == "20261119"
    assert reg.versions["20250101-asof"].status == "deprecated"
    path = tmp_path / "v.json"
    reg.save(path)
    assert VersionRegistry.load(path).active == "20261119"
    assert VersionRegistry.load(tmp_path / "missing.json").active is None
