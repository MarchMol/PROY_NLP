"""Detector de inyección de prompt por patrones (español e inglés) sobre texto normalizado."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def normalize(text: str) -> str:
    """Minúsculas, sin acentos y con espacios simples (para que los patrones sean robustos)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


_INSTR = r"(?:instrucciones|reglas|indicaciones|ordenes|directrices|restricciones|prompt)"
_IGNORE = r"(?:ignora|ignorar|ignore|olvida|olvidar|omite|descarta|salta(?:te)?|desobedece)"

INJECTION_RULES: dict[str, re.Pattern[str]] = {
    "ignorar_instrucciones": re.compile(
        rf"\b{_IGNORE}\b.{{0,40}}\b{_INSTR}\b"
        r"|\b(?:ignore|disregard|forget)\b.{0,30}\b(?:instructions|rules|prompt|above)\b"
    ),
    "revelar_prompt": re.compile(
        r"\b(?:muestra|muestrame|revela|dime|imprime|repite|ensena|escribe|copia|dame)\b.{0,40}"
        r"\b(?:prompt|instrucciones (?:del sistema|internas|iniciales|ocultas)|system prompt|"
        r"mensaje del sistema|configuracion interna)\b"
        r"|\b(?:show|reveal|print|repeat|leak)\b.{0,30}"
        r"\b(?:system prompt|your prompt|instructions)\b"
        r"|\bsystem prompt\b"
    ),
    "cambio_de_rol": re.compile(
        r"\b(?:ahora eres|a partir de ahora (?:eres|seras|actua)|actua como|finge (?:ser|que eres)|"
        r"haz de cuenta que eres|comportate como|you are now|act as|pretend (?:to be|you are)|"
        r"roleplay as)\b"
    ),
    "escalada_de_privilegios": re.compile(
        r"\b(?:soy (?:el |un |la )?(?:admin|administrador|desarrollador|developer)|"
        r"(?:modo|rol|permisos?) (?:de )?(?:admin|administrador|desarrollador|dios|root)|"
        r"(?:developer|admin|god|dan) mode|i am (?:the |an )?admin|sudo)\b"
    ),
    "jailbreak": re.compile(r"\b(?:jailbreak|do anything now|sin restricciones|sin filtros)\b"),
    "delimitadores": re.compile(r"</?\s*(?:fragmento|contexto|pregunta|system|instrucciones)\s*>"),
}


@dataclass
class InjectionResult:
    is_attack: bool
    rules: list[str] = field(default_factory=list)


def detect_injection(text: str) -> InjectionResult:
    norm = normalize(text)
    rules = [name for name, pat in INJECTION_RULES.items() if pat.search(norm)]
    return InjectionResult(bool(rules), rules)
