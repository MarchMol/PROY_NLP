"""Reranker cross-encoder (bge-reranker-v2-m3, CPU) y uno léxico para tests."""

from __future__ import annotations

import math
import re
from typing import Protocol


class Reranker(Protocol):
    name: str

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Relevancia en [0, 1] de cada pasaje para la consulta."""
        ...


class CrossEncoderReranker:
    def __init__(
        self, model_name: str, *, max_length: int = 512, batch_size: int = 8, device: str = "cpu"
    ) -> None:
        self.name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self.device = device
        self._model = None

    @property
    def model(self):  # noqa: ANN201 - CrossEncoder, carga perezosa
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.name, device=self.device, max_length=self.max_length)
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        import torch

        # Identity: logits crudos. Con None se aplicaría la activación por defecto del modelo
        # (sigmoide) y la de abajo quedaría duplicada.
        logits = self.model.predict(
            [(query, p) for p in passages],
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            activation_fn=torch.nn.Identity(),
        )
        # bge-reranker devuelve logits; la sigmoide los lleva a [0, 1] para comparar con τ.
        return [1.0 / (1.0 + math.exp(-float(x))) for x in logits]


class LexicalReranker:
    """Proporción de términos de la consulta presentes en el pasaje. Determinista; para tests."""

    name = "lexical"

    def score(self, query: str, passages: list[str]) -> list[float]:
        q = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2}
        if not q:
            return [0.0] * len(passages)
        return [len(q & set(re.findall(r"\w+", p.lower()))) / len(q) for p in passages]
