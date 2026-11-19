"""Embeddings: e5 multilingüe (producción) y un embedder por hashing (tests, sin red)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


class Embedder(Protocol):
    name: str

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class E5Embedder:
    """`intfloat/multilingual-e5-*`: prefijos `query:`/`passage:` y vectores normalizados."""

    def __init__(self, model_name: str, batch_size: int = 16, device: str = "cpu") -> None:
        self.name = model_name
        self.batch_size = batch_size
        self.device = device
        self._model = None

    @property
    def model(self):  # noqa: ANN201 - SentenceTransformer, carga perezosa
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.name, device=self.device)
        return self._model

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > self.batch_size * 4,
        )
        return vecs.tolist()

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode([PASSAGE_PREFIX + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([QUERY_PREFIX + text])[0]


class HashEmbedder:
    """Bolsa de palabras con hashing, normalizada. Determinista; solo para tests."""

    def __init__(self, dim: int = 128) -> None:
        self.name = f"hash-{dim}"
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in re.findall(r"\w+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 8) % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)
