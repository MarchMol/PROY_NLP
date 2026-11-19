"""Métricas de recuperación y de sistema."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence


def recall_at_k(relevance: Sequence[bool], k: int) -> float:
    """1 si algún relevante está en el top-k (definición del anteproyecto), si no 0."""
    return 1.0 if any(relevance[:k]) else 0.0


def reciprocal_rank(relevance: Sequence[bool]) -> float:
    for i, rel in enumerate(relevance, 1):
        if rel:
            return 1.0 / i
    return 0.0


def precision_at_k(relevance: Sequence[bool], k: int) -> float:
    """Relevantes en el top-k ÷ k (huecos cuentan como no relevantes)."""
    return sum(relevance[:k]) / k


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else float("nan")


def percentile(values: Sequence[float], p: float) -> float:
    """Percentil con interpolación lineal (p en [0, 100])."""
    if not values:
        return float("nan")
    xs = sorted(values)
    pos = (len(xs) - 1) * p / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
