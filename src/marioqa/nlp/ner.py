"""NER por diccionario (gazetteer) + expansión de la consulta con nombres canónicos en inglés.

Coincidencia por tokens normalizados (sin acentos ni mayúsculas), la más larga primero y
sin solapamientos. Un modelo estadístico (spaCy) no aporta aquí: las entidades son nombres
propios de un juego que ningún modelo general conoce, y la tabla sale de la propia wiki.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from marioqa.nlp.aliases import AliasTable, Entity, normalize_alias

# Palabras comunes que no deben disparar una entidad por sí solas.
SINGLE_WORD_BLOCKLIST = frozenset(
    [
        "mario",
        "luna",
        "lunas",
        "moon",
        "reino",
        "kingdom",
        "tienda",
        "sombrero",
        "gorra",
        "traje",
        "casco",
        "faro",
        "torre",
        "nube",
        "nubes",
        "bosque",
        "lago",
        "nieve",
        "playa",
        "mar",
        "arena",
        "hat",
        "cap",
        "suit",
        "the",
        "una",
        "el",
        "la",
        "los",
        "las",
    ]
)


def _prefer_kingdoms(ents: list[Entity]) -> list[Entity]:
    """Si un alias nombra a un reino y a una zona de ese reino, se queda con el reino."""
    kingdoms = {e.canonical for e in ents if e.type == "kingdom"}
    return [e for e in ents if not (e.type == "location" and e.kingdom in kingdoms)]


@dataclass
class EntityMatch:
    text: str
    alias: str
    start: int  # índice de token
    end: int
    entities: list[Entity]


@dataclass
class QueryAnalysis:
    question: str
    matches: list[EntityMatch] = field(default_factory=list)
    expansion_terms: list[str] = field(default_factory=list)

    @property
    def entities(self) -> list[Entity]:
        seen: dict[tuple[str, str], Entity] = {}
        for m in self.matches:
            for e in m.entities:
                seen.setdefault((e.canonical, e.type), e)
        return list(seen.values())

    @property
    def kingdoms(self) -> list[str]:
        out: list[str] = []
        for e in self.entities:
            k = e.canonical if e.type == "kingdom" else e.kingdom
            if k and k not in out:
                out.append(k)
        return out

    @property
    def entity_types(self) -> list[str]:
        return list(dict.fromkeys(e.type for e in self.entities))

    @property
    def pages(self) -> list[str]:
        return list(dict.fromkeys(e.page for e in self.entities if e.page))

    @property
    def expanded_query(self) -> str:
        """Pregunta original + nombres canónicos + traducciones (para BM25 y embeddings)."""
        return " ".join([self.question, *self.expansion_terms]).strip()

    @property
    def rerank_query(self) -> str:
        """Pregunta original + solo los nombres canónicos de las entidades (para el reranker).

        Las traducciones del glosario ("unlock", "outfit", "suit"…) ayudan a BM25 y a los
        embeddings, pero al cross-encoder le diluyen la señal específica de la pregunta.
        """
        norm_q = normalize_alias(self.question)
        names = [e.canonical for e in self.entities if normalize_alias(e.canonical) not in norm_q]
        return " ".join([self.question, *dict.fromkeys(names)]).strip()


class EntityRecognizer:
    def __init__(self, table: AliasTable) -> None:
        self.table = table
        self._by_tokens = {tuple(k.split()): v for k, v in table.aliases.items()}
        self._max_len = max((len(k) for k in self._by_tokens), default=1)

    def find(self, text: str) -> list[EntityMatch]:
        tokens = normalize_alias(text).split()
        matches: list[EntityMatch] = []
        i = 0
        while i < len(tokens):
            for n in range(min(self._max_len, len(tokens) - i), 0, -1):
                key = tuple(tokens[i : i + n])
                idxs = self._by_tokens.get(key)
                if idxs and not (n == 1 and key[0] in SINGLE_WORD_BLOCKLIST):
                    ents = _prefer_kingdoms([self.table.entities[j] for j in idxs])
                    matches.append(EntityMatch(" ".join(key), " ".join(key), i, i + n, ents))
                    i += n
                    break
            else:
                i += 1
        return matches

    def analyze(self, question: str) -> QueryAnalysis:
        matches = self.find(question)
        terms: list[str] = []
        for m in matches:
            for e in m.entities:
                terms.append(e.canonical)
                if e.type == "kingdom" and e.page and e.page != e.canonical:
                    terms.append(e.page)
        covered = {t for m in matches for t in range(m.start, m.end)}
        for pos, tok in enumerate(normalize_alias(question).split()):
            if pos not in covered:
                terms.extend(self.table.glossary.get(tok, []))
        norm_q = normalize_alias(question)
        terms = [t for t in dict.fromkeys(terms) if normalize_alias(t) not in norm_q]
        return QueryAnalysis(question, matches, terms)
