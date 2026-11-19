"""Chunking por sección + ventanas con solapamiento, cortando en límite de oración."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel

from marioqa.ingest.clean import Section

TokenCounter = Callable[[str], int]
Status = Literal["active", "deprecated"]
Role = Literal["jugador", "admin"]


class Chunk(BaseModel):
    chunk_id: str
    text: str
    source: str
    url: str
    page: str
    sections: list[str]
    version: str
    revid: int
    cutoff: str
    hash: str
    entity_type: str
    status: Status = "active"
    rol_min: Role = "jugador"
    n_tokens: int
    position: int


@dataclass(frozen=True)
class ChunkParams:
    chunk_tokens: int = 300
    overlap_tokens: int = 50
    min_section_tokens: int = 80

    def __post_init__(self) -> None:
        if not 0 <= self.overlap_tokens < self.chunk_tokens:
            raise ValueError("overlap_tokens debe estar en [0, chunk_tokens)")


@dataclass(frozen=True)
class DocMeta:
    """Metadatos de documento que se copian a cada chunk."""

    doc_key: str  # p. ej. "p229460" (wiki) o "int-notas-eval" (interno)
    title: str
    url: str
    version: str
    revid: int
    cutoff: str
    entity_type: str
    rol_min: Role = "jugador"


# ------------------------------------------------------------------ tokens
def whitespace_counter(text: str) -> int:
    """Contador aproximado (tests y fallback)."""
    return len(text.split())


def hf_token_counter(model_name: str) -> TokenCounter:
    """Cuenta tokens con el tokenizer del modelo de embeddings (sin tokens especiales)."""
    from transformers import AutoTokenizer
    from transformers.utils import logging as hf_logging

    # Contar secciones completas (> 512 tokens) es intencional; solo se embeben ventanas.
    hf_logging.set_verbosity_error()
    tok = AutoTokenizer.from_pretrained(model_name)

    @lru_cache(maxsize=200_000)
    def count(text: str) -> int:
        return len(tok(text, add_special_tokens=False)["input_ids"])

    return count


# --------------------------------------------------------------- oraciones
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[A-Z0-9¿¡])")
# Filas de tabla linealizadas por clean.py: "Col: valor; Col: valor."
_TABLE_ROW = re.compile(r"^[^:;\n]{1,40}: .+; [^:;\n]{1,40}: ")


def split_sentences(text: str) -> list[str]:
    """Separa en oraciones; las filas de tabla se mantienen enteras como una sola unidad."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if _TABLE_ROW.match(line):
            out.append(line)
        else:
            out.extend(s.strip() for s in _SENT_END.split(line) if s.strip())
    return out


def _hard_split(sentence: str, budget: int, count: TokenCounter) -> list[str]:
    """Parte una oración más larga que la ventana por palabras."""
    pieces: list[str] = []
    current: list[str] = []
    for word in sentence.split():
        if current and count(" ".join([*current, word])) > budget:
            pieces.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def window_sentences(
    sentences: list[str], budget: int, overlap: int, count: TokenCounter
) -> list[list[str]]:
    """Empaqueta oraciones en ventanas de ≤ `budget` tokens con ~`overlap` tokens de solape."""
    units: list[str] = []
    for s in sentences:
        units.extend(_hard_split(s, budget, count) if count(s) > budget else [s])

    windows: list[list[str]] = []
    i = 0
    while i < len(units):
        window: list[str] = []
        used = 0
        j = i
        while j < len(units):
            cost = count(units[j])
            if window and used + cost > budget:
                break
            window.append(units[j])
            used += cost
            j += 1
        windows.append(window)
        if j >= len(units):
            break
        # Retrocede oraciones completas mientras quepan en el solape, sin repetir la ventana entera.
        back, carried = j, 0
        while back - 1 > i and carried + count(units[back - 1]) <= overlap:
            back -= 1
            carried += count(units[back])
        i = back
    return windows


# ---------------------------------------------------------------- secciones
@dataclass
class _Group:
    paths: list[str]
    parts: list[str]
    tokens: int


def merge_short_sections(
    sections: Iterable[Section], min_tokens: int, count: TokenCounter
) -> list[_Group]:
    """Une secciones < `min_tokens` con la siguiente (la última corta se une a la anterior)."""
    groups: list[_Group] = []
    pending: _Group | None = None
    for sec in sections:
        n = count(sec.text)
        if pending is None:
            pending = _Group([sec.title], [sec.text], n)
        else:
            pending.paths.append(sec.title)
            pending.parts.append(f"{sec.title}:\n{sec.text}")
            pending.tokens += n
        if pending.tokens >= min_tokens:
            groups.append(pending)
            pending = None
    if pending is not None:
        if groups:
            last = groups[-1]
            for path, part in zip(pending.paths, pending.parts, strict=True):
                last.paths.append(path)
                last.parts.append(part if part.startswith(f"{path}:") else f"{path}:\n{part}")
            last.tokens += pending.tokens
        else:
            groups.append(pending)
    return groups


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_chunk_id(doc_key: str, section_path: str, window: int) -> str:
    """ID determinista: mismo documento + sección + ventana → mismo id."""
    sec = hashlib.sha1(section_path.encode("utf-8")).hexdigest()[:8]
    return f"{doc_key}-{sec}-{window:02d}"


def chunk_document(
    meta: DocMeta, sections: list[Section], params: ChunkParams, count: TokenCounter
) -> list[Chunk]:
    """Secciones limpias → chunks con prefijo `Título > Sección` y metadatos completos."""
    chunks: list[Chunk] = []
    seen_paths: dict[str, int] = {}
    for group in merge_short_sections(sections, params.min_section_tokens, count):
        page = group.paths[0]
        dup = seen_paths.get(page, 0)
        seen_paths[page] = dup + 1
        id_path = page if dup == 0 else f"{page}#{dup}"

        prefix = f"{meta.title} > {page}"
        budget = max(params.chunk_tokens - count(prefix), 1)
        sentences = split_sentences("\n".join(group.parts))
        for w, window in enumerate(
            window_sentences(sentences, budget, params.overlap_tokens, count)
        ):
            text = prefix + "\n" + "\n".join(window)
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(meta.doc_key, id_path, w),
                    text=text,
                    source=meta.title,
                    url=meta.url,
                    page=page,
                    sections=group.paths,
                    version=meta.version,
                    revid=meta.revid,
                    cutoff=meta.cutoff,
                    hash=sha256(text),
                    entity_type=meta.entity_type,
                    rol_min=meta.rol_min,
                    n_tokens=count(text),
                    position=len(chunks),
                )
            )
    return chunks
