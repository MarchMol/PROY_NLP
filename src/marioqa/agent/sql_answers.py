"""Respuestas deterministas a partir de filas de SQLite (sin LLM) y su forma de evidencia.

Un dato exacto no pasa por el modelo: la cifra sale tal cual de la tabla, con su fuente. Para
consultas mixtas, el resultado se convierte en un fragmento de evidencia más (id `sql:…`) que el
generador puede citar como cualquier chunk.
"""

from __future__ import annotations

from marioqa.ingest.chunking import Chunk
from marioqa.tools.game_data import GameDataResult, GameEntity, Operation


def _coins(price: int | None, currency: str | None) -> str:
    if price is None:
        return "precio no indicado"
    unit = "monedas regionales" if currency == "regional_coin" else "monedas"
    return f"{price} {unit}"


def _in(kingdom: str | None) -> str:
    return f" en {kingdom}" if kingdom else ""


def render_rows(res: GameDataResult) -> str:
    """Texto plano (inglés/datos) del resultado, usado como evidencia y para verificar soporte."""
    q = res.query
    lines = [
        f"query_game_data({q.entity_type}, {q.operation}, kingdom={q.kingdom}, "
        f"name={q.name}) → {res.total} filas"
    ]
    if res.kingdom_info:
        k = res.kingdom_info
        lines.append(
            f"Kingdom infobox: {k['name']}; moons: {k['moons_total']}; regional coins: "
            f"{k['regional_coins']}; boss: {k['boss']}; previous: {k['previous']}; "
            f"next: {k['next']}. The infobox total counts each Multi Moon as 3 Power Moons."
        )
    for r in res.rows:
        lines.append(
            "; ".join(f"{k}: {v}" for k, v in r.items() if k != "source_url" and v is not None)
            + "."
        )
    if res.truncated:
        lines.append(f"(Mostrando {len(res.rows)} de {res.total}.)")
    return "\n".join(lines)


def to_evidence(res: GameDataResult) -> list[Chunk]:
    """Un pseudo-chunk por fuente del resultado, con el texto completo de las filas."""
    text = render_rows(res)
    return [
        Chunk(
            chunk_id=s.chunk_id,
            text=f"{s.source} > {s.page}\n{text}",
            source=s.source,
            url=s.url,
            page=s.page,
            sections=[s.page],
            version=res.version,
            revid=0,
            cutoff="",
            hash="",
            entity_type=str(res.query.entity_type),
            n_tokens=len(text.split()),
            position=0,
        )
        for s in res.sources()
    ]


def compose_answer(res: GameDataResult) -> str:
    """Respuesta en español con la cifra exacta y la marca de cita de la primera fuente."""
    q = res.query
    sources = res.sources()
    cite = "".join(f" [{s.chunk_id}]" for s in sources)
    k = res.kingdom_info

    if q.operation is Operation.COUNT:
        if q.entity_type is GameEntity.POWER_MOON:
            row = res.rows[0] if res.rows else {"entries": 0, "multi_moons": 0}
            if k and k.get("moons_total") is not None:
                return (
                    f"{k['name']} tiene {k['moons_total']} Power Moons según el infobox de la "
                    f"wiki, donde cada Multi Moon cuenta como 3. La lista del reino tiene "
                    f"{row['entries']} entradas, de las cuales {row['multi_moons']} son Multi "
                    f"Moons.{cite}"
                )
            return (
                f"La lista de Power Moons tiene {row['entries']} entradas{_in(q.kingdom)} "
                f"({row['multi_moons']} de ellas son Multi Moons, que valen 3 lunas "
                f"cada una).{cite}"
            )
        if q.entity_type is GameEntity.OUTFIT:
            where = f" que se venden en la Crazy Cap de {q.kingdom}" if q.kingdom else ""
            return f"Hay {res.total} piezas de atuendo{where} en la lista de la wiki.{cite}"
        return f"La Crazy Cap de {q.kingdom} vende {res.total} artículos.{cite}"

    lines: list[str] = []
    for r in res.rows:
        if q.entity_type is GameEntity.POWER_MOON:
            multi = " (Multi Moon)" if r.get("is_multi_moon") else ""
            line = f"#{r.get('number')} {r['name']}{multi}"
            if q.operation is Operation.GET:
                line += f" — {r['kingdom']}. Ubicación: {r.get('location') or 'no indicada'}"
                if r.get("availability"):
                    line += f" Disponibilidad: {r['availability']}"
        elif q.entity_type is GameEntity.OUTFIT:
            if r.get("price") is not None and r.get("shop_kingdom"):
                how = (
                    f"se compra en la Crazy Cap de {r['shop_kingdom']} por "
                    f"{_coins(r['price'], r['currency'])}"
                )
            else:
                how = f"cómo se obtiene (wiki): {r.get('unlock_method') or 'no indicado'}"
            line = (
                f"{r['name']}"
                + (f", conjunto «{r['outfit_set']}»" if r.get("outfit_set") else "")
                + f": {how}"
            )
        else:
            line = f"{r['item']}: {_coins(r.get('price'), r.get('currency'))}"
            if r.get("note"):
                line += f" ({r['note']})"
        lines.append(f"- {line}")

    head = {
        GameEntity.POWER_MOON: f"Power Moons{_in(q.kingdom)}",
        GameEntity.OUTFIT: "Atuendos" + (f" que se venden en {q.kingdom}" if q.kingdom else ""),
        GameEntity.SHOP: f"Artículos de la Crazy Cap{_in(q.kingdom)}",
    }[q.entity_type]
    if q.operation is Operation.GET:
        head = f"Resultados para «{q.name}»"
    more = f"\n(Se muestran {len(res.rows)} de {res.total}.)" if res.truncated else ""
    return f"{head} ({res.total}):{cite}\n" + "\n".join(lines) + more
