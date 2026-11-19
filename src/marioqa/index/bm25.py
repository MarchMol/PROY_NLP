"""Índice léxico BM25 (rank_bm25) sobre los mismos chunks, persistido en JSON."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from marioqa.index.access import Role, is_allowed
from marioqa.index.vector_store import doc_id
from marioqa.ingest.chunking import Chunk

# Solo stopwords muy frecuentes: los nombres propios del juego deben pesar.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "de",
        "la",
        "el",
        "en",
        "y",
        "que",
        "los",
        "las",
        "un",
        "una",
        "por",
        "con",
        "para",
        "del",
        "al",
        "lo",
        "se",
        "su",
        "sus",
        "es",
    ]
)
META_KEYS = ("chunk_id", "version", "status", "rol_min", "source", "page", "entity_type")


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return [t for t in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text) if t not in STOPWORDS]


@dataclass
class LexHit:
    doc_id: str
    meta: dict[str, str]
    score: float


class BM25Index:
    def __init__(
        self, ids: list[str], tokens: list[list[str]], metas: list[dict[str, str]]
    ) -> None:
        self.ids = ids
        self.tokens = tokens
        self.metas = metas
        self._pos = {d: i for i, d in enumerate(ids)}
        self._bm25 = BM25Okapi(tokens) if tokens else None

    @classmethod
    def from_chunks(cls, chunks: list[Chunk]) -> BM25Index:
        return cls(
            [doc_id(c.version, c.chunk_id) for c in chunks],
            [tokenize(c.text) for c in chunks],
            [{k: str(getattr(c, k)) for k in META_KEYS} for c in chunks],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"ids": self.ids, "tokens": self.tokens, "metas": self.metas}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> BM25Index:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["ids"], data["tokens"], data["metas"])

    def set_status(self, version: str, status: str) -> None:
        for m in self.metas:
            if m["version"] == version:
                m["status"] = status

    def search(
        self,
        query: str,
        k: int,
        role: Role,
        version: str | None = None,
        include_deprecated: bool = False,
    ) -> list[LexHit]:
        """Top-k BM25 entre los documentos que el rol puede leer (filtro antes de rankear)."""
        q = tokenize(query)
        if not q or self._bm25 is None:
            return []
        scores = self._bm25.get_scores(q)
        q_set = set(q)
        # Coincidencia léxica real: con Okapi, un término presente en la mitad del corpus
        # tiene IDF ≈ 0, así que `score > 0` no sirve como criterio de "hay match".
        candidates = [
            i
            for i, m in enumerate(self.metas)
            if is_allowed(m, role, version, include_deprecated) and q_set & set(self.tokens[i])
        ]
        candidates.sort(key=lambda i: scores[i], reverse=True)
        return [LexHit(self.ids[i], self.metas[i], float(scores[i])) for i in candidates[:k]]
