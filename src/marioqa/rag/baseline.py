"""Línea base: embedding de la pregunta → top-k vectorial → LLM con citas.

Sin guardrails, NER, BM25, rerank ni compuerta de evidencia: es el punto de comparación.

    python -m marioqa.rag.baseline "¿Dónde compro el atuendo de samurái?"
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

from marioqa.config import Settings, get_settings
from marioqa.index.access import Role
from marioqa.index.embeddings import E5Embedder, Embedder
from marioqa.index.vector_store import Hit, VectorStore
from marioqa.llm.base import LLMClient, LLMError, LLMUsage
from marioqa.llm.factory import get_llm
from marioqa.logging_setup import setup_logging
from marioqa.rag.prompts import SYSTEM_PROMPT, build_user_prompt
from marioqa.schemas import AnswerOutput, GroundedDraft, SourceRef


@dataclass
class RAGResult:
    question: str
    output: AnswerOutput
    retrieved: list[Hit]
    usage: LLMUsage = field(default_factory=LLMUsage)
    cost_usd: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    @property
    def latency_s(self) -> float:
        return sum(self.timings.values())


def draft_to_output(draft: GroundedDraft, hits: list[Hit]) -> AnswerOutput:
    """Las fuentes salen de la evidencia recuperada; los ids que no existen se descartan."""
    by_id = {h.chunk.chunk_id: h.chunk for h in hits}
    cited = [cid for cid in dict.fromkeys(draft.cited_chunk_ids) if cid in by_id]
    sources = [
        SourceRef(source=by_id[c].source, page=by_id[c].page, chunk_id=c, url=by_id[c].url)
        for c in cited
    ]
    return AnswerOutput(
        answer=draft.answer,
        sources=sources,
        abstained=draft.insufficient_context or not sources,
        tool_used="search_documents",
    )


class BaselineRAG:
    def __init__(
        self, settings: Settings, embedder: Embedder, store: VectorStore, llm: LLMClient
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.store = store
        self.llm = llm

    @classmethod
    def from_settings(cls, settings: Settings) -> BaselineRAG:
        return cls(
            settings,
            E5Embedder(settings.embed_model, batch_size=settings.embed_batch_size),
            VectorStore(settings.chroma_dir, settings.collection_name()),
            get_llm(settings),
        )

    def retrieve(self, question: str, role: Role = "jugador") -> list[Hit]:
        return self.store.query(
            self.embedder.embed_query(question), self.settings.top_k_final, role
        )

    def ask(self, question: str, role: Role = "jugador") -> RAGResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        hits = self.retrieve(question, role)
        timings["retrieval"] = time.perf_counter() - t0

        t1 = time.perf_counter()
        try:
            res = self.llm.generate(
                SYSTEM_PROMPT, build_user_prompt(question, [h.chunk for h in hits]), GroundedDraft
            )
        except LLMError as exc:
            timings["generation"] = time.perf_counter() - t1
            out = AnswerOutput(
                answer="El modelo de lenguaje no está disponible; no se generó respuesta.",
                abstained=True,
                tool_used="search_documents",
                needs_human_review=True,
            )
            return RAGResult(question, out, hits, timings=timings, error=str(exc))
        timings["generation"] = time.perf_counter() - t1
        cost = res.usage.cost_usd(
            self.settings.llm_price_input_per_mtok, self.settings.llm_price_output_per_mtok
        )
        return RAGResult(
            question, draft_to_output(res.output, hits), hits, res.usage, cost, timings
        )


def print_result(r: RAGResult) -> None:
    print(f"\n❓ {r.question}")
    print(f"💬 {r.output.answer}")
    flag = " (ABSTENCIÓN)" if r.output.abstained else ""
    print(f"📚 Fuentes{flag}:")
    for s in r.output.sources:
        print(f"   - {s.source} › {s.page} [{s.chunk_id}]  {s.url}")
    print(
        "🔎 Recuperados:",
        ", ".join(f"{h.chunk.source}›{h.chunk.page} ({h.score:.3f})" for h in r.retrieved),
    )
    print(
        f"⏱  {r.latency_s:.2f}s (recup. {r.timings.get('retrieval', 0):.2f}s, "
        f"gen. {r.timings.get('generation', 0):.2f}s) | tokens {r.usage.input_tokens}+"
        f"{r.usage.output_tokens} | ${r.cost_usd:.5f}" + (f" | ERROR: {r.error}" if r.error else "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--role", choices=["jugador", "admin"], default="jugador")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    print_result(BaselineRAG.from_settings(settings).ask(args.question, args.role))
    return 0


if __name__ == "__main__":
    sys.exit(main())
