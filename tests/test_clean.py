"""Tests de la limpieza de wikitexto. El wikitexto es SINTÉTICO (imita el formato de la wiki)."""

from __future__ import annotations

from marioqa.ingest.clean import INTRO_SECTION, clean_wikitext, normalize_text

WIKI = """{{about|otra cosa}}
{{Course infobox
|image=[[File:Foo.jpg|280px]]
|boss=[[Madame Broode]]
|moons=42
|next=[[Sand Kingdom]]
}}
The '''Test Kingdom''' is a [[kingdom]] in ''[[Super Mario Odyssey|SMO]]''.<ref>{{cite|x}}</ref> \
It looks like {{wp|Iguazu Falls|real falls}}. Press {{button|switch|y}} to throw.<!-- oculto -->
[[File:Pic.png|thumb|caption]]

==Crazy Cap items==
{|class="wikitable"
!Item
!Price
|-
|[[File:SMO Hat.png|100px]]<br>Test Hat
|{{icon|SMO-CoinBowser}} 5
|-
|Test Suit
|[[File:SMO_Art_-_Regional_Coin_(Cascade).svg|20px]] 10
|}

==Outfits==
{|class="wikitable"
!Outfit
!Piece
|-
|rowspan=2|Samurai set
|Helmet
|-
|Armor
|}

==Checkpoint Flag locations==
===Odyssey===
Starting area.
====Names in other languages====
Japanese name here.

==References==
<references/>
[[Category:Super Mario Odyssey]]
"""


def _sections() -> dict[str, str]:
    return {s.title: s.text for s in clean_wikitext("Test Kingdom", WIKI).sections}


def test_intro_keeps_text_and_drops_markup() -> None:
    intro = _sections()[INTRO_SECTION]
    assert "The Test Kingdom is a kingdom in SMO." in intro
    assert "real falls" in intro
    assert "Press Y to throw" in intro
    for junk in ("<ref", "cite", "oculto", "File:", "thumb", "Category", "''", "about"):
        assert junk not in intro


def test_infobox_becomes_key_value_line() -> None:
    intro = _sections()[INTRO_SECTION]
    assert "boss: Madame Broode; moons: 42; next: Sand Kingdom." in intro
    assert "image" not in intro


def test_tables_are_linearized_with_headers_and_coin_icons() -> None:
    text = _sections()["Crazy Cap items"]
    assert "Item: Test Hat; Price: [regional coin (Bowser)] 5." in text
    assert "Item: Test Suit; Price: [regional coin (Cascade)] 10." in text


def test_rowspan_is_carried_to_next_row() -> None:
    lines = _sections()["Outfits"].splitlines()
    assert lines == ["Outfit: Samurai set; Piece: Helmet.", "Outfit: Samurai set; Piece: Armor."]


def test_nested_paths_and_dropped_sections() -> None:
    secs = _sections()
    assert secs["Checkpoint Flag locations > Odyssey"] == "Starting area."
    assert not any("Names in other languages" in t for t in secs)
    assert "References" not in secs
    assert "Checkpoint Flag locations" not in secs  # sin texto propio


def test_header_row_without_row_separator() -> None:
    wiki = (
        '{|class="w"\n!!data-sort-type="number"|#\n!class="x"|Name\n'
        "|-<span id=\"A\"></span>\n|4\n|''Behind''\n|}"
    )
    (sec,) = clean_wikitext("L", wiki).sections
    assert sec.text == "#: 4; Name: Behind."


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("a  \t b\n\n\n\nc &amp; d\n ; \n") == "a b\nc & d"
