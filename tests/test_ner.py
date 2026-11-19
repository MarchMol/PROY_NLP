"""Tests de alias es↔en y NER. Wikitexto SINTÉTICO con la forma de `{{foreign names}}`."""

from __future__ import annotations

from pathlib import Path

import pytest

from marioqa.config import ROOT_DIR
from marioqa.nlp.aliases import AliasTable, Entity, build_alias_table, foreign_names
from marioqa.nlp.ner import EntityRecognizer
from tests.synthetic import make_page

SAND = """Intro.
==Checkpoint Flag locations==
===Tostarena Ruins Entrance===
Text.
====Names in other languages====
{{foreign names|Jap=x|SpaE=Entrada a las ruinas de Soltitlán|SpaEM=-}}
==Names in other languages==
===Sand Kingdom===
{{foreign names|Spa=Reino de las Arenas|SpaM=-}}
===Desert Area===
{{foreign names|SpaE=Reino de las Arenas}}
"""
BOSS = """Boss.
==Naming==
===Names in other languages===
{{foreign names|Spa=Katunda|SpaM=-}}
"""


@pytest.fixture
def table(tmp_path: Path) -> AliasTable:
    pages = [make_page("Sand Kingdom", 1, SAND), make_page("Knucklotec", 2, BOSS)]
    pages[0].requested_titles.append("Tostarena")
    return build_alias_table(
        pages,
        {"Sand Kingdom": "kingdom", "Knucklotec": "boss"},
        manual_path=ROOT_DIR / "data" / "aliases_manual.json",
    )


def test_foreign_names_paths() -> None:
    got = foreign_names(SAND)
    assert got[0] == (
        ["Checkpoint Flag locations", "Tostarena Ruins Entrance", "Names in other languages"],
        ["Entrada a las ruinas de Soltitlán"],
    )
    assert (["Names in other languages", "Sand Kingdom"], ["Reino de las Arenas"]) in got


def test_aliases_map_spanish_names_to_canonical(table: AliasTable) -> None:
    ner = EntityRecognizer(table)
    a = ner.analyze("Estoy atascado en el Reino de las Arenas, ¿cómo continúo?")
    assert [(e.canonical, e.type) for e in a.entities] == [("Sand Kingdom", "kingdom")]
    assert a.kingdoms == ["Sand Kingdom"] and a.pages == ["Sand Kingdom"]
    assert "Sand Kingdom" in a.expanded_query

    b = ner.analyze("¿Cómo venzo a KATUNDA?")
    assert [e.canonical for e in b.entities] == ["Knucklotec"]


def test_sub_locations_redirects_and_accents(table: AliasTable) -> None:
    ner = EntityRecognizer(table)
    entrance = ner.analyze("entrada a las ruinas de soltitlan").entities
    assert entrance[0].canonical == "Tostarena Ruins Entrance"
    assert entrance[0].type == "location" and entrance[0].kingdom == "Sand Kingdom"
    assert ner.analyze("¿Qué hay en Tostarena?").kingdoms == ["Sand Kingdom"]


def test_longest_match_wins_and_glossary_expands(table: AliasTable) -> None:
    ner = EntityRecognizer(table)
    a = ner.analyze("¿Dónde compro el atuendo de samurái?")
    assert [e.canonical for e in a.entities] == ["Samurai Armor"]
    assert {"buy", "Crazy Cap"} <= set(a.expansion_terms)
    c = ner.analyze("la luna detrás de la cascada")
    assert c.entities == [] and {"behind", "waterfall"} <= set(c.expansion_terms)


def test_single_common_words_do_not_trigger_entities() -> None:
    table = AliasTable()
    table.add(Entity(canonical="Sombrero", type="outfit"), "sombrero")
    table.add(Entity(canonical="Sombrero and Poncho", type="outfit"), "sombrero y poncho")
    ner = EntityRecognizer(table)
    assert ner.analyze("¿Dónde compro un sombrero?").entities == []
    assert [e.canonical for e in ner.analyze("el sombrero y poncho").entities] == [
        "Sombrero and Poncho"
    ]


def test_ambiguous_question_has_no_entities(table: AliasTable) -> None:
    assert (
        EntityRecognizer(table).analyze("¿Qué necesito para desbloquear determinada zona?").entities
        == []
    )


def test_alias_table_roundtrip(table: AliasTable, tmp_path: Path) -> None:
    path = tmp_path / "aliases.json"
    table.save(path)
    loaded = AliasTable.load(path)
    assert loaded.aliases == table.aliases and len(loaded.entities) == len(table.entities)
