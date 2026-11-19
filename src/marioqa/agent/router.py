"""Router de intención por reglas: documental, dato exacto, fuera de dominio o acción crítica.

Decide QUÉ herramienta usar y con qué parámetros cerrados. Usa el análisis del NER (entidades y
reinos) y patrones sobre el texto normalizado. Es determinista y explicable; el LLM no participa
en la elección de herramienta, así que una inyección no puede forzar una herramienta distinta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from marioqa.guardrails.injection import normalize
from marioqa.nlp.aliases import normalize_alias
from marioqa.nlp.ner import QueryAnalysis
from marioqa.tools.game_data import GameDataQuery, GameEntity, Operation


class Intent(StrEnum):
    DOCUMENTAL = "documental"
    DATO_EXACTO = "dato_exacto"
    MIXTA = "mixta"  # dato exacto + pregunta documental en la misma consulta
    FUERA_DE_DOMINIO = "fuera_de_dominio"
    ACCION_CRITICA = "accion_critica"
    AMBIGUA = "ambigua"


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


CRITICAL_ACTION = _re(
    r"\b(?:reindex\w*|re index\w*|reindexa\w*"
    r"|reconstru\w+ (?:el |los |la )?(?:indice|indices|corpus|base)"
    r"|regenera\w* (?:el |los |la )?(?:indice|indices|corpus|base)"
    r"|(?:borra|elimina|resetea|actualiza)\w* (?:el |los |la |todo el )?"
    r"(?:indice|indices|corpus|base de datos)"
    r"|(?:ejecuta|corre|lanza|run)\w* (?:la herramienta |el comando )?(?:reindex|ingest\w*))\b"
)

OTHER_GAMES = _re(
    r"\b(?:zelda|hyrule|pokemon|pikachu|mario kart|smash|metroid|kirby|animal crossing|splatoon"
    r"|minecraft|fortnite|sonic|mario 64|sunshine|mario galaxy|galaxy 2|bros wonder|3d world"
    r"|mario party|paper mario|luigi s mansion|mario maker|xenoblade|fire emblem|elden ring"
    r"|god of war|call of duty|halo|tetris|pac man|street fighter|final fantasy|genshin)\b"
)

DOMAIN_WORDS = _re(
    r"\b(?:odyssey|odisea|mario|cappy|luna|lunas|energiluna\w*|superluna\w*|moon|moons|moneda\w*"
    r"|coin|coins|reino\w*|kingdom|atuendo\w*|traje\w*|ropa|disfraz\w*|sombrero\w*|gorra\w*"
    r"|casco\w*|tienda\w*|crazy cap|jefe\w*|boss|captur\w*|nivel\w*|zona\w*|mundo\w*|nave|bowser"
    r"|peach|tiara|broodal\w*|desbloque\w*|juego|partida|piedra lunar|moon rock|checkpoint|bandera"
    r"|tuberia\w*|tubo\w*|8 bit|pintura|mini ?juego\w*|carrera\w*|globo\w*|koopa|toad\w*|yoshi"
    r"|goomba\w*|pokio|odyssey)\b"
    # Vocabulario del propio sistema (documentos internos). Se busca igual que lo demás: el
    # filtro por rol del índice decide qué puede ver la sesión.
    r"|\b(?:umbral\w*|tau|banco de pruebas|reindexad\w*|runbook|notas? internas?"
    r"|documentos? internos?|evaluacion del equipo|calibra\w*)\b"
)

VAGUE = _re(
    r"\b(?:determinad[oa]s?|ciert[oa]s?|tal (?:zona|lugar|luna|nivel|cosa)"
    r"|(?:es|est)[ea] (?:zona|lugar|parte|nivel|luna|jefe|reino|cosa|sitio)"
    r"|alguna? (?:zona|lugar|parte|nivel|sitio)|aqui|ahi|alli|eso|esto)\b"
)

COUNT = _re(r"\b(?:cuant[ao]s|numero de|cantidad de|total de|how many)\b")
LIST = _re(
    r"\b(?:lista|listado|enumera\w*|cuales son (?:las|los|todas|todos)|todas las|todos los"
    r"|que (?:vende|venden|se vende|se puede comprar|puedo comprar|hay en la tienda)"
    r"|que (?:articulos|objetos|atuendos|trajes|lunas))\b"
)
PRICE = _re(r"\b(?:cuanto (?:cuesta|cuestan|vale|valen)|precio\w*|costo|cuesta)\b")
REQUIREMENT = _re(
    r"\b(?:necesit\w*|requier\w*|hace falta|hacen falta|para (?:salir|desbloquear|abrir|avanzar"
    r"|pasar|ir|viajar|llegar|despegar|reparar)"
    # "¿cuántas lunas vale una Multi Moon?" pregunta una equivalencia, no un conteo de tabla.
    r"|vale|valen|equivale\w*|cuenta como|cuentan como)\b"
)
DOC_QUESTION = _re(r"\b(?:como|donde|por que|porque|quien|cuando|que hago|que debo)\b")

MOON_WORDS = _re(r"\b(?:luna|lunas|energiluna\w*|superluna\w*|moon|moons)\b")
OUTFIT_WORDS = _re(
    r"\b(?:atuendo\w*|traje\w*|ropa|disfraz\w*|vestimenta\w*|outfit\w*|armadura\w*)\b"
)
SHOP_WORDS = _re(
    r"\b(?:tienda\w*|vende\w*|comprar|articulo\w*|objeto\w*|crazy cap|souvenir\w*|recuerdo\w*"
    r"|pegatina\w*|sticker\w*)\b"
)

SPECIFIC_TYPES = frozenset(
    {"kingdom", "location", "power_moon", "outfit", "boss", "character", "capture", "shop"}
)


@dataclass
class RouteDecision:
    intent: Intent
    sql: GameDataQuery | None = None
    search: bool = False
    ambiguous: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def tool_plan(self) -> list[str]:
        plan = ["query_game_data"] if self.sql else []
        return plan + (["search_documents"] if self.search else [])


def _sql_params(norm: str, analysis: QueryAnalysis, reasons: list[str]) -> GameDataQuery | None:
    """Arma parámetros cerrados para `query_game_data`, o None si no aplica."""
    kingdom = analysis.kingdoms[0] if analysis.kingdoms else None
    outfits = [e.canonical for e in analysis.entities if e.type == "outfit"]

    if PRICE.search(norm) and outfits:
        reasons.append("precio de atuendo → outfit/get")
        return GameDataQuery(
            entity_type=GameEntity.OUTFIT, operation=Operation.GET, name=outfits[0]
        )
    if REQUIREMENT.search(norm):
        return None  # "¿cuántas lunas necesito para…?" es progresión, no un conteo de tabla
    if COUNT.search(norm):
        if MOON_WORDS.search(norm):
            reasons.append("conteo de lunas → power_moon/count")
            return GameDataQuery(
                entity_type=GameEntity.POWER_MOON, operation=Operation.COUNT, kingdom=kingdom
            )
        if OUTFIT_WORDS.search(norm):
            reasons.append("conteo de atuendos → outfit/count")
            return GameDataQuery(
                entity_type=GameEntity.OUTFIT, operation=Operation.COUNT, kingdom=kingdom
            )
        if SHOP_WORDS.search(norm) and kingdom:
            reasons.append("conteo de artículos de tienda → shop/count")
            return GameDataQuery(
                entity_type=GameEntity.SHOP, operation=Operation.COUNT, kingdom=kingdom
            )
    if LIST.search(norm):
        if SHOP_WORDS.search(norm) and kingdom:
            reasons.append("listado de tienda → shop/list")
            return GameDataQuery(
                entity_type=GameEntity.SHOP, operation=Operation.LIST, kingdom=kingdom
            )
        if OUTFIT_WORDS.search(norm):
            reasons.append("listado de atuendos → outfit/list")
            return GameDataQuery(
                entity_type=GameEntity.OUTFIT, operation=Operation.LIST, kingdom=kingdom
            )
        if MOON_WORDS.search(norm) and kingdom:
            reasons.append("listado de lunas → power_moon/list")
            return GameDataQuery(
                entity_type=GameEntity.POWER_MOON, operation=Operation.LIST, kingdom=kingdom
            )
    return None


def _has_doc_part(question: str) -> bool:
    """¿Hay además una cláusula documental (p. ej. "… y cómo derroto al jefe")?"""
    parts = [normalize_alias(p) for p in re.split(r"\by\b|[,;?¿]", normalize(question))]
    is_data = [bool(COUNT.search(p) or PRICE.search(p) or LIST.search(p)) for p in parts]
    is_doc = [bool(DOC_QUESTION.search(p)) and not d for p, d in zip(parts, is_data, strict=True)]
    return any(is_data) and any(is_doc)


def route(question: str, analysis: QueryAnalysis) -> RouteDecision:
    norm = normalize_alias(question)
    reasons: list[str] = []
    specific = [e for e in analysis.entities if e.type in SPECIFIC_TYPES]

    if CRITICAL_ACTION.search(norm):
        return RouteDecision(Intent.ACCION_CRITICA, reasons=["pide una acción administrativa"])
    if OTHER_GAMES.search(norm) and not specific:
        return RouteDecision(Intent.FUERA_DE_DOMINIO, reasons=["menciona otro videojuego"])
    if not analysis.entities and VAGUE.search(norm):
        # "¿Cómo paso esa parte?": sin referente ni vocabulario, pero es una duda de juego.
        return RouteDecision(
            Intent.AMBIGUA, ambiguous=True, reasons=["referencia vaga sin entidad concreta"]
        )
    if not analysis.entities and not DOMAIN_WORDS.search(norm):
        return RouteDecision(Intent.FUERA_DE_DOMINIO, reasons=["sin entidades ni vocabulario"])

    sql = _sql_params(norm, analysis, reasons)
    if sql is not None:
        if _has_doc_part(question):
            reasons.append("además pregunta algo documental")
            return RouteDecision(Intent.MIXTA, sql=sql, search=True, reasons=reasons)
        return RouteDecision(Intent.DATO_EXACTO, sql=sql, reasons=reasons)

    if not specific and (VAGUE.search(norm) or len(norm.split()) <= 3):
        return RouteDecision(
            Intent.AMBIGUA, ambiguous=True, reasons=["referencia vaga sin entidad concreta"]
        )
    return RouteDecision(Intent.DOCUMENTAL, search=True, reasons=reasons or ["pregunta abierta"])
