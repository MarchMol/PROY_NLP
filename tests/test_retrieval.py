"""Tests de fusión, rerank, recuperador híbrido y permisos (datos SINTÉTICOS, sin modelos)."""

from __future__ import annotations

import pytest

from marioqa.config import FusionMethod, Settings
from marioqa.index.bm25 import BM25Index
from marioqa.index.embeddings import HashEmbedder
from marioqa.index.vector_store import Hit, VectorStore
from marioqa.ingest.chunking import Chunk, whitespace_counter
from marioqa.ingest.pipeline import bm25_path, run_ingest
from marioqa.ingest.snapshot import write_snapshot
from marioqa.nlp.ner import QueryAnalysis
from marioqa.retrieval.fusion import minmax, rrf_fusion, weighted_fusion
from marioqa.retrieval.hybrid import HybridRetriever
from marioqa.retrieval.permissions import validate_evidence
from marioqa.retrieval.reranker import LexicalReranker
from tests.synthetic import synthetic_pages


def chunk(
    cid: str,
    *,
    rol: str = "jugador",
    status: str = "active",
    version: str = "v1",
    source: str = "S",
    text: str = "x",
) -> Chunk:
    return Chunk(
        chunk_id=cid,
        text=text,
        source=source,
        url="u",
        page="P",
        sections=["P"],
        version=version,
        revid=1,
        cutoff="c",
        hash="h",
        entity_type="kingdom",
        status=status,
        rol_min=rol,
        n_tokens=1,
        position=0,
    )


# ------------------------------------------------------------------- fusión
def test_minmax_and_weighted_fusion() -> None:
    assert minmax({"a": 2, "b": 4, "c": 3}) == {"a": 0.0, "b": 1.0, "c": 0.5}
    assert minmax({"a": 7}) == {"a": 1.0} and minmax({}) == {}
    fused = weighted_fusion({"a": 0.9, "b": 0.5}, {"b": 10.0, "c": 2.0}, 0.6, 0.4)
    assert fused["b"] == pytest.approx(0.4)  # 0.6*0 + 0.4*1
    assert fused["a"] == pytest.approx(0.6)  # solo en la rama densa
    assert fused["c"] == pytest.approx(0.0)


def test_rrf_rewards_agreement() -> None:
    fused = rrf_fusion([["a", "b", "c"], ["b", "a", "d"]], k=60)
    assert fused["a"] == fused["b"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["a"] > fused["c"] > fused["d"] or fused["c"] == fused["d"]


def test_lexical_reranker_is_bounded() -> None:
    scores = LexicalReranker().score("samurai armor bowser", ["samurai armor bowser", "nada", ""])
    assert scores == [1.0, 0.0, 0.0]


# ---------------------------------------------------------------- permisos
def test_validate_evidence_second_check() -> None:
    chunks = [
        chunk("a"),
        chunk("b", rol="admin"),
        chunk("c", status="deprecated"),
        chunk("d", version="v0"),
    ]
    player = validate_evidence(chunks, "jugador", version="v1")
    assert [c.chunk_id for c in player.allowed] == ["a"]
    assert {c.chunk_id for c in player.denied} == {"b", "c", "d"}
    admin = validate_evidence(chunks, "admin")
    assert [c.chunk_id for c in admin.allowed] == ["a", "b", "d"]


# ------------------------------------------------------------------ híbrido
@pytest.fixture
def retriever(tmp_settings: Settings) -> HybridRetriever:
    write_snapshot(
        tmp_settings.raw_dir,
        "20261119",
        synthetic_pages(),
        [],
        cutoff="2026-11-19T00:00:00Z",
        mode="latest",
        source="t",
    )
    emb = HashEmbedder()
    run_ingest(tmp_settings, emb, whitespace_counter)
    return HybridRetriever(
        tmp_settings,
        emb,
        VectorStore(tmp_settings.chroma_dir, tmp_settings.collection_name()),
        BM25Index.load(bm25_path(tmp_settings)),
        LexicalReranker(),
    )


def test_hybrid_merges_branches_and_reranks(retriever: HybridRetriever) -> None:
    res = retriever.search("Behind the Synthetic Falls waterfall", "jugador")
    assert 0 < len(res.top) <= retriever.settings.top_k_final
    assert res.top[0].rerank is not None and res.top[0].score == res.top[0].rerank
    assert "Behind the Synthetic Falls" in res.top[0].chunk.text
    scores = [c.rerank for c in res.top]
    assert scores == sorted(scores, reverse=True)
    assert any(c.dense is not None and c.lexical is not None for c in res.candidates)
    assert {"dense", "lexical", "rerank"} <= set(res.timings)


def test_hybrid_never_returns_internal_to_player(retriever: HybridRetriever) -> None:
    res = retriever.search("umbral tau calibra banco pruebas CANARIO", "jugador", top_k=10)
    assert all(c.chunk.rol_min == "jugador" for c in res.candidates)
    admin = retriever.search("umbral tau calibra banco pruebas CANARIO", "admin", top_k=10)
    assert any(c.chunk.rol_min == "admin" for c in admin.top)


def test_entity_boost_and_rrf(retriever: HybridRetriever) -> None:
    analysis = QueryAnalysis("helmet", expansion_terms=["Test Helmet"])
    retriever.reranker = None
    plain = retriever.search("helmet", "jugador", analysis=analysis)
    assert all(c.rerank is None for c in plain.top)
    retriever.settings = retriever.settings.model_copy(update={"fusion_method": FusionMethod.RRF})
    rrf = retriever.search("helmet", "jugador", analysis=analysis)
    assert rrf.top and all(0 < c.fused < 1 for c in rrf.top)


class LeakyStore(VectorStore):
    """Simula un índice cuyo filtro falla: devuelve también un chunk de admin."""

    def __init__(self, inner: VectorStore) -> None:
        self.inner = inner

    def query(self, embedding, k, role, version=None, include_deprecated=False):  # type: ignore[override]
        leaked = chunk("secreto", rol="admin", text="CANARIO interno")
        return [Hit("v::secreto", leaked, 0.99), *self.inner.query(embedding, k, role, version)]

    def get_chunks(self, ids):  # type: ignore[override]
        return self.inner.get_chunks(ids)


def test_second_permission_check_blocks_index_leak(retriever: HybridRetriever) -> None:
    retriever.store = LeakyStore(retriever.store)
    res = retriever.search("waterfall", "jugador")
    assert all(c.chunk.rol_min == "jugador" for c in res.top)
    assert [c.chunk_id for c in res.denied] == ["secreto"]
