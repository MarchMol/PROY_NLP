"""Tests del chunker con un contador de tokens por espacios (determinista y sin red).

Los textos son SINTÉTICOS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marioqa.ingest.build_chunks import chunk_pages, page_wikitext
from marioqa.ingest.chunking import (
    ChunkParams,
    DocMeta,
    chunk_document,
    merge_short_sections,
    sha256,
    split_sentences,
    whitespace_counter,
    window_sentences,
)
from marioqa.ingest.clean import Section
from marioqa.ingest.mediawiki import RawPage
from marioqa.ingest.snapshot import read_seed_entities

count = whitespace_counter
META = DocMeta(
    doc_key="p1",
    title="Doc",
    url="https://w/Doc",
    version="20261119",
    revid=7,
    cutoff="2026-11-19T00:00:00Z",
    entity_type="kingdom",
)


def sentences(n: int, words: int = 10, tag: str = "S") -> list[str]:
    """n oraciones de `words` palabras cada una, identificables por índice."""
    return [" ".join([f"{tag}{i}"] + ["w"] * (words - 2) + ["end."]) for i in range(n)]


def text_of(n: int, words: int = 10, tag: str = "S") -> str:
    return " ".join(sentences(n, words, tag))


# ----------------------------------------------------------------- oraciones
def test_split_sentences_and_keep_table_rows_whole() -> None:
    row = "Name: Behind the Waterfall; Location: Go left. Then jump; Availability: Always."
    assert split_sentences(f"Uno dos. Tres cuatro! Cinco?\n{row}") == [
        "Uno dos.",
        "Tres cuatro!",
        "Cinco?",
        row,
    ]


# -------------------------------------------------------------------- ventanas
@pytest.mark.parametrize("budget", [200, 300, 500])
def test_windows_respect_budget_and_overlap(budget: int) -> None:
    overlap = round(budget * 0.15)
    sents = sentences(200)
    windows = window_sentences(sents, budget, overlap, count)
    assert len(windows) > 1
    for w in windows:
        assert sum(count(s) for s in w) <= budget
    for prev, nxt in zip(windows, windows[1:], strict=False):
        shared = [s for s in nxt if s in prev]
        assert shared == prev[len(prev) - len(shared) :]  # solape = cola de la anterior
        assert 0 < sum(count(s) for s in shared) <= overlap
    # Cobertura total y en orden
    seen = []
    for w in windows:
        seen.extend(s for s in w if s not in seen)
    assert seen == sents


def test_windows_cut_only_at_sentence_boundaries() -> None:
    sents = sentences(50)
    for w in window_sentences(sents, 100, 15, count):
        assert all(s in sents for s in w)


def test_long_sentence_is_hard_split() -> None:
    long = " ".join(["x"] * 250) + "."
    windows = window_sentences([long], 100, 15, count)
    assert all(sum(count(s) for s in w) <= 100 for w in windows)
    assert sum(count(s) for w in windows for s in w) >= 250


def test_zero_overlap_and_invalid_params() -> None:
    windows = window_sentences(sentences(30), 50, 0, count)
    flat = [s for w in windows for s in w]
    assert len(flat) == len(set(flat)) == 30
    with pytest.raises(ValueError):
        ChunkParams(chunk_tokens=100, overlap_tokens=100)


# -------------------------------------------------------------- secciones
def test_short_sections_merge_with_next() -> None:
    secs = [
        Section(["A"], text_of(2)),  # 20 tokens < 80
        Section(["B"], text_of(10)),  # 100
        Section(["C"], text_of(9)),  # 90
    ]
    groups = merge_short_sections(secs, 80, count)
    assert [g.paths for g in groups] == [["A", "B"], ["C"]]
    assert groups[0].parts[1].startswith("B:\n")


def test_trailing_short_section_merges_into_previous() -> None:
    secs = [Section(["A"], text_of(10)), Section(["B"], text_of(1))]
    groups = merge_short_sections(secs, 80, count)
    assert [g.paths for g in groups] == [["A", "B"]]


# ----------------------------------------------------------------- chunks
def _chunks(sections: list[Section], params: ChunkParams | None = None):
    return chunk_document(META, sections, params or ChunkParams(100, 15, 30), count)


def test_chunk_text_prefix_metadata_and_hash() -> None:
    chunks = _chunks([Section(["Power Moons"], text_of(30))])
    assert len(chunks) > 1
    for c in chunks:
        assert c.text.startswith("Doc > Power Moons\n")
        assert c.n_tokens <= 100
        assert c.hash == sha256(c.text)
        assert (c.source, c.url, c.page, c.version, c.revid) == (
            "Doc",
            "https://w/Doc",
            "Power Moons",
            "20261119",
            7,
        )
        assert (c.status, c.rol_min, c.entity_type) == ("active", "jugador", "kingdom")
    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_chunk_ids_are_stable_and_unique() -> None:
    secs = [
        Section(["A"], text_of(30)),
        Section(["A"], text_of(30, tag="T")),
        Section(["B"], text_of(5)),
    ]
    first, second = _chunks(secs), _chunks(secs)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len({c.chunk_id for c in first}) == len(first)
    assert all(c.chunk_id.startswith("p1-") for c in first)


def test_chunk_id_does_not_depend_on_other_sections() -> None:
    base = _chunks([Section(["A"], text_of(10)), Section(["B"], text_of(10, tag="B"))])
    edited = _chunks([Section(["A"], text_of(12)), Section(["B"], text_of(10, tag="B"))])
    ids = lambda cs: {c.chunk_id for c in cs if c.page == "B"}  # noqa: E731
    assert ids(base) == ids(edited)


def test_window_size_is_parametrizable() -> None:
    secs = [Section(["A"], text_of(200))]
    sizes = {n: len(_chunks(secs, ChunkParams(n, round(n * 0.15), 80))) for n in (200, 300, 500)}
    assert sizes[200] > sizes[300] > sizes[500]


# ------------------------------------------------------------ build_chunks
def _page(title: str, wikitext: str, expanded: str | None = None, pageid: int = 1) -> RawPage:
    return RawPage(
        requested_titles=[title],
        title=title,
        pageid=pageid,
        revid=10,
        timestamp="t",
        url=f"https://w/{title}",
        wikitext=wikitext,
        expanded_wikitext=expanded,
    )


def test_power_moon_lists_use_expanded_wikitext() -> None:
    moons = _page("List of Power Moons in the X Kingdom", "{{SMO Power Moons|A}}", "EXPANDED")
    other = _page("X Kingdom", "RAW", "EXPANDED")
    assert page_wikitext(moons) == "EXPANDED"
    assert page_wikitext(other) == "RAW"


def test_chunk_pages_assigns_entity_type_from_seed(tmp_path: Path) -> None:
    seed = tmp_path / "seed.txt"
    seed.write_text("# x\n[kingdom]\nX Kingdom\n[boss]\nTopper\n", encoding="utf-8")
    entities = read_seed_entities(seed)
    assert entities == {"X Kingdom": "kingdom", "Topper": "boss"}
    pages = [_page("X Kingdom", text_of(20), pageid=2), _page("Unknown", text_of(20), pageid=3)]
    chunks = chunk_pages(
        pages,
        version="v1",
        cutoff="c",
        entities=entities,
        params=ChunkParams(100, 15, 30),
        count=count,
    )
    types = {c.source: c.entity_type for c in chunks}
    assert types == {"X Kingdom": "kingdom", "Unknown": "other"}
