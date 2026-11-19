"""Compara configuraciones de recuperación (sin LLM) sobre un archivo de casos.

python -m marioqa.eval.run_retrieval                       # preguntas demo
python -m marioqa.eval.run_retrieval --cases eval/bench.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from marioqa.config import ROOT_DIR, FusionMethod, Settings, get_settings
from marioqa.eval.cases import BenchCase, load_cases
from marioqa.eval.metrics import mean, percentile, precision_at_k, recall_at_k, reciprocal_rank
from marioqa.ingest.chunking import Chunk
from marioqa.logging_setup import setup_logging
from marioqa.nlp.ner import EntityRecognizer
from marioqa.retrieval.factory import build_recognizer, build_retriever
from marioqa.retrieval.hybrid import HybridRetriever

K = 5


def _baseline(retriever: HybridRetriever, q: str) -> list[Chunk]:
    emb = retriever.embedder.embed_query(q)
    return [h.chunk for h in retriever.store.query(emb, K, "jugador")]


def evaluate(
    name: str,
    cases: list[BenchCase],
    run,  # noqa: ANN001 - callable(question) -> list[Chunk]
) -> dict:
    per_case, lat = [], []
    for case in cases:
        t = time.perf_counter()
        chunks = run(case.question)
        lat.append(time.perf_counter() - t)
        rel = [case.is_relevant(c) for c in chunks]
        per_case.append(
            {
                "id": case.id,
                f"recall@{K}": recall_at_k(rel, K),
                "rr": reciprocal_rank(rel),
                f"precision@{K}": precision_at_k(rel, K),
                "top": [
                    f"{c.source} › {c.page}" + (" ✓" if r else "")
                    for c, r in zip(chunks, rel, strict=True)
                ],
            }
        )
    return {
        "config": name,
        f"recall@{K}": mean([p[f"recall@{K}"] for p in per_case]),
        "mrr": mean([p["rr"] for p in per_case]),
        f"precision@{K}": mean([p[f"precision@{K}"] for p in per_case]),
        "latency_mean_s": mean(lat),
        "latency_p95_s": percentile(lat, 95),
        "cases": per_case,
    }


def run_all(settings: Settings, cases: list[BenchCase]) -> list[dict]:
    retriever = build_retriever(settings)
    ner: EntityRecognizer = build_recognizer(settings)
    reranker = retriever.reranker
    retriever.embedder.embed_query("calentamiento")
    if reranker is not None:
        reranker.score("calentamiento", ["calentamiento"])

    def hybrid(method: FusionMethod, rerank: bool):  # noqa: ANN202
        def run(q: str) -> list[Chunk]:
            retriever.settings = settings.model_copy(update={"fusion_method": method})
            retriever.reranker = reranker if rerank else None
            return retriever.search(q, "jugador", analysis=ner.analyze(q)).chunks

        return run

    labeled = [c for c in cases if c.has_retrieval_labels]
    configs = [
        ("baseline (vectorial top-5)", lambda q: _baseline(retriever, q)),
        ("híbrida ponderada + NER", hybrid(FusionMethod.WEIGHTED, False)),
        ("híbrida RRF + NER", hybrid(FusionMethod.RRF, False)),
        ("híbrida ponderada + NER + rerank", hybrid(FusionMethod.WEIGHTED, True)),
        ("híbrida RRF + NER + rerank", hybrid(FusionMethod.RRF, True)),
    ]
    return [evaluate(name, labeled, fn) for name, fn in configs]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT_DIR / "eval" / "demo.jsonl")
    parser.add_argument(
        "--out", type=Path, default=ROOT_DIR / "eval" / "results" / "retrieval_demo.json"
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    results = run_all(settings, load_cases(args.cases))

    print(f"\n{'config':<36} {'R@5':>5} {'MRR':>5} {'P@5':>5} {'lat':>6} {'p95':>6}")
    for r in results:
        print(
            f"{r['config']:<36} {r[f'recall@{K}']:>5.2f} {r['mrr']:>5.2f} "
            f"{r[f'precision@{K}']:>5.2f} {r['latency_mean_s']:>5.2f}s {r['latency_p95_s']:>5.2f}s"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Guardado en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
