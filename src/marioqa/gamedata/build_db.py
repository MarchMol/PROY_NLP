"""Construye `game_data.sqlite` (reinos, Power Moons, atuendos y tienda) desde un snapshot."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import mwparserfromhell

from marioqa.ingest.clean import extract_tables
from marioqa.ingest.mediawiki import RawPage
from marioqa.ingest.snapshot import utc_now_iso

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE kingdom (
    name TEXT PRIMARY KEY,
    display_order INTEGER NOT NULL,
    moons_total INTEGER,
    regional_coins INTEGER,
    boss TEXT,
    previous TEXT,
    next TEXT,
    source_url TEXT NOT NULL
);
CREATE TABLE power_moon (
    id INTEGER PRIMARY KEY,
    kingdom TEXT NOT NULL REFERENCES kingdom(name),
    number INTEGER,
    name TEXT NOT NULL,
    is_multi_moon INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    availability TEXT,
    source_url TEXT NOT NULL
);
CREATE TABLE outfit (
    id INTEGER PRIMARY KEY,
    outfit_set TEXT,
    name TEXT NOT NULL,
    description TEXT,
    unlock_method TEXT,
    shop_kingdom TEXT,
    price INTEGER,
    currency TEXT,
    source_url TEXT NOT NULL
);
CREATE TABLE shop_item (
    id INTEGER PRIMARY KEY,
    kingdom TEXT NOT NULL REFERENCES kingdom(name),
    item TEXT NOT NULL,
    description TEXT,
    price INTEGER,
    currency TEXT,
    note TEXT,
    source_url TEXT NOT NULL
);
CREATE INDEX idx_moon_kingdom ON power_moon(kingdom);
CREATE INDEX idx_shop_kingdom ON shop_item(kingdom);
CREATE INDEX idx_outfit_kingdom ON outfit(shop_kingdom);
"""

MOON_LIST_RE = re.compile(r"^List of Power Moons in (?:the )?(.+)$")
PRICE_RE = re.compile(r"\[(coin|regional coin)(?: \(([^)]+)\))?\]\s*([\d,]+)")
SOLD_IN_RE = re.compile(r"Sold by Crazy Cap in (?:the )?(.+?) for ")
MULTI_MOON_MARK = "❸"
BR_RE = re.compile(r"<br\s*/?>", re.I)


def kingdom_name(title: str) -> str:
    """Nombre canónico del reino a partir del título del artículo."""
    return re.sub(r"\s*\(Super Mario Odyssey\)$", "", title)


@dataclass(frozen=True)
class Price:
    amount: int | None
    currency: str | None  # "coin" | "regional_coin"
    kingdom_hint: str | None


def parse_price(text: str) -> Price:
    m = PRICE_RE.search(text or "")
    if not m:
        return Price(None, None, None)
    currency = "coin" if m.group(1) == "coin" else "regional_coin"
    return Price(int(m.group(3).replace(",", "")), currency, m.group(2))


def resolve_kingdom(hint: str | None, kingdoms: Iterable[str]) -> str | None:
    """'Bowser' / 'Bowser's Kingdom' / 'Cap' → nombre canónico de la lista de reinos."""
    if not hint:
        return None
    hint = hint.strip().removeprefix("the ")
    for k in kingdoms:
        if hint == k or k.startswith(hint + " ") or k.startswith(hint + "'"):
            return k
    return None


def _int(value: str | None) -> int | None:
    m = re.search(r"\d+", value or "")
    return int(m.group()) if m else None


def _infobox(wikitext: str) -> dict[str, str]:
    """Parámetros del primer `Course infobox` como texto plano."""
    code = mwparserfromhell.parse(wikitext)
    for t in code.filter_templates(recursive=False):
        if str(t.name).strip().lower() == "course infobox":
            out = {}
            for p in t.params:
                # strip_code() pega valores separados por <br>; se separan con coma antes.
                raw = BR_RE.sub(", ", str(p.value))
                text = mwparserfromhell.parse(raw).strip_code().replace("\n", " ")
                out[str(p.name).strip()] = re.sub(r"\s+", " ", text).strip(" ,")
            return out
    return {}


def _clean_moon_name(name: str) -> tuple[str, bool]:
    is_multi = MULTI_MOON_MARK in name
    return name.replace(MULTI_MOON_MARK, "").strip(), is_multi


