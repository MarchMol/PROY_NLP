"""Construye el agente real (índices en disco, modelos y LLM según `.env`).

python -m marioqa.agent.factory "¿Cuántas lunas hay en el Reino de las Arenas?"
"""

from __future__ import annotations

import argparse
import sys

from marioqa.agent.audit import AuditLog
from marioqa.agent.graph import Agent, AgentResult
from marioqa.config import Settings, get_settings
from marioqa.llm.factory import get_llm
from marioqa.logging_setup import get_logger, setup_logging
from marioqa.retrieval.factory import build_recognizer, build_retriever
from marioqa.tools.game_data import GameDataTool
from marioqa.tools.search import SearchTool

log = get_logger(__name__)


def audit_path(settings: Settings):  # noqa: ANN201 - Path
    return settings.logs_dir / "audit.jsonl"


def build_agent(settings: Settings, *, warmup: bool = True) -> Agent:
    retriever = build_retriever(settings)
    if warmup:  # carga los modelos ahora y no en la primera pregunta
        retriever.embedder.embed_query("calentamiento")
        if retriever.reranker is not None:
            retriever.reranker.score("calentamiento", ["calentamiento"])
    return Agent(
        settings,
        build_recognizer(settings),
        SearchTool(retriever),
        GameDataTool(
            settings.sqlite_path, max_rows=settings.sql_max_rows, timeout_s=settings.sql_timeout_s
        ),
        get_llm(settings),
        AuditLog(audit_path(settings)),
    )


def print_result(r: AgentResult) -> None:
    o, t = r.output, r.trace
    print(f"\n❓ {r.question}")
    print(
        f"🧭 intención: {t['intent']} | herramienta: {o.tool_used} | "
        f"τ={t['tau']} máx={t['max_score']}"
    )
    print(f"💬 {o.answer}")
    flags = [
        f
        for f, on in (("ABSTENCIÓN", o.abstained), ("REVISIÓN HUMANA", o.needs_human_review))
        if on
    ]
    print("📚 Fuentes" + (f" ({', '.join(flags)})" if flags else "") + ":")
    for s in o.sources:
        print(f"   - {s.source} › {s.page} [{s.chunk_id}]  {s.url}")
    if t["issues"] or t["errors"]:
        print(f"⚠  {t['issues'] + t['errors']}")
    print(
        f"⏱  {r.latency_s:.2f}s | tokens {r.usage.input_tokens}+{r.usage.output_tokens} | "
        f"${r.cost_usd:.5f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--role", choices=["jugador", "admin"], default="jugador")
    parser.add_argument("--version", default=None, help="Solo admin: versión del corpus")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    print_result(build_agent(settings).ask(args.question, args.role, args.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
