"""Índice vectorial persistente en ChromaDB (HNSW, coseno, filtros por metadatos)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb

from marioqa.index.access import Role, chroma_where
from marioqa.ingest.chunking import Chunk

SECTIONS_SEP = " | "


def doc_id(version: str, chunk_id: str) -> str:
    """ID único en el índice: el mismo chunk_id puede existir en varias versiones."""
    return f"{version}::{chunk_id}"


def chunk_to_metadata(c: Chunk) -> dict[str, Any]:
    meta = c.model_dump(exclude={"text", "sections"})
    meta["sections"] = SECTIONS_SEP.join(c.sections)
    return meta


def metadata_to_chunk(text: str, meta: dict[str, Any]) -> Chunk:
    data = dict(meta)
    data["sections"] = data.get("sections", "").split(SECTIONS_SEP) if data.get("sections") else []
    return Chunk(text=text, **data)


@dataclass
class Hit:
    doc_id: str
    chunk: Chunk
    score: float


class VectorStore:
    def __init__(self, path: Path, collection: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(path))
        self.collection = self._client.get_or_create_collection(
            collection, configuration={"hnsw": {"space": "cosine"}}, embedding_function=None
        )

    def count(self) -> int:
        return self.collection.count()

    def hashes(self, version: str) -> dict[str, str]:
        """doc_id → hash de los chunks ya indexados de `version`."""
        got = self.collection.get(where={"version": version}, include=["metadatas"])
        return {i: m["hash"] for i, m in zip(got["ids"], got["metadatas"], strict=True)}

    def upsert(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if not chunks:
            return
        self.collection.upsert(
            ids=[doc_id(c.version, c.chunk_id) for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[chunk_to_metadata(c) for c in chunks],
        )

    def get_chunks(self, ids: list[str]) -> dict[str, Chunk]:
        """doc_id → Chunk (para candidatos que solo trajo la rama léxica)."""
        if not ids:
            return {}
        got = self.collection.get(ids=ids, include=["documents", "metadatas"])
        return {
            i: metadata_to_chunk(doc, meta)
            for i, doc, meta in zip(got["ids"], got["documents"], got["metadatas"], strict=True)
        }

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.collection.delete(ids=ids)

    def set_status(self, version: str, status: str) -> int:
        """Marca todos los chunks de `version` como active/deprecated. Devuelve cuántos cambió."""
        got = self.collection.get(where={"version": version}, include=["metadatas"])
        ids = [
            i for i, m in zip(got["ids"], got["metadatas"], strict=True) if m["status"] != status
        ]
        if ids:
            self.collection.update(ids=ids, metadatas=[{"status": status}] * len(ids))
        return len(ids)

    def query(
        self,
        embedding: list[float],
        k: int,
        role: Role,
        version: str | None = None,
        include_deprecated: bool = False,
    ) -> list[Hit]:
        """Top-k por similitud coseno, con el filtro de rol/estado/versión DENTRO de la consulta."""
        res = self.collection.query(
            query_embeddings=[embedding],
            n_results=k,
            where=chroma_where(role, version, include_deprecated),
            include=["documents", "metadatas", "distances"],
        )
        return [
            Hit(i, metadata_to_chunk(doc, meta), 1.0 - dist)
            for i, doc, meta, dist in zip(
                res["ids"][0],
                res["documents"][0],
                res["metadatas"][0],
                res["distances"][0],
                strict=True,
            )
        ]
