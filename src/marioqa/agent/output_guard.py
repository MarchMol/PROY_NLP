"""Guardrails de salida: contrato, citas, soporte de la respuesta y fugas.

1. **Contrato:** la salida se revalida contra `AnswerOutput`.
2. **Citas:** solo sobreviven ids de evidencia realmente recuperada; las marcas `[id]` inválidas
   se borran también del texto.
3. **Soporte:** las "anclas verificables" de la respuesta (cifras y nombres entre paréntesis o
   comillas, que suelen ser los nombres originales en inglés) deben aparecer en los fragmentos
   citados. Es un chequeo barato y determinista; la fidelidad fina se mide con el juez LLM.
4. **Fugas:** la respuesta no puede contener trozos del prompt del sistema, credenciales ni, para
   un jugador, marcadores de documentos internos (`CANARIO-…`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from marioqa.guardrails.pii import PII_PATTERNS
from marioqa.index.access import Role
from marioqa.ingest.chunking import Chunk
from marioqa.nlp.aliases import normalize_alias
from marioqa.rag.prompts import SYSTEM_PROMPT
from marioqa.schemas import AnswerOutput

CITE_RE = re.compile(r"\s?\[([A-Za-z0-9][\w:.\-]*)\]")
CANARY_RE = re.compile(r"CANARIO-[A-Z]+-[A-Z0-9]+")
NUMBER_RE = re.compile(r"(?<![\w.])\d[\d,.]*(?![\w])")
PAREN_RE = re.compile(r"\(([^()]{3,60})\)|[«\"“]([^»\"”]{3,60})[»\"”]")
SECRET_RE = re.compile(r"LLM_API_KEY|NO-ES-REAL-\d+|clave de prueba", re.I)
SHINGLE = 7

UNSUPPORTED_ABSTAIN_RATIO = 0.5
SAFE_LEAK_ANSWER = (
    "No puedo mostrar esa respuesta porque incluía información interna del sistema. "
    "Reformula tu pregunta sobre Super Mario Odyssey."
)
SAFE_UNSUPPORTED_ANSWER = (
    "No pude verificar que la respuesta esté respaldada por las fuentes recuperadas, así que "
    "prefiero no responder. Revisa los fragmentos citados o reformula la pregunta."
)


def _shingles(text: str, n: int = SHINGLE) -> set[tuple[str, ...]]:
    toks = normalize_alias(text).split()
    return {tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)}


# Los ejemplos del prompt (entre comillas o corchetes, p. ej. "Detrás de la cascada (Behind the
# Waterfall)") son respuestas legítimas: no cuentan como fuga si el modelo los repite.
_PROMPT_SHINGLES = _shingles(re.sub(r"\"[^\"]*\"|\[[^\]]*\]", " ", SYSTEM_PROMPT))


@dataclass
class OutputCheck:
    output: AnswerOutput
    issues: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    leak: bool = False

    @property
    def support_ratio(self) -> float | None:
        if not self.anchors:
            return None
        return 1 - len(self.unsupported) / len(self.anchors)


def strip_invalid_citations(answer: str, valid_ids: set[str]) -> tuple[str, list[str]]:
    """Quita del texto las marcas `[id]` que no corresponden a evidencia recuperada."""
    removed: list[str] = []

    def repl(m: re.Match[str]) -> str:
        if m.group(1) in valid_ids:
            return m.group(0)
        removed.append(m.group(1))
        return ""

    return CITE_RE.sub(repl, answer), removed


def cited_ids_in_text(answer: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in CITE_RE.finditer(answer)))


def detect_leak(answer: str, role: Role) -> list[str]:
    issues = []
    if _shingles(answer) & _PROMPT_SHINGLES:
        issues.append("fuga_prompt_sistema")
    if role != "admin" and CANARY_RE.search(answer):
        issues.append("fuga_documento_interno")
    if SECRET_RE.search(answer) or any(
        kind == "token" and pat.search(answer) for kind, pat in PII_PATTERNS
    ):
        issues.append("fuga_credenciales")
    return issues


def _anchors(answer: str) -> list[str]:
    body = CITE_RE.sub(" ", answer)
    out = [m.group(0).rstrip(".,") for m in NUMBER_RE.finditer(body)]
    for m in PAREN_RE.finditer(body):
        text = (m.group(1) or m.group(2)).strip()
        if _looks_like_name(text):
            out.append(text)
    return [a for a in dict.fromkeys(out) if a]


def _looks_like_name(text: str) -> bool:
    """Nombre propio (p. ej. 'Behind the Waterfall'), no una aclaración en prosa."""
    words = [w for w in re.findall(r"[^\W\d_][\w'’-]*", text) if len(w) > 3]
    return bool(words) and sum(w[0].isupper() for w in words) / len(words) > 0.5


def _supported(anchor: str, haystack: str) -> bool:
    a = normalize_alias(anchor)
    if not a:
        return True
    if a.replace(" ", "").isdigit():
        return re.search(rf"(?<!\d){re.escape(a.replace(' ', ''))}(?!\d)", haystack) is not None
    return a in haystack


def check_support(answer: str, cited: Sequence[Chunk]) -> tuple[list[str], list[str]]:
    haystack = normalize_alias(" ".join(c.text + " " + c.source for c in cited))
    haystack_digits = re.sub(r"(?<=\d)[,.](?=\d{3})", "", haystack)
    anchors = _anchors(answer)
    unsupported = [
        a
        for a in anchors
        if not _supported(a, haystack) and not _supported(a.replace(",", ""), haystack_digits)
    ]
    return anchors, unsupported


def validate_output(output: AnswerOutput, evidence: Sequence[Chunk], role: Role) -> OutputCheck:
    by_id = {c.chunk_id: c for c in evidence}
    answer, removed = strip_invalid_citations(output.answer, set(by_id))
    out = output.model_copy(update={"answer": answer})
    check = OutputCheck(out)
    if removed:
        check.issues.append(f"citas_inexistentes:{','.join(removed)}")

    leaks = detect_leak(answer, role)
    if leaks:
        check.issues += leaks
        check.leak = True
        check.output = AnswerOutput(
            answer=SAFE_LEAK_ANSWER,
            abstained=True,
            tool_used=out.tool_used,
            needs_human_review=True,
        )
        return check

    if not out.abstained:
        cited = [by_id[s.chunk_id] for s in out.sources if s.chunk_id in by_id]
        check.anchors, check.unsupported = check_support(answer, cited)
        ratio = check.support_ratio
        if ratio is not None and ratio < UNSUPPORTED_ABSTAIN_RATIO:
            check.issues.append("respuesta_sin_soporte")
            check.output = AnswerOutput(
                answer=SAFE_UNSUPPORTED_ANSWER,
                sources=out.sources,
                abstained=True,
                tool_used=out.tool_used,
                needs_human_review=True,
            )
        elif check.unsupported:
            check.issues.append("soporte_parcial")
            check.output = out.model_copy(update={"needs_human_review": True})

    # Revalidación del contrato (por si algún paso armó un objeto inconsistente).
    check.output = AnswerOutput.model_validate(check.output.model_dump())
    return check
