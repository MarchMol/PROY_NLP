"""Fusión de rankings: puntajes min-max normalizados con pesos, o Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Mapping


def minmax(scores: Mapping[str, float]) -> dict[str, float]:
    """Normaliza a [0, 1]. Si todos empatan, todos valen 1 (un solo candidato no es "malo")."""
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi == lo:
        return dict.fromkeys(scores, 1.0)
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def weighted_fusion(
    dense: Mapping[str, float], lexical: Mapping[str, float], w_dense: float, w_lexical: float
) -> dict[str, float]:
    """Suma ponderada de puntajes normalizados; quien falta en una rama aporta 0 en ella."""
    nd, nl = minmax(dense), minmax(lexical)
    return {k: w_dense * nd.get(k, 0.0) + w_lexical * nl.get(k, 0.0) for k in set(nd) | set(nl)}


def rrf_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion (Cormack et al., 2009): Σ 1 / (k + rango)."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, 1):
            out[doc] = out.get(doc, 0.0) + 1.0 / (k + rank)
    return out
