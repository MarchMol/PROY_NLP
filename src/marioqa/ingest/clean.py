"""Limpieza de wikitexto → secciones de texto plano.

Usa mwparserfromhell para recorrer el árbol: conserva texto de enlaces y plantillas
"de contenido", elimina referencias, imágenes, navboxes y plantillas de metadatos,
y linealiza tablas como filas `Columna: valor; ...`.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import mwparserfromhell
from mwparserfromhell.nodes import (
    Comment,
    ExternalLink,
    Heading,
    HTMLEntity,
    Node,
    Tag,
    Template,
    Text,
    Wikilink,
)
from mwparserfromhell.wikicode import Wikicode

INTRO_SECTION = "Introduction"

DROP_SECTIONS = {
    "references",
    "notes",
    "gallery",
    "names in other languages",
    "profiles",
    "profiles and statistics",
    "see also",
    "external links",
    "artwork",
    "screenshots",
    "media",
    "multimedia",
    "staff",
    "music",
    "reception",
    "release",
    "references to other games",
    "references in other media",
    "pre-release and unused content",
    "internal names",
    "nintendo today!",
    "notable promotions",
    "appearances in other media",
}

DROP_TAGS = {"ref", "gallery", "references", "math", "score", "timeline", "imagemap"}
NEWLINE_TAGS = {"br", "p", "li", "div"}
IGNORED_LINK_NS = ("category:", "file:", "image:", "media:")
INFOBOX_SKIP_KEYS = {"image", "directory", "caption", "title", "music", "track", "image2"}


@dataclass
class Section:
    """Sección limpia de un documento; `path` = encabezados desde H2 hacia abajo."""

    path: list[str]
    text: str

    @property
    def title(self) -> str:
        return " > ".join(self.path)


@dataclass
class CleanDocument:
    title: str
    sections: list[Section] = field(default_factory=list)


# ---------------------------------------------------------------- plantillas
def _param(t: Template, name: str | int, default: str = "") -> str:
    key = str(name)
    return _render(t.get(key).value) if t.has(key) else default


def _positional(t: Template) -> list[str]:
    return [_render(p.value) for p in t.params if not p.showkey]


def _first(t: Template) -> str:
    vals = _positional(t)
    return vals[0] if vals else ""


def _last(t: Template) -> str:
    vals = _positional(t)
    return vals[-1] if vals else ""


def _label_or_target(t: Template) -> str:
    vals = _positional(t)
    return vals[1] if len(vals) > 1 and vals[1].strip() else (vals[0] if vals else "")


def _icon(t: Template) -> str:
    name = _first(t).strip()
    low = name.lower()
    if low in {"smo-coin", "smo coin"}:
        return "[coin]"
    if low.startswith("smo-coin"):
        return f"[regional coin ({name[len('smo-coin') :].strip()})]"
    return ""


def _infobox(t: Template) -> str:
    parts = []
    for p in t.params:
        key = str(p.name).strip()
        if not p.showkey or key.lower() in INFOBOX_SKIP_KEYS:
            continue
        lines = [_collapse_inline(ln) for ln in _render(p.value).split("\n")]
        value = ", ".join(ln for ln in lines if ln)
        if value:
            parts.append(f"{key.replace('_', ' ')}: {value}")
    return ("\n" + "; ".join(parts) + ".\n") if parts else ""


TEMPLATE_HANDLERS: dict[str, Callable[[Template], str]] = {
    "wp": _label_or_target,
    "wikipedia": _label_or_target,
    "hover": _first,
    "nw": _first,
    "conjectural": _first,
    "color": _last,
    "ruby": _first,
    "quote": _first,
    "button": lambda t: _last(t).upper(),
    "icon": _icon,
    "br": lambda t: "\n",
    "'": lambda t: "'",
    "!": lambda t: "/",
    "smo": lambda t: "Super Mario Odyssey",
    "iw": lambda t: (_positional(t) + ["", "", ""])[2] or (_positional(t) + ["", ""])[1],
}


def _render_template(t: Template) -> str:
    name = str(t.name).strip().lower().replace("_", " ")
    if name.endswith("infobox"):
        return _infobox(t)
    handler = TEMPLATE_HANDLERS.get(name)
    return handler(t) if handler else ""


# --------------------------------------------------------------------- tablas
def _cell_span(cell: Tag, attr: str) -> int:
    if cell.has(attr):
        raw = str(cell.get(attr).value).strip().strip('"')
        if raw.isdigit():
            return max(1, int(raw))
    return 1


def _table_rows(table: Tag) -> list[list[Tag]]:
    """Filas de celdas; las celdas sueltas antes del primer `|-` forman una fila implícita."""
    rows: list[list[Tag]] = []
    loose: list[Tag] = []
    for n in table.contents.nodes:
        if not isinstance(n, Tag):
            continue
        if n.tag in ("td", "th"):
            loose.append(n)
        elif n.tag == "tr":
            if loose:
                rows.append(loose)
                loose = []
            rows.append(
                [c for c in n.contents.nodes if isinstance(c, Tag) and c.tag in ("td", "th")]
            )
    if loose:
        rows.append(loose)
    return rows


def _cell_text(cell: Tag) -> str:
    lines = [_collapse_inline(ln) for ln in _render(cell.contents).split("\n")]
    return ", ".join(ln for ln in lines if ln)


def _end_sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."


def _render_table(table: Tag) -> str:
    headers: list[str] = []
    carry: dict[int, tuple[str, int]] = {}  # columna → (valor, filas restantes)
    lines: list[str] = []
    for cells in _table_rows(table):
        if not cells:
            continue
        if not headers and all(c.tag == "th" for c in cells):
            headers = [_cell_text(c) for c in cells]
            continue
        values: list[str] = []
        col = 0
        queue = list(cells)
        while queue or col in carry:
            if col in carry:
                val, left = carry[col]
                values.append(val)
                carry[col] = (val, left - 1)
                if left - 1 <= 0:
                    del carry[col]
                col += 1
                continue
            cell = queue.pop(0)
            val = _cell_text(cell)
            rowspan, colspan = _cell_span(cell, "rowspan"), _cell_span(cell, "colspan")
            for _ in range(colspan):
                values.append(val)
                if rowspan > 1:
                    carry[col] = (val, rowspan - 1)
                col += 1
        pairs: list[str] = []
        seen: set[tuple[str, str]] = set()
        for i, val in enumerate(values):
            if not val or val in {"–", "-", "N/A"}:
                continue
            head = headers[i] if i < len(headers) and headers[i] else ""
            if (head, val) in seen:
                continue
            seen.add((head, val))
            pairs.append(f"{head}: {val}" if head else val)
        if pairs:
            lines.append(_end_sentence("; ".join(p.rstrip(".") for p in pairs)))
    return "\n" + "\n".join(lines) + "\n"


# ------------------------------------------------------------------ recorrido
def _render_node(node: Node) -> str:
    if isinstance(node, Text):
        return str(node.value)
    if isinstance(node, Wikilink):
        target = str(node.title).strip()
        if target.lower().startswith(IGNORED_LINK_NS):
            return _icon_from_file(target)
        if target.startswith(":"):
            target = target[1:]
        return _render(node.text) if node.text else target.split("#")[0] or target
    if isinstance(node, Template):
        return _render_template(node)
    if isinstance(node, Tag):
        tag = str(node.tag).lower()
        if tag in DROP_TAGS:
            return ""
        if tag == "table":
            return _render_table(node)
        if tag in NEWLINE_TAGS:
            return "\n" + (_render(node.contents) if node.contents else "")
        return _render(node.contents) if node.contents else ""
    if isinstance(node, HTMLEntity):
        return html.unescape(str(node))
    if isinstance(node, ExternalLink):
        return _render(node.title) if node.title else ""
    if isinstance(node, (Comment, Heading)):
        return ""
    return str(node)


_REGIONAL_FILE = re.compile(r"regional coin \(([^)]+)\)", re.I)


def _icon_from_file(target: str) -> str:
    low = target.replace("_", " ").split("|")[0]
    if low.lower().startswith(("file:", "image:")):
        m = _REGIONAL_FILE.search(low)
        if m:
            return f"[regional coin ({m.group(1)})]"
        if "regional coin" in low.lower():
            return "[regional coin]"
        if "smo coin" in low.lower():
            return "[coin]"
    return ""


def _render(code: Wikicode | None) -> str:
    if code is None:
        return ""
    return "".join(_render_node(n) for n in code.nodes)


# -------------------------------------------------------------- normalización
_WS = re.compile(r"[ \t ]+")
_SPACE_BEFORE_PUNCT = re.compile(r" +([,.;:!?)])")


def _collapse_inline(text: str) -> str:
    return _SPACE_BEFORE_PUNCT.sub(r"\1", _WS.sub(" ", text.replace("\n", " "))).strip()


def normalize_text(text: str) -> str:
    """Normaliza espacios y saltos de línea; quita residuos de marcado."""
    text = html.unescape(text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"__[A-Z]+__", "", text)
    lines = [_SPACE_BEFORE_PUNCT.sub(r"\1", _WS.sub(" ", ln)).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln and not re.fullmatch(r"[\W_]+", ln)]
    return "\n".join(lines).strip()


# ------------------------------------------------------------------ secciones
def clean_wikitext(title: str, wikitext: str) -> CleanDocument:
    """Convierte el wikitexto de un artículo en secciones de texto limpio."""
    code = mwparserfromhell.parse(wikitext)
    doc = CleanDocument(title=title)
    stack: list[tuple[int, str]] = []
    buffer: list[Node] = []
    current_path: list[str] = [INTRO_SECTION]

    def flush() -> None:
        if any(h.lower() in DROP_SECTIONS for h in current_path):
            return
        text = normalize_text(_render(Wikicode(buffer)))
        if text:
            doc.sections.append(Section(path=list(current_path), text=text))

    for node in code.nodes:
        if isinstance(node, Heading):
            flush()
            buffer = []
            heading = _collapse_inline(normalize_text(_render(node.title)))
            while stack and stack[-1][0] >= node.level:
                stack.pop()
            stack.append((node.level, heading))
            current_path = [h for _, h in stack]
        else:
            buffer.append(node)
    flush()
    return doc
