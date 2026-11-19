"""Tests de la extracción a SQLite con wikitexto SINTÉTICO."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from marioqa.gamedata.build_db import build_database, parse_price, resolve_kingdom
from tests.synthetic import synthetic_pages

KINGDOMS = ["Alpha Kingdom", "Beta Kingdom", "Bowser's Kingdom", "Mushroom Kingdom"]


def test_parse_price_and_resolve_kingdom() -> None:
    p = parse_price("[regional coin (Bowser)] 10")
    assert (p.amount, p.currency, p.kingdom_hint) == (10, "regional_coin", "Bowser")
    assert parse_price("[coin] 1,000").amount == 1000
    assert parse_price("free").amount is None
    assert resolve_kingdom("Bowser", KINGDOMS) == "Bowser's Kingdom"
    assert resolve_kingdom("the Beta Kingdom", KINGDOMS) == "Beta Kingdom"
    assert resolve_kingdom("Nowhere", KINGDOMS) is None


def test_build_database_extracts_all_tables(tmp_path: Path) -> None:
    db = tmp_path / "g.sqlite"
    build_database(db, synthetic_pages(), ["Beta Kingdom"], "v1", "2026-11-19T00:00:00Z")
    con = sqlite3.connect(db)

    assert con.execute(
        "SELECT name, moons_total, regional_coins, boss, next FROM kingdom"
    ).fetchall() == [("Beta Kingdom", 12, 50, "Test Boss, Other Boss", "Gamma Kingdom")]
    moons = con.execute(
        "SELECT number, name, is_multi_moon, location FROM power_moon ORDER BY number"
    ).fetchall()
    assert moons == [
        (1, "Behind the Synthetic Falls", 0, "Jump behind the synthetic waterfall."),
        (2, "Synthetic Multi Moon", 1, "Defeat the Test Boss."),
    ]
    shop = con.execute("SELECT kingdom, item, price, currency, note FROM shop_item").fetchall()
    assert shop == [
        ("Beta Kingdom", "Test Helmet", 5, "regional_coin", None),
        ("Beta Kingdom", "Beta Kingdom Power Moon", 100, "coin", None),
    ]
    outfits = con.execute(
        "SELECT outfit_set, name, shop_kingdom, price FROM outfit ORDER BY id"
    ).fetchall()
    assert outfits == [
        ("Test Set", "Test Helmet", "Beta Kingdom", 5),
        ("Test Set", "Test Armor", None, None),
    ]
    assert dict(con.execute("SELECT key, value FROM meta").fetchall())["version"] == "v1"


def test_rebuild_is_atomic_and_replaces(tmp_path: Path) -> None:
    db = tmp_path / "g.sqlite"
    for _ in range(2):
        build_database(db, synthetic_pages(), ["Beta Kingdom"], "v1", "c")
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM power_moon").fetchone() == (2,)
    assert not db.with_suffix(".tmp").exists()
