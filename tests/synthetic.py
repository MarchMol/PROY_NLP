"""Corpus SINTÉTICO mínimo con forma de wiki, compartido por los tests."""

from __future__ import annotations

from marioqa.ingest.mediawiki import RawPage

KINGDOM_WIKI = """{{Course infobox
|boss=[[Test Boss]]<br>[[Other Boss]]
|moons=12
|regional_coins=50
|previous=[[Alpha Kingdom]]
|next=[[Gamma Kingdom]]
}}
The '''Beta Kingdom''' is a synthetic test kingdom with a giant waterfall.

==Crazy Cap items==
{|class="wikitable"
!Item
!Description
!Price
!Note
|-
|Test Helmet
|''A synthetic helmet.''
|{{icon|SMO-CoinBeta}} 5
|N/A
|-
|Beta Kingdom Power Moon
|''A synthetic moon.''
|[[File:SMO_coin.png|20px]] 100
|N/A
|}
"""

MOON_LIST_EXPANDED = """This is a list of synthetic Power Moons.
{|class="wikitable"
!#
!Image
!Name
!Location
!Availability
|-<span id="Behind the Synthetic Falls"></span>
|1
|[[File:x.png]]
|''Behind the Synthetic Falls''
|Jump behind the synthetic waterfall.
|Available from the start.
|-
|2
|[[File:y.png]]
|''Synthetic Multi Moon'' ❸
|Defeat the Test Boss.
|Available after collecting "Behind the Synthetic Falls".
|}
{|class="mw-collapsible navbox"
!Kingdoms
|-
|Noise
|}
"""

OUTFITS_WIKI = """==Outfits==
{|class="wikitable"
!Outfit
!Hat/Clothing
!Description
!Unlock method
|-
|rowspan=2|Test Set
|Test Helmet
|''Synthetic.''
|Sold by Crazy Cap in the Beta Kingdom for {{icon|SMO-CoinBeta}} 5.
|-
|Test Armor
|''Synthetic.''
|Given by Cappy.
|}
"""


def make_page(
    title: str, pageid: int, wikitext: str, expanded: str | None = None, revid: int = 100
) -> RawPage:
    return RawPage(
        requested_titles=[title],
        title=title,
        pageid=pageid,
        revid=revid,
        timestamp="2026-11-01T00:00:00Z",
        url=f"https://w/{title.replace(' ', '_')}",
        wikitext=wikitext,
        expanded_wikitext=expanded or wikitext,
    )


def synthetic_pages(variant: str = "") -> list[RawPage]:
    """Corpus sintético; `variant` altera el texto para simular otra versión."""
    return [
        make_page("Beta Kingdom", 1, KINGDOM_WIKI + variant),
        make_page(
            "List of Power Moons in the Beta Kingdom",
            2,
            "{{SMO Power Moons|x}}",
            MOON_LIST_EXPANDED,
        ),
        make_page("List of outfits in Super Mario Odyssey", 3, OUTFITS_WIKI),
    ]


SEED = """[kingdom]
Beta Kingdom
[power_moon]
List of Power Moons in the Beta Kingdom
[outfit]
List of outfits in Super Mario Odyssey
"""

INTERNAL_MD = """# Notas sintéticas
> Documento interno SINTÉTICO. CANARIO-TEST-0001.
## Calibración
El umbral tau se calibra con el banco de pruebas del equipo.
"""
