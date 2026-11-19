"""Construye el recuperador híbrido real a partir de la configuración y los índices en disco."""

from __future__ import annotations

from marioqa.config import Settings
from marioqa.index.bm25 import BM25Index
from marioqa.index.embeddings import E5Embedder
from marioqa.index.vector_store import VectorStore
from marioqa.ingest.pipeline import aliases_path, bm25_path
from marioqa.nlp.aliases import AliasTable
from marioqa.nlp.ner import EntityRecognizer
from marioqa.retrieval.hybrid import HybridRetriever
from marioqa.retrieval.reranker import CrossEncoderReranker


def build_retriever(settings: Settings) -> HybridRetriever:
    reranker = (
        CrossEncoderReranker(
            settings.rerank_model,
            max_length=settings.rerank_max_length,
            batch_size=settings.rerank_batch_size,
        )
        if settings.rerank_enabled
        else None
    )
    return HybridRetriever(
        settings,
        E5Embedder(settings.embed_model, batch_size=settings.embed_batch_size),
        VectorStore(settings.chroma_dir, settings.collection_name()),
        BM25Index.load(bm25_path(settings)),
        reranker,
    )


def build_recognizer(settings: Settings) -> EntityRecognizer:
    return EntityRecognizer(AliasTable.load(aliases_path(settings)))
