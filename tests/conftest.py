"""Fixtures compartidas: settings aislados en un directorio temporal."""

from __future__ import annotations

from pathlib import Path

import pytest

from marioqa.config import Settings
from tests.synthetic import INTERNAL_MD, SEED


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    seed = tmp_path / "seed.txt"
    seed.write_text(SEED, encoding="utf-8")
    internal = tmp_path / "internal"
    internal.mkdir()
    (internal / "notas.md").write_text(INTERNAL_MD, encoding="utf-8")
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        raw_dir=tmp_path / "raw",
        processed_dir=tmp_path / "processed",
        index_dir=tmp_path / "index",
        chroma_dir=tmp_path / "chroma",
        sqlite_path=tmp_path / "game.sqlite",
        internal_docs_dir=internal,
        seed_titles_path=seed,
        chunk_tokens=60,
        chunk_overlap_tokens=10,
        min_section_tokens=5,
    )
