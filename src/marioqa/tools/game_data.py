"""`query_game_data`: conteos y listados exactos sobre SQLite con parámetros cerrados.

El modelo nunca escribe SQL. Cada combinación (tipo, operación) tiene una plantilla fija; solo
los VALORES (reino, nombre) entran como parámetros enlazados. La conexión es de solo lectura
(`mode=ro` + `PRAGMA query_only`), con máximo de filas y tiempo límite por consulta.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marioqa.schemas import SourceRef
from marioqa.tools.base import ToolError


class GameEntity(StrEnum):
    POWER_MOON = "power_moon"
    OUTFIT = "outfit"
    SHOP = "shop"


class Operation(StrEnum):
    COUNT = "count"
    LIST = "list"
    GET = "get"


class GameDataQuery(BaseModel):
    """Parámetros cerrados de la herramienta (validados antes de tocar la base)."""

    model_config = ConfigDict(extra="forbid")

    entity_type: GameEntity
    operation: Operation
    kingdom: str | None = Field(default=None, max_length=60)
    name: str | None = Field(default=None, min_length=2, max_length=80)

    @model_validator(mode="after")
    def _check(self) -> GameDataQuery:
        if self.operation is Operation.GET and not self.name:
            raise ValueError("operation=get requiere name")
        if (
            self.entity_type in (GameEntity.POWER_MOON, GameEntity.SHOP)
            and self.operation is Operation.LIST
            and not self.kingdom
        ):
            raise ValueError(f"list de {self.entity_type} requiere kingdom")
        return self


# Columnas devueltas y orden por (tipo). WHERE se arma solo con estas piezas fijas.
_COLUMNS = {
    GameEntity.POWER_MOON: (
        "power_moon",
        "kingdom, number, name, is_multi_moon, location, availability, source_url",
        "kingdom",
        "name",
        "number",
    ),
    GameEntity.OUTFIT: (
        "outfit",
        "outfit_set, name, unlock_method, shop_kingdom, price, currency, source_url",
        "shop_kingdom",
        "name",
        "id",
    ),
    GameEntity.SHOP: (
        "shop_item",
        "kingdom, item, description, price, currency, note, source_url",
        "kingdom",
        "item",
        "id",
    ),
}

_SECTION = {
    GameEntity.POWER_MOON: "Power Moons",
    GameEntity.OUTFIT: "Outfits",
    GameEntity.SHOP: "Crazy Cap items",
}


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def title_from_url(url: str) -> str:
    return unquote(url.rsplit("/", 1)[-1]).replace("_", " ")


@dataclass
class GameDataResult:
    query: GameDataQuery
    rows: list[dict] = field(default_factory=list)
    total: int = 0  # filas que cumplen el filtro (puede superar las devueltas)
    truncated: bool = False
    kingdom_info: dict | None = None  # fila de `kingdom` (infobox) si se pidió un reino
    version: str = ""
    latency_s: float = 0.0

    @property
    def empty(self) -> bool:
        return self.total == 0

    def sources(self) -> list[SourceRef]:
        urls = list(dict.fromkeys(r["source_url"] for r in self.rows if r.get("source_url")))
        if self.kingdom_info and self.query.operation is Operation.COUNT:
            urls.append(self.kingdom_info["source_url"])
        section = _SECTION[self.query.entity_type]
        tag = f"sql:{self.query.entity_type}:{self.query.operation}"
        return [
            SourceRef(
                source=title_from_url(u),
                page="Infobox"
                if self.kingdom_info and u == self.kingdom_info["source_url"]
                else section,
                chunk_id=f"{tag}:{i}",
                url=u,
            )
            for i, u in enumerate(dict.fromkeys(urls))
        ]


class GameDataTool:
    name = "query_game_data"

    def __init__(self, path: Path, *, max_rows: int = 50, timeout_s: float = 2.0) -> None:
        self.path = path
        self.max_rows = max_rows
        self.timeout_s = timeout_s
        self._kingdoms: list[str] | None = None

    # --------------------------------------------------------------- conexión
    def _connect(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise ToolError("La base de datos del juego no está disponible (corre `ingest`)")
        try:
            con = sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
            )
        except sqlite3.Error as exc:
            raise ToolError(f"No se pudo abrir la base del juego: {exc}") from exc
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        deadline = time.perf_counter() + self.timeout_s
        con.set_progress_handler(lambda: int(time.perf_counter() > deadline), 1000)
        return con

    def kingdoms(self) -> list[str]:
        """Valores válidos del enum `kingdom` (los nombres de la tabla `kingdom`)."""
        if self._kingdoms is None:
            con = self._connect()
            try:
                rows = con.execute("SELECT name FROM kingdom ORDER BY display_order").fetchall()
            finally:
                con.close()
            self._kingdoms = [r["name"] for r in rows]
        return self._kingdoms

    # ---------------------------------------------------------------- consulta
    def run(self, q: GameDataQuery) -> GameDataResult:
        if q.kingdom is not None and q.kingdom not in self.kingdoms():
            raise ToolError(f"Reino desconocido: {q.kingdom!r}")
        table, cols, kingdom_col, name_col, order = _COLUMNS[q.entity_type]
        where, params = [], []
        if q.kingdom:
            where.append(f"{kingdom_col} = ?")
            params.append(q.kingdom)
        if q.name:
            names = [name_col] + (["outfit_set"] if q.entity_type is GameEntity.OUTFIT else [])
            where.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in names) + ")")
            params.extend([f"%{_escape_like(q.name)}%"] * len(names))
        clause = f" WHERE {' AND '.join(where)}" if where else ""

        start = time.perf_counter()
        con = self._connect()
        try:
            total = con.execute(f"SELECT COUNT(*) FROM {table}{clause}", params).fetchone()[0]
            rows: list[dict] = []
            if q.operation is Operation.COUNT and q.entity_type is GameEntity.POWER_MOON:
                sql = f"SELECT SUM(is_multi_moon) FROM {table}{clause}"
                multi = con.execute(sql, params).fetchone()[0] or 0
                rows = [{"entries": total, "multi_moons": multi, "source_url": None}]
                cur = con.execute(f"SELECT source_url FROM {table}{clause} LIMIT 1", params)
                if (r := cur.fetchone()) is not None:
                    rows[0]["source_url"] = r["source_url"]
            elif q.operation is not Operation.COUNT:
                cur = con.execute(f"SELECT {cols} FROM {table}{clause} ORDER BY {order}", params)
                rows = [dict(r) for r in cur.fetchmany(self.max_rows)]
            else:
                cur = con.execute(f"SELECT source_url FROM {table}{clause} LIMIT 1", params)
                if (r := cur.fetchone()) is not None:
                    rows = [{"entries": total, "source_url": r["source_url"]}]
            kingdom_info = None
            if q.kingdom:
                r = con.execute("SELECT * FROM kingdom WHERE name = ?", [q.kingdom]).fetchone()
                kingdom_info = dict(r) if r else None
            version = con.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise ToolError(f"La consulta superó {self.timeout_s:.1f} s") from exc
            raise ToolError(f"Error de la base del juego: {exc}") from exc
        finally:
            con.close()
        return GameDataResult(
            query=q,
            rows=rows,
            total=total,
            truncated=q.operation is not Operation.COUNT and total > len(rows),
            kingdom_info=kingdom_info,
            version=version[0] if version else "",
            latency_s=time.perf_counter() - start,
        )
