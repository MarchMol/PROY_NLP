"""Tabla de alias es↔en derivada de la wiki (+ glosario manual de traducción).

Fuentes, en orden:
1. Títulos de artículo y redirecciones del snapshot.
2. Nombres en español de las plantillas `{{foreign names}}` (Spa/SpaE/SpaA/Spa2).
3. Nombres exactos de Power Moons y atuendos de la base SQLite.
4. `data/aliases_manual.json`: formas coloquiales y glosario de palabras comunes.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import mwparserfromhell
from mwparserfromhell.nodes import Heading, Template
from pydantic import BaseModel

from marioqa.gamedata.build_db import MOON_LIST_RE, kingdom_name
from marioqa.guardrails.injection import normalize
from marioqa.ingest.mediawiki import RawPage

SPANISH_KEYS = ("Spa", "SpaE", "SpaA", "Spa2")
FOREIGN_HEADING = "Names in other languages"
OUTFITS_PAGE = "List of outfits in Super Mario Odyssey"
MAX_ALIAS_WORDS = 6


class Entity(BaseModel):
    canonical: str
    type: str
    page: str | None = None  # artículo de la wiki donde vive la información
    kingdom: str | None = None


class AliasTable(BaseModel):
    entities: list[Entity] = []
    aliases: dict[str, list[int]] = {}  # alias normalizado → índices en `entities`
    glossary: dict[str, list[str]] = {}

    # ----------------------------------------------------------- construcción
    def _entity_index(self, ent: Entity) -> int:
        for i, e in enumerate(self.entities):
            if e.canonical == ent.canonical and e.type == ent.type:
                if e.page is None and ent.page:
                    e.page = ent.page
                if e.kingdom is None and ent.kingdom:
                    e.kingdom = ent.kingdom
                return i
        self.entities.append(ent)
        return len(self.entities) - 1

    def add(self, ent: Entity, *aliases: str) -> None:
        idx = self._entity_index(ent)
        for alias in (ent.canonical, *aliases):
            key = normalize_alias(alias)
            if not key or len(key) < 3 or len(key.split()) > MAX_ALIAS_WORDS:
                continue
            bucket = self.aliases.setdefault(key, [])
            if idx not in bucket:
                bucket.append(idx)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> AliasTable:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def normalize_alias(text: str) -> str:
    """Igual que la normalización de consultas, sin puntuación (para comparar por tokens)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", normalize(text))).strip()


# ----------------------------------------------------------------- fuentes
def _spanish_names(t: Template) -> list[str]:
    out = []
    for key in SPANISH_KEYS:
        if t.has(key):
            val = t.get(key).value.strip_code().strip()
            if val and val != "-" and len(val.split()) <= MAX_ALIAS_WORDS:
                out.append(val)
    return out


def foreign_names(wikitext: str) -> list[tuple[list[str], list[str]]]:
    """(ruta de encabezados, nombres en español) por cada plantilla `{{foreign names}}`."""
    stack: list[tuple[int, str]] = []
    out = []
    for node in mwparserfromhell.parse(wikitext).nodes:
        if isinstance(node, Heading):
            h = node.title.strip_code().strip()
            while stack and stack[-1][0] >= node.level:
                stack.pop()
            stack.append((node.level, h))
        elif isinstance(node, Template) and str(node.name).strip().lower() == "foreign names":
            names = _spanish_names(node)
            if names:
                out.append(([h for _, h in stack], names))
    return out


def _named_entity(path: list[str], page_ent: Entity) -> Entity:
    """A quién nombra la plantilla según dónde aparece."""
    if FOREIGN_HEADING in path:
        i = path.index(FOREIGN_HEADING)
        after, before = path[i + 1 :], [h for h in path[:i] if h != "Naming"]
        target = after[-1] if after else (before[-1] if before else None)
    else:
        target = path[-1] if path else None
    if target is None or target == page_ent.page or target == page_ent.canonical:
        return page_ent
    sub_type = "location" if page_ent.type == "kingdom" else page_ent.type
    return Entity(canonical=target, type=sub_type, page=page_ent.page, kingdom=page_ent.kingdom)


def build_alias_table(
    pages: list[RawPage],
    entity_types: dict[str, str],
    db_path: Path | None = None,
    manual_path: Path | None = None,
) -> AliasTable:
    table = AliasTable()
    url_to_title = {p.url: p.title for p in pages}

    for page in pages:
        etype = entity_types.get(page.title, "other")
        canonical = kingdom_name(page.title) if etype == "kingdom" else page.title
        ent = Entity(
            canonical=canonical,
            type=etype,
            page=page.title,
            kingdom=canonical if etype == "kingdom" else None,
        )
        table.add(ent, page.title, *page.requested_titles)
        for path, names in foreign_names(page.wikitext):
            table.add(_named_entity(path, ent), *names)

    if db_path is not None and db_path.exists():
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for name, kingdom, url in con.execute(
                "SELECT name, kingdom, source_url FROM power_moon"
            ):
                page = url_to_title.get(url)
                table.add(Entity(canonical=name, type="power_moon", page=page, kingdom=kingdom))
            for name, outfit_set, kingdom in con.execute(
                "SELECT name, outfit_set, shop_kingdom FROM outfit"
            ):
                table.add(Entity(canonical=name, type="outfit", page=OUTFITS_PAGE, kingdom=kingdom))
                if outfit_set and outfit_set != name:
                    table.add(Entity(canonical=outfit_set, type="outfit", page=OUTFITS_PAGE))
        finally:
            con.close()

    if manual_path is not None and manual_path.exists():
        manual = json.loads(manual_path.read_text(encoding="utf-8"))
        known = {(e.canonical, e.type): e for e in table.entities}
        for item in manual.get("aliases", []):
            base = known.get((item["canonical"], item["type"]))
            ent = base or Entity(canonical=item["canonical"], type=item["type"])
            table.add(ent, *item["aliases"])
        table.glossary = {normalize_alias(k): v for k, v in manual.get("glossary", {}).items()}
    return table


def moon_list_titles(pages: list[RawPage]) -> dict[str, str]:
    """Reino → título de su lista de Power Moons."""
    out = {}
    for p in pages:
        m = MOON_LIST_RE.match(p.title)
        if m:
            out[m.group(1)] = p.title
    return out
