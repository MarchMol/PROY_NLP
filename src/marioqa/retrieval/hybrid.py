"""Recuperador híbrido: vectorial + BM25 (top-N de cada rama) → fusión → rerank → top-k.

Los filtros de rol, estado y versión van DENTRO de la consulta a cada índice, y la
evidencia final pasa además por `validate_evidence` (segunda comprobación).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from marioqa.config import FusionMethod, Settings
from marioqa.index.access import Role
from marioqa.index.bm25 import BM25Index
from marioqa.index.embeddings import Embedder
from marioqa.index.vector_store import VectorStore
from marioqa.ingest.chunking import Chunk
from marioqa.logging_setup import get_logger
from marioqa.nlp.ner import QueryAnalysis
from marioqa.retrieval.fusion import rrf_fusion, weighted_fusion
from marioqa.retrieval.permissions import validate_evidence
from marioqa.retrieval.reranker import Reranker

log = get_logger(__name__)


@dataclass
class Candidate:
    doc_id: str
    chunk: Chunk
    dense: float | None = None
    lexical: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    fused: float = 0.0
    rerank: float | None = None

    @property
    def score(self) -> float:
        """Puntaje que usa la compuerta de evidencia: el del reranker si existe."""
        return self.rerank if self.rerank is not None else self.fused


@dataclass
class RetrievalResult:
    query: str
    candidates: list[Candidate] = field(default_factory=list)  # fusionados, en orden final
    top: list[Candidate] = field(default_factory=list)
    denied: list[Chunk] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def chunks(self) -> list[Chunk]:
        return [c.chunk for c in self.top]

    @property
    def max_score(self) -> float:
        return max((c.score for c in self.top), default=0.0)


class HybridRetriever:
    def __init__(
        self,
        settings: Settings,
        embedder: Embedder,
        store: VectorStore,
        bm25: BM25Index,
        reranker: Reranker | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.store = store
        self.bm25 = bm25
        self.reranker = reranker if settings.rerank_enabled else None

    def search(
        self,
        question: str,
        role: Role,
        *,
        analysis: QueryAnalysis | None = None,
        version: str | None = None,
        include_deprecated: bool = False,
        top_k: int | None = None,
    ) -> RetrievalResult:
        s = self.settings
        k = top_k or s.top_k_final
        query = analysis.expanded_query if analysis else question
        res = RetrievalResult(query=query)

        t = time.perf_counter()
        dense = self.store.query(
            self.embedder.embed_query(query), s.top_k_branch, role, version, include_deprecated
        )
        res.timings["dense"] = time.perf_counter() - t

        t = time.perf_counter()
        lexical = self.bm25.search(query, s.top_k_branch, role, version, include_deprecated)
        res.timings["lexical"] = time.perf_counter() - t

        cands: dict[str, Candidate] = {}
        for rank, h in enumerate(dense, 1):
            cands[h.doc_id] = Candidate(h.doc_id, h.chunk, dense=h.score, dense_rank=rank)
        missing = [h.doc_id for h in lexical if h.doc_id not in cands]
        fetched = self.store.get_chunks(missing)
        for rank, h in enumerate(lexical, 1):
            if h.doc_id in cands:
                cands[h.doc_id].lexical, cands[h.doc_id].lexical_rank = h.score, rank
            elif h.doc_id in fetched:
                cands[h.doc_id] = Candidate(
                    h.doc_id, fetched[h.doc_id], lexical=h.score, lexical_rank=rank
                )

        if s.fusion_method is FusionMethod.RRF:
            fused = rrf_fusion([[h.doc_id for h in dense], [h.doc_id for h in lexical]], s.rrf_k)
        else:
            fused = weighted_fusion(
                {h.doc_id: h.score for h in dense},
                {h.doc_id: h.score for h in lexical},
                s.fusion_w_dense,
                s.fusion_w_lexical,
            )
        boost_pages = set(analysis.pages) if analysis else set()
        for doc_id, c in cands.items():
            c.fused = fused.get(doc_id, 0.0) + (
                s.entity_boost if c.chunk.source in boost_pages else 0.0
            )
        ordered = sorted(cands.values(), key=lambda c: c.fused, reverse=True)

        if self.reranker is not None and ordered:
            head, tail = ordered[: s.rerank_top_n], ordered[s.rerank_top_n :]
            t = time.perf_counter()
            for c, score in zip(
                head, self.reranker.score(query, [c.chunk.text for c in head]), strict=True
            ):
                c.rerank = score
            res.timings["rerank"] = time.perf_counter() - t
            head.sort(key=lambda c: (c.rerank or 0.0, c.fused), reverse=True)
            ordered = head + tail

        check = validate_evidence([c.chunk for c in ordered[:k]], role, version, include_deprecated)
        if check.denied:
            log.warning("Permisos: %d chunks bloqueados en la 2ª validación", len(check.denied))
        allowed_ids = {id(ch) for ch in check.allowed}
        res.candidates = ordered
        res.top = [c for c in ordered[:k] if id(c.chunk) in allowed_ids]
        res.denied = check.denied
        return res
