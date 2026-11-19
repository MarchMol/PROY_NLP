"""Enmascarado de PII con expresiones regulares (correos, teléfonos, tokens, IPs, tarjetas)."""

from __future__ import annotations

import re

# El orden importa: lo más específico primero, para que un token no se lea como teléfono.
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    (
        "token",
        re.compile(
            r"\b(?:sk|pk|rk|ghp|gho|xox[abpr]|AKIA|AIza)[-_A-Za-z0-9]{12,}\b"
            r"|\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"
            r"|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"  # JWT
            r"|\b[A-Fa-f0-9]{32,}\b"
        ),
    ),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("phone", re.compile(r"(?<![\w-])\+?\d[\d\s().-]{6,}\d(?![\w-])")),
]


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Devuelve el texto con PII reemplazada por `[TIPO]` y la lista de tipos encontrados."""
    found: list[str] = []
    for kind, pattern in PII_PATTERNS:
        text, n = pattern.subn(f"[{kind.upper()}]", text)
        if n:
            found.append(kind)
    return text, found
