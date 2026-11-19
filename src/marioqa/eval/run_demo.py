"""Corre la línea base sobre las preguntas demo y guarda respuestas + métricas.

python -m marioqa.eval.run_demo                 # LLM según .env
python -m marioqa.eval.run_demo --mock          # sin costo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from marioqa.config import ROOT_DIR, LLMProvider, get_settings
from marioqa.eval.cases import BenchCase, load_cases
from marioqa.eval.metrics import mean, percentile, precision_at_k, recall_at_k, reciprocal_rank
from marioqa.logging_setup import setup_logging
from marioqa.rag.baseline import BaselineRAG, RAGResult, print_result

K = 5


def case_record(case: BenchCase, r: RAGResult) -> dict:
    relevance = [case.is_relevant(h.chunk) for h in r.retrieved]
    rec = {
        "id": case.id,
        "question": case.question,
        "answer": r.output.model_dump(),
        "retrieved": [
            {
                "chunk_id": h.chunk.chunk_id,
                "source": h.chunk.source,
                "page": h.chunk.page,
                "score": round(h.score, 4),
                "relevant": rel,
            }
            for h, rel in zip(r.retrieved, relevance, strict=True)
        ],
        "latency_s": round(r.latency_s, 3),
        "timings": {k: round(v, 3) for k, v in r.timings.items()},
        "tokens": {"input": r.usage.input_tokens, "output": r.usage.output_tokens},
        "cost_usd": r.cost_usd,
        "error": r.error,
    }
    if case.has_retrieval_labels:
        rec["metrics"] = {
            f"recall@{K}": recall_at_k(relevance, K),
            "rr": reciprocal_rank(relevance),
            f"precision@{K}": precision_at_k(relevance, K),
        }
    return rec


def summarize(records: list[dict]) -> dict:
    labeled = [r["metrics"] for r in records if "metrics" in r]
    lat = [r["latency_s"] for r in records]
    return {
        "n_cases": len(records),
        "n_labeled": len(labeled),
        f"recall@{K}": mean([m[f"recall@{K}"] for m in labeled]),
        "mrr": mean([m["rr"] for m in labeled]),
        f"precision@{K}": mean([m[f"precision@{K}"] for m in labeled]),
        "latency_mean_s": mean(lat),
        "latency_p95_s": percentile(lat, 95),
        "cost_mean_usd": mean([r["cost_usd"] for r in records]),
        "tokens_mean": mean([r["tokens"]["input"] + r["tokens"]["output"] for r in records]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT_DIR / "eval" / "demo.jsonl")
    parser.add_argument(
        "--out", type=Path, default=ROOT_DIR / "eval" / "results" / "baseline_demo.json"
    )
    parser.add_argument("--mock", action="store_true", help="Forzar LLM mock")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.mock:
        settings = settings.model_copy(update={"llm_provider": LLMProvider.MOCK})
    setup_logging(settings.log_level)
    rag = BaselineRAG.from_settings(settings)
    rag.embedder.embed_query("calentamiento")  # carga el modelo fuera del cronómetro

    records = []
    for case in load_cases(args.cases):
        result = rag.ask(case.question)
        print_result(result)
        records.append(case_record(case, result))

    summary = summarize(records)
    payload = {
        "system": "baseline (vectorial top-5 + LLM)",
        "llm": {"provider": settings.llm_provider.value, "model": rag.llm.model},
        "collection": settings.collection_name(),
        "summary": summary,
        "cases": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n📊 Resumen:", json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"Guardado en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
