"""Cliente mínimo de la API de MediaWiki (Action API) para la Super Mario Wiki."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from marioqa.logging_setup import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRYABLE_API_ERRORS = {"maxlag", "ratelimited", "readonly"}
# Máximo de títulos por request que permite MediaWiki a clientes sin privilegios.
MAX_TITLES_PER_QUERY = 50


class MediaWikiError(RuntimeError):
    """Error no recuperable de la API."""


class RawPage(BaseModel):
    """Una página tal como viene de la API, en una revisión concreta."""

    requested_titles: list[str]
    title: str
    pageid: int
    revid: int
    timestamp: str
    url: str
    wikitext: str
    expanded_wikitext: str | None = None


class FetchResult(BaseModel):
    pages: list[RawPage]
    missing: list[str]


def page_url(base_url: str, title: str) -> str:
    """URL pública del artículo (formato estándar de MediaWiki)."""
    return base_url.rstrip("/") + "/" + quote(title.replace(" ", "_"), safe="()'!,:")


class MediaWikiClient:
    """Cliente con User-Agent propio, intervalo mínimo entre requests, `maxlag` y reintentos."""

    def __init__(
        self,
        api_url: str,
        base_url: str,
        user_agent: str,
        *,
        min_interval_s: float = 1.0,
        max_retries: int = 4,
        timeout_s: float = 30.0,
        batch_size: int = 20,
        maxlag_s: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= batch_size <= MAX_TITLES_PER_QUERY:
            raise ValueError(f"batch_size debe estar entre 1 y {MAX_TITLES_PER_QUERY}")
        self.api_url = api_url
        self.base_url = base_url
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.batch_size = batch_size
        self.maxlag_s = maxlag_s
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"},
            timeout=timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> MediaWikiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ core
    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self.min_interval_s - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return min(2.0**attempt, 60.0)

    def request(self, params: dict[str, Any], *, post: bool = False) -> dict[str, Any]:
        """Hace una llamada a la API con reintentos; devuelve el JSON."""
        full = {"format": "json", "formatversion": 2, "maxlag": self.maxlag_s, **params}
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = (
                    self._http.post(self.api_url, data=full)
                    if post
                    else self._http.get(self.api_url, params=full)
                )
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise MediaWikiError(f"Error de red: {exc}") from exc
                delay = self._backoff(attempt, None)
                log.warning("Error de red (%s); reintento en %.0fs", exc, delay)
                self._sleep(delay)
                continue

            if resp.status_code in RETRYABLE_STATUS:
                if attempt == self.max_retries:
                    raise MediaWikiError(f"HTTP {resp.status_code} tras {attempt + 1} intentos")
                delay = self._backoff(attempt, resp.headers.get("Retry-After"))
                log.warning("HTTP %s; reintento en %.0fs", resp.status_code, delay)
                self._sleep(delay)
                continue
            if resp.status_code != 200:
                raise MediaWikiError(f"HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            error = data.get("error")
            if error:
                code = error.get("code", "")
                if code in RETRYABLE_API_ERRORS and attempt < self.max_retries:
                    delay = self._backoff(attempt, resp.headers.get("Retry-After"))
                    log.warning("API error %s; reintento en %.0fs", code, delay)
                    self._sleep(delay)
                    continue
                raise MediaWikiError(f"API error {code}: {error.get('info', '')}")
            return data
        raise MediaWikiError("Reintentos agotados")  # pragma: no cover

    # ------------------------------------------------------------- endpoints
    def fetch_latest(self, titles: Iterable[str]) -> FetchResult:
        """Revisión actual de cada título; sigue redirecciones y deduplica por pageid."""
        titles = list(dict.fromkeys(titles))
        by_id: dict[int, RawPage] = {}
        missing: list[str] = []
        for start in range(0, len(titles), self.batch_size):
            batch = titles[start : start + self.batch_size]
            pages, batch_missing = self._fetch_batch(batch)
            missing.extend(batch_missing)
            for page in pages:
                if page.pageid in by_id:
                    by_id[page.pageid].requested_titles.extend(page.requested_titles)
                else:
                    by_id[page.pageid] = page
        return FetchResult(pages=list(by_id.values()), missing=missing)

    def _fetch_batch(self, batch: list[str]) -> tuple[list[RawPage], list[str]]:
        params: dict[str, Any] = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
            "redirects": 1,
            "titles": "|".join(batch),
        }
        raw_pages: dict[str, dict[str, Any]] = {}
        normalized: dict[str, str] = {}
        redirects: dict[str, str] = {}
        cont: dict[str, Any] = {}
        while True:
            data = self.request({**params, **cont})
            query = data.get("query", {})
            normalized.update({n["from"]: n["to"] for n in query.get("normalized", [])})
            redirects.update({r["from"]: r["to"] for r in query.get("redirects", [])})
            for p in query.get("pages", []):
                prev = raw_pages.get(p["title"])
                if prev is None or ("revisions" in p and "revisions" not in prev):
                    raw_pages[p["title"]] = p
            if "continue" not in data:
                break
            cont = data["continue"]

        pages: dict[str, RawPage] = {}
        missing: list[str] = []
        for requested in batch:
            title = normalized.get(requested, requested)
            title = redirects.get(title, title)
            p = raw_pages.get(title)
            if p is None or p.get("missing") or p.get("invalid") or not p.get("revisions"):
                missing.append(requested)
                continue
            if title in pages:
                pages[title].requested_titles.append(requested)
                continue
            pages[title] = self._to_raw_page(p, [requested])
        return list(pages.values()), missing

    def fetch_as_of(self, title: str, timestamp: str) -> RawPage | None:
        """Revisión vigente de `title` en `timestamp` (ISO 8601 UTC); None si no existía."""
        data = self.request(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "rvlimit": 1,
                "rvdir": "older",
                "rvstart": timestamp,
                "redirects": 1,
                "titles": title,
            }
        )
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing") or not pages[0].get("revisions"):
            return None
        return self._to_raw_page(pages[0], [title])

    def expand_templates(self, title: str, wikitext: str) -> str:
        """Expande plantillas del wikitexto (necesario en listas generadas por plantilla)."""
        data = self.request(
            {"action": "expandtemplates", "title": title, "text": wikitext, "prop": "wikitext"},
            post=True,
        )
        return data["expandtemplates"]["wikitext"]

    def _to_raw_page(self, p: dict[str, Any], requested: list[str]) -> RawPage:
        rev = p["revisions"][0]
        return RawPage(
            requested_titles=requested,
            title=p["title"],
            pageid=p["pageid"],
            revid=rev["revid"],
            timestamp=rev["timestamp"],
            url=page_url(self.base_url, p["title"]),
            wikitext=rev["slots"]["main"]["content"],
        )
