"""Grafo con estado (LangGraph) del flujo de consulta.

    guard_input ─▶ analyze (NER + router) ─┬─▶ refuse (fuera de dominio / ambigua / acción crítica)
         │                                 ├─▶ sql ─┬─▶ permissions ─▶ gate ─▶ sql_answer ─┐
         └─▶ (rechazo)                     │        └─▶ search (mixta o respaldo)          │
                                           └─▶ search ─▶ permissions ─▶ gate ─▶ generate ──┤
                                                                         └─(τ)──────────────┤
                                                                                 validate ◀─┘

Razonar (router) → actuar (herramienta) → observar (permisos + compuerta τ) → decidir (responder,
abstenerse o pedir revisión humana). Máximo `MAX_TOOL_CALLS` llamadas por consulta.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from marioqa.agent.audit import AuditLog
from marioqa.agent.output_guard import cited_ids_in_text, validate_output
from marioqa.agent.router import Intent, RouteDecision, route
from marioqa.agent.sql_answers import compose_answer, to_evidence
from marioqa.config import Settings
from marioqa.guardrails.input import RejectReason, check_input
from marioqa.index.access import Role
from marioqa.ingest.chunking import Chunk
from marioqa.llm.base import LLMClient, LLMError, LLMUsage
from marioqa.logging_setup import get_logger
from marioqa.nlp.ner import EntityRecognizer, QueryAnalysis
from marioqa.rag.prompts import SYSTEM_PROMPT, build_user_prompt
from marioqa.retrieval.hybrid import RetrievalResult
from marioqa.retrieval.permissions import validate_evidence
from marioqa.schemas import AnswerOutput, GroundedDraft, SourceRef
from marioqa.tools.base import (
    QUERY_GAME_DATA,
    SEARCH_DOCUMENTS,
    ToolBudget,
    ToolError,
    run_with_timeout,
)
from marioqa.tools.game_data import GameDataResult, GameDataTool
from marioqa.tools.search import SearchInput, SearchTool

log = get_logger(__name__)

MSG_OUT_OF_DOMAIN = (
    "Solo puedo responder preguntas sobre Super Mario Odyssey con información de la Super "
    "Mario Wiki. Esa pregunta queda fuera de lo que cubre mi base de conocimiento."
)
MSG_CRITICAL = (
    "No puedo ejecutar acciones administrativas como reindexar el corpus desde una pregunta. "
    "Esa acción solo la hace un administrador desde la línea de comandos, con confirmación "
    "explícita."
)
MSG_AMBIGUOUS = (
    "Tu pregunta es ambigua: no indica a qué zona, reino, luna o atuendo te refieres. "
    "¿Puedes decirme cuál? Por ejemplo: «¿Qué necesito para desbloquear el Reino de Bowser?»."
)
MSG_NO_EVIDENCE = (
    "No encontré información suficiente en las fuentes para responder con seguridad. "
    "Intenta reformular la pregunta o nombrar el reino, la luna o el atuendo."
)
MSG_UNAVAILABLE = (
    "El servicio de búsqueda no está disponible en este momento; no puedo responder sin "
    "consultar las fuentes. Intenta de nuevo más tarde."
)
MSG_LLM_DOWN = (
    "El modelo de lenguaje no está disponible, así que no generé una respuesta redactada. "
    "Estos son los fragmentos más relevantes que encontré:"
)


class AgentState(TypedDict, total=False):
    request_id: str
    question_raw: str
    role: Role
    version: str | None
    question: str
    input: dict[str, Any]
    analysis: QueryAnalysis
    route: RouteDecision
    budget: ToolBudget
    sql: GameDataResult | None
    retrieval: RetrievalResult | None
    evidence: list[Chunk]
    denied: list[str]
    max_score: float
    gate_passed: bool
    output: AnswerOutput
    usage: LLMUsage
    tool_calls: list[dict[str, Any]]
    issues: list[str]
    errors: list[str]
    timings: dict[str, float]
    llm_model: str | None


@dataclass
class AgentResult:
    request_id: str
    question: str
    role: Role
    output: AnswerOutput
    trace: dict[str, Any]
    usage: LLMUsage = field(default_factory=LLMUsage)
    cost_usd: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
    evidence: list[Chunk] = field(default_factory=list)

    @property
    def latency_s(self) -> float:
        return self.timings.get("total", sum(self.timings.values()))


def _timed(name: str, fn: Callable[[AgentState], dict]) -> Callable[[AgentState], dict]:
    def wrapper(state: AgentState) -> dict:
        t = time.perf_counter()
        update = fn(state)
        timings = state["timings"]
        timings[name] = timings.get(name, 0.0) + time.perf_counter() - t
        return update

    return wrapper


def _degraded_answer(evidence: list[Chunk]) -> str:
    lines = [MSG_LLM_DOWN]
    for c in evidence[:5]:
        body = c.text.split("\n", 1)[-1].replace("\n", " ")
        lines.append(f"- {c.source} › {c.page}: {body[:220]}{'…' if len(body) > 220 else ''}")
    return "\n".join(lines)


def _sources(ids: list[str], evidence: list[Chunk]) -> list[SourceRef]:
    by_id = {c.chunk_id: c for c in evidence}
    return [
        SourceRef(source=by_id[i].source, page=by_id[i].page, chunk_id=i, url=by_id[i].url)
        for i in dict.fromkeys(ids)
        if i in by_id
    ]


class Agent:
    """Orquestador. Las dependencias se inyectan para poder probarlo sin modelos ni red."""

    def __init__(
        self,
        settings: Settings,
        recognizer: EntityRecognizer,
        search: SearchTool | None,
        game_data: GameDataTool | None,
        llm: LLMClient,
        audit: AuditLog | None = None,
    ) -> None:
        self.settings = settings
        self.recognizer = recognizer
        self.search_tool = search
        self.game_tool = game_data
        self.llm = llm
        self.audit = audit
        self.graph = self._build()

    # ================================================================ nodos
    def _guard_input(self, s: AgentState) -> dict:
        chk = check_input(s["question_raw"], self.settings.max_question_chars)
        update: dict[str, Any] = {
            "question": chk.question,
            "input": {
                "ok": chk.ok,
                "reason": chk.reason.value if chk.reason else None,
                "injection_rules": chk.injection_rules,
                "pii_found": chk.pii_found,
            },
        }
        if not chk.ok:
            update["output"] = AnswerOutput(
                answer=chk.message,
                abstained=True,
                needs_human_review=chk.reason is RejectReason.INJECTION,
            )
        return update

    def _analyze(self, s: AgentState) -> dict:
        analysis = self.recognizer.analyze(s["question"])
        return {"analysis": analysis, "route": route(s["question"], analysis)}

    def _refuse(self, s: AgentState) -> dict:
        r = s["route"]
        msg = {
            Intent.FUERA_DE_DOMINIO: MSG_OUT_OF_DOMAIN,
            Intent.ACCION_CRITICA: MSG_CRITICAL,
            Intent.AMBIGUA: MSG_AMBIGUOUS,
        }[r.intent]
        return {
            "output": AnswerOutput(
                answer=msg,
                abstained=True,
                needs_human_review=r.intent in (Intent.AMBIGUA, Intent.ACCION_CRITICA),
            )
        }

    def _call(self, s: AgentState, tool: str, fn: Callable[[], Any], params: dict) -> Any:
        """Autoriza (lista blanca + presupuesto), ejecuta con tiempo límite y registra."""
        call: dict[str, Any] = {"tool": tool, "params": params}
        t = time.perf_counter()
        try:
            s["budget"].authorize(tool)
            result = run_with_timeout(fn, self.settings.tool_timeout_s, tool)
            call["ok"] = True
            return result
        except ToolError as exc:
            call["ok"] = False
            call["error"] = str(exc)
            s["errors"].append(f"{tool}: {exc}")
            log.warning("Herramienta %s falló: %s", tool, exc)
            return None
        finally:
            call["latency_s"] = round(time.perf_counter() - t, 4)
            s["tool_calls"].append(call)

    def _sql(self, s: AgentState) -> dict:
        q = s["route"].sql
        assert q is not None
        if self.game_tool is None:
            s["errors"].append(f"{QUERY_GAME_DATA}: no configurada")
            return {"sql": None}
        tool = self.game_tool
        res = self._call(s, QUERY_GAME_DATA, lambda: tool.run(q), q.model_dump(mode="json"))
        if res is not None:
            s["tool_calls"][-1]["rows"] = res.total
        return {"sql": res}

    def _search(self, s: AgentState) -> dict:
        if self.search_tool is None:
            s["errors"].append(f"{SEARCH_DOCUMENTS}: índice no disponible")
            return {"retrieval": None}
        analysis = s["analysis"]
        admin_version = s.get("version") if s["role"] == "admin" else None
        params = SearchInput(
            query=s["question"],
            top_k=self.settings.top_k_final,
            entities=[e.canonical for e in analysis.entities][:20],
            version=admin_version,
        )
        tool = self.search_tool
        res = self._call(
            s,
            SEARCH_DOCUMENTS,
            lambda: tool.run(
                params, s["role"], analysis, include_deprecated=admin_version is not None
            ),
            params.model_dump(),
        )
        if res is not None:
            s["tool_calls"][-1]["hits"] = len(res.top)
        return {"retrieval": res}

    def _permissions(self, s: AgentState) -> dict:
        """Segunda validación de permisos sobre TODA la evidencia, justo antes del generador."""
        evidence: list[Chunk] = []
        sql = s.get("sql")
        if sql is not None and not sql.empty:
            evidence += to_evidence(sql)
        retrieval = s.get("retrieval")
        denied: list[str] = []
        max_score = 1.0 if evidence else 0.0
        if retrieval is not None:
            admin_version = s.get("version") if s["role"] == "admin" else None
            chk = validate_evidence(
                retrieval.chunks, s["role"], admin_version, admin_version is not None
            )
            denied = [c.chunk_id for c in chk.denied + retrieval.denied]
            allowed = {id(c) for c in chk.allowed}
            kept = [c for c in retrieval.top if id(c.chunk) in allowed]
            evidence += [c.chunk for c in kept]
            max_score = max([max_score, *(c.score for c in kept)])
        return {"evidence": evidence, "denied": denied, "max_score": max_score}

    def _gate(self, s: AgentState) -> dict:
        has_sql = s.get("sql") is not None and not s["sql"].empty
        passed = has_sql or (bool(s["evidence"]) and s["max_score"] >= self.settings.evidence_tau)
        update: dict[str, Any] = {"gate_passed": passed}
        if not passed:
            searched_ok = s.get("retrieval") is not None
            update["output"] = AnswerOutput(
                answer=MSG_NO_EVIDENCE if searched_ok else MSG_UNAVAILABLE,
                abstained=True,
                tool_used=SEARCH_DOCUMENTS if searched_ok else None,
                needs_human_review=not searched_ok,
            )
        return update

    def _tool_used(self, s: AgentState) -> str | None:
        used = [c["tool"] for c in s["tool_calls"] if c.get("ok")]
        if s.get("sql") is not None and s["sql"].empty and SEARCH_DOCUMENTS in used:
            used = [t for t in used if t != QUERY_GAME_DATA]
        return "+".join(dict.fromkeys(used)) or None

    def _sql_answer(self, s: AgentState) -> dict:
        res = s["sql"]
        assert res is not None
        evidence = s["evidence"]
        text = compose_answer(res)
        return {
            "output": AnswerOutput(
                answer=text,
                sources=_sources(cited_ids_in_text(text) or [evidence[0].chunk_id], evidence),
                tool_used=QUERY_GAME_DATA,
            )
        }

    def _generate(self, s: AgentState) -> dict:
        evidence = s["evidence"]
        prompt = build_user_prompt(s["question"], evidence)
        usage = s.get("usage") or LLMUsage()
        draft: GroundedDraft | None = None
        for attempt in (1, 2):  # un reintento si la salida no cumple el esquema
            try:
                res = self.llm.generate(SYSTEM_PROMPT, prompt, GroundedDraft)
                usage = usage + res.usage
                draft = res.output
                break
            except LLMError as exc:
                s["errors"].append(f"llm (intento {attempt}): {exc}")
                if "esquema" not in str(exc):
                    break
        tool_used = self._tool_used(s)
        if draft is None:
            return {
                "usage": usage,
                "output": AnswerOutput(
                    answer=_degraded_answer(evidence),
                    sources=_sources([c.chunk_id for c in evidence[:5]], evidence),
                    abstained=True,
                    tool_used=tool_used,
                    needs_human_review=True,
                ),
            }
        ids = [*draft.cited_chunk_ids, *cited_ids_in_text(draft.answer)]
        sources = _sources(ids, evidence)
        return {
            "usage": usage,
            "llm_model": self.llm.model,
            "output": AnswerOutput(
                answer=draft.answer,
                sources=[] if draft.insufficient_context else sources,
                abstained=draft.insufficient_context or not sources,
                tool_used=tool_used,
            ),
        }

    def _validate(self, s: AgentState) -> dict:
        chk = validate_output(s["output"], s.get("evidence", []), s["role"])
        s["issues"].extend(chk.issues)
        return {"output": chk.output}

    # ============================================================ aristas
    @staticmethod
    def _after_input(s: AgentState) -> str:
        return "analyze" if s["input"]["ok"] else END

    @staticmethod
    def _after_analyze(s: AgentState) -> str:
        r = s["route"]
        if r.sql is not None:
            return "sql"
        return "search" if r.search else "refuse"

    @staticmethod
    def _after_sql(s: AgentState) -> str:
        sql, r = s.get("sql"), s["route"]
        if r.search or sql is None or sql.empty:
            return "search"  # mixta, o respaldo documental si la tabla no tuvo datos
        return "permissions"

    @staticmethod
    def _after_gate(s: AgentState) -> str:
        if not s["gate_passed"]:
            return "validate"
        sql, r = s.get("sql"), s["route"]
        if sql is not None and not sql.empty and r.intent is Intent.DATO_EXACTO:
            return "sql_answer"
        return "generate"

    def _build(self):  # noqa: ANN202 - CompiledStateGraph
        g = StateGraph(AgentState)
        nodes = {
            "guard_input": self._guard_input,
            "analyze": self._analyze,
            "refuse": self._refuse,
            "sql": self._sql,
            "search": self._search,
            "permissions": self._permissions,
            "gate": self._gate,
            "sql_answer": self._sql_answer,
            "generate": self._generate,
            "validate": self._validate,
        }
        for name, fn in nodes.items():
            g.add_node(name, _timed(name, fn))
        g.add_edge(START, "guard_input")
        g.add_conditional_edges("guard_input", self._after_input, ["analyze", END])
        g.add_conditional_edges("analyze", self._after_analyze, ["sql", "search", "refuse"])
        g.add_conditional_edges("sql", self._after_sql, ["search", "permissions"])
        g.add_edge("search", "permissions")
        g.add_edge("permissions", "gate")
        g.add_conditional_edges("gate", self._after_gate, ["validate", "sql_answer", "generate"])
        g.add_edge("sql_answer", "validate")
        g.add_edge("generate", "validate")
        g.add_edge("refuse", END)
        g.add_edge("validate", END)
        return g.compile()

    # ============================================================== API
    def ask(self, question: str, role: Role = "jugador", version: str | None = None) -> AgentResult:
        """`role` viene SIEMPRE de la sesión del llamador, nunca del texto de la pregunta."""
        rid = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        state: AgentState = {
            "request_id": rid,
            "question_raw": question,
            "role": role,
            "version": version,
            "budget": ToolBudget(role, self.settings.max_tool_calls),
            "tool_calls": [],
            "issues": [],
            "errors": [],
            "timings": {},
            "evidence": [],
        }
        final: AgentState = self.graph.invoke(state)
        timings = dict(final["timings"])
        timings["total"] = time.perf_counter() - t0
        usage = final.get("usage") or LLMUsage()
        cost = usage.cost_usd(
            self.settings.llm_price_input_per_mtok, self.settings.llm_price_output_per_mtok
        )
        result = AgentResult(
            request_id=rid,
            question=final.get("question", ""),
            role=role,
            output=final["output"],
            trace=build_trace(final, self.settings.evidence_tau),
            usage=usage,
            cost_usd=cost,
            timings=timings,
            evidence=final.get("evidence", []),
        )
        if self.audit is not None:
            self.audit.write(audit_record(result))
        return result


# ===================================================================== traza
def build_trace(s: AgentState, tau: float) -> dict[str, Any]:
    analysis = s.get("analysis")
    r = s.get("route")
    retrieval = s.get("retrieval")
    sql = s.get("sql")
    return {
        "input": s.get("input"),
        "entities": [e.model_dump() for e in analysis.entities] if analysis else [],
        "expanded_query": analysis.expanded_query if analysis else None,
        "intent": r.intent.value if r else None,
        "route_reasons": r.reasons if r else [],
        "filters": {
            "role": s["role"],
            "status": "active" if not (s["role"] == "admin" and s.get("version")) else "any",
            "version": s.get("version") if s["role"] == "admin" else None,
            "kingdoms": analysis.kingdoms if analysis else [],
        },
        "tool_calls": s.get("tool_calls", []),
        "sql": (
            {
                "query": sql.query.model_dump(mode="json"),
                "total": sql.total,
                "truncated": sql.truncated,
                "version": sql.version,
            }
            if sql
            else None
        ),
        "candidates": [
            {
                "chunk_id": c.chunk.chunk_id,
                "source": c.chunk.source,
                "page": c.chunk.page,
                "version": c.chunk.version,
                "dense": c.dense,
                "lexical": c.lexical,
                "fused": round(c.fused, 4),
                "rerank": None if c.rerank is None else round(c.rerank, 4),
            }
            for c in (retrieval.candidates[:10] if retrieval else [])
        ],
        "evidence": [
            {"chunk_id": c.chunk_id, "source": c.source, "page": c.page, "url": c.url}
            for c in s.get("evidence", [])
        ],
        "denied": s.get("denied", []),
        "max_score": round(s.get("max_score", 0.0), 4),
        "tau": tau,
        "gate_passed": s.get("gate_passed"),
        "issues": s.get("issues", []),
        "errors": s.get("errors", []),
        "llm_model": s.get("llm_model"),
    }


def audit_record(r: AgentResult) -> dict[str, Any]:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_id": r.request_id,
        "role": r.role,
        "question": r.question,
        **r.trace,
        "context": [{"chunk_id": c.chunk_id, "text": c.text[:500]} for c in r.evidence],
        "output": r.output.model_dump(),
        "usage": {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens},
        "cost_usd": round(r.cost_usd, 6),
        "timings": {k: round(v, 4) for k, v in r.timings.items()},
    }
