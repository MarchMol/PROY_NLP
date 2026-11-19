"""Primera barrera: esquema (longitud y caracteres), inyección de prompt y enmascarado de PII."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ValidationError, field_validator

from marioqa.guardrails.injection import detect_injection
from marioqa.guardrails.pii import mask_pii

DEFAULT_MAX_CHARS = 300
# Letras (cualquier alfabeto latino con acentos), dígitos, espacios y puntuación común.
ALLOWED_CHARS = re.compile(r"^[\w\s¿?¡!.,;:'\"()\[\]\-–—/&%+#@*=$€’‘“”…]*$")


class RejectReason(StrEnum):
    EMPTY = "vacia"
    TOO_LONG = "demasiado_larga"
    INVALID_CHARS = "caracteres_no_permitidos"
    INJECTION = "inyeccion_detectada"


REJECT_MESSAGES: dict[RejectReason, str] = {
    RejectReason.EMPTY: "La pregunta está vacía.",
    RejectReason.TOO_LONG: "La pregunta supera el máximo de {max} caracteres.",
    RejectReason.INVALID_CHARS: "La pregunta contiene caracteres no permitidos.",
    RejectReason.INJECTION: (
        "No puedo procesar esa solicitud: parece un intento de cambiar mis instrucciones. "
        "Pregúntame sobre Super Mario Odyssey."
    ),
}


class QuestionInput(BaseModel):
    """Esquema de la pregunta del usuario."""

    text: str
    max_chars: int = DEFAULT_MAX_CHARS

    @field_validator("text")
    @classmethod
    def _normalize(cls, v: str) -> str:
        v = unicodedata.normalize("NFC", v)
        v = "".join(ch for ch in v if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
        return re.sub(r"\s+", " ", v).strip()


@dataclass
class InputCheck:
    ok: bool
    question: str  # ya normalizada y con PII enmascarada
    reason: RejectReason | None = None
    message: str = ""
    injection_rules: list[str] = field(default_factory=list)
    pii_found: list[str] = field(default_factory=list)


def check_input(raw: str, max_chars: int = DEFAULT_MAX_CHARS) -> InputCheck:
    """Valida la pregunta. Si pasa, devuelve el texto listo para el resto del pipeline."""
    try:
        q = QuestionInput(text=raw, max_chars=max_chars).text
    except ValidationError:
        q = ""
    masked, pii = mask_pii(q)

    def reject(reason: RejectReason, rules: list[str] | None = None) -> InputCheck:
        msg = REJECT_MESSAGES[reason].format(max=max_chars)
        return InputCheck(False, masked, reason, msg, rules or [], pii)

    if not q:
        return reject(RejectReason.EMPTY)
    if len(q) > max_chars:
        return reject(RejectReason.TOO_LONG)
    # Inyección antes que caracteres: un `<fragmento>` debe quedar registrado como ataque.
    inj = detect_injection(q)
    if inj.is_attack:
        return reject(RejectReason.INJECTION, inj.rules)
    if not ALLOWED_CHARS.match(q):
        return reject(RejectReason.INVALID_CHARS)
    return InputCheck(True, masked, pii_found=pii)