@dataclass
class GameData:
    kingdoms: list[tuple]
    moons: list[tuple]
    outfits: list[tuple]
    shop: list[tuple]


def extract_game_data(pages: list[RawPage], kingdom_titles: list[str]) -> GameData:
    """Extrae filas de las tablas de la wiki. `kingdom_titles` en orden del juego (seed)."""
    by_title = {p.title: p for p in pages}
    names = [kingdom_name(t) for t in kingdom_titles]
    data = GameData([], [], [], [])

    for order, title in enumerate(kingdom_titles, 1):
        page = by_title.get(title)
        if page is None:
            continue
        kname = kingdom_name(title)
        box = _infobox(page.wikitext)
        data.kingdoms.append(
            (
                kname,
                order,
                _int(box.get("moons")),
                _int(box.get("regional_coins")),
                box.get("boss") or None,
                box.get("previous") or None,
                box.get("next") or None,
                page.url,
            )
        )
        for table in extract_tables(page.wikitext):
            if not table.section.lower().startswith("crazy cap items"):
                continue
            for rec in table.records():
                item = rec.get("Item", "").strip()
                if not item:
                    continue
                price = parse_price(rec.get("Price", ""))
                note = rec.get("Note") or rec.get("Notes") or rec.get("Availability")
                data.shop.append(
                    (
                        kname,
                        item,
                        rec.get("Description") or None,
                        price.amount,
                        price.currency,
                        None if note in (None, "", "N/A") else note,
                        page.url,
                    )
                )

    for page in pages:
        m = MOON_LIST_RE.match(page.title)
        if not m or not page.expanded_wikitext:
            continue
        kname = resolve_kingdom(m.group(1), names) or m.group(1)
        for table in extract_tables(page.expanded_wikitext):
            if "Name" not in table.headers:
                continue
            for rec in table.records():
                name, is_multi = _clean_moon_name(rec.get("Name", ""))
                if not name:
                    continue
                data.moons.append(
                    (
                        kname,
                        _int(rec.get("#")),
                        name,
                        int(is_multi),
                        rec.get("Location") or None,
                        rec.get("Availability") or None,
                        page.url,
                    )
                )

    outfits_page = by_title.get("List of outfits in Super Mario Odyssey")
    if outfits_page is not None:
        for table in extract_tables(outfits_page.wikitext):
            if "Hat/Clothing" not in table.headers:
                continue
            for rec in table.records():
                name = rec.get("Hat/Clothing", "").strip()
                if not name:
                    continue
                unlock = rec.get("Unlock method") or ""
                price = parse_price(unlock)
                sold = SOLD_IN_RE.search(unlock)
                shop_k = resolve_kingdom(sold.group(1), names) if sold else None
                data.outfits.append(
                    (
                        rec.get("Outfit") or None,
                        name,
                        rec.get("Description") or None,
                        unlock or None,
                        shop_k,
                        price.amount,
                        price.currency,
                        outfits_page.url,
                    )
                )
    return data


def build_database(
    path: Path, pages: list[RawPage], kingdom_titles: list[str], version: str, cutoff: str
) -> GameData:
    """(Re)crea la base de forma atómica: escribe a un temporal y lo renombra."""
    data = extract_game_data(pages, kingdom_titles)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO kingdom VALUES (?,?,?,?,?,?,?,?)", data.kingdoms)
        con.executemany(
            "INSERT INTO power_moon (kingdom, number, name, is_multi_moon, location, availability,"
            " source_url) VALUES (?,?,?,?,?,?,?)",
            data.moons,
        )
        con.executemany(
            "INSERT INTO outfit (outfit_set, name, description, unlock_method, shop_kingdom, price,"
            " currency, source_url) VALUES (?,?,?,?,?,?,?,?)",
            data.outfits,
        )
        con.executemany(
            "INSERT INTO shop_item (kingdom, item, description, price, currency, note, source_url)"
            " VALUES (?,?,?,?,?,?,?)",
            data.shop,
        )
        con.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [
                ("version", version),
                ("cutoff", cutoff),
                ("built_at", utc_now_iso()),
                ("license", "CC BY-SA (Super Mario Wiki)"),
            ],
        )
        con.commit()
    finally:
        con.close()
    tmp.replace(path)
    return data
