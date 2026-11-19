"""LLM simulado: extractivo y determinista. Sirve para tests y para correr sin costo.

Lee los fragmentos delimitados del prompt, elige el de mayor solapamiento léxico con la
pregunta y responde con sus primeras oraciones citando su id. No inventa nada.
"""

from __future__ import annotations

import re
import time

from marioqa.llm.base import LLMError, LLMResult, LLMUsage, T
from marioqa.schemas import GroundedDraft

FRAGMENT_RE = re.compile(r'<fragmento id="([^"]+)"[^>]*>\n(.*?)\n</fragmento>', re.S)
QUESTION_RE = re.compile(r"<pregunta>\n(.*?)\n</pregunta>", re.S)
MIN_OVERLAP = 2


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 3}


class MockLLM:
    def __init__(self, model: str = "mock", *, fail: bool = False) -> None:
        self.model = model
        self.fail = fail
        self.calls = 0

    def generate(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        self.calls += 1
        if self.fail:
            raise LLMError("MockLLM configurado para fallar")
        if schema is not GroundedDraft:
            raise LLMError(f"MockLLM no sabe generar {schema.__name__}")
        start = time.perf_counter()
        q = QUESTION_RE.search(user)
        question = _words(q.group(1)) if q else set()
        best: tuple[int, str, str] | None = None
        for cid, text in FRAGMENT_RE.findall(user):
            body = text.split("\n", 1)[-1]
            score = len(question & _words(body))
            if best is None or score > best[0]:
                best = (score, cid, body)
        if best is None or best[0] < MIN_OVERLAP:
            draft = GroundedDraft(
                answer="No encontré información suficiente en las fuentes para responder.",
                cited_chunk_ids=[],
                insufficient_context=True,
            )
        else:
            sentences = re.split(r"(?<=[.!?])\s+", best[2].strip())
            draft = GroundedDraft(
                answer=f"Según la wiki: {' '.join(sentences[:2])} [{best[1]}]",
                cited_chunk_ids=[best[1]],
                insufficient_context=False,
            )
        usage = LLMUsage(len(system.split()) + len(user.split()), len(draft.answer.split()))
        return LLMResult(draft, usage, self.model, time.perf_counter() - start)  # type: ignore[arg-type]
