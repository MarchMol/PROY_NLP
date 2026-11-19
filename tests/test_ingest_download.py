"""Tests de la ingesta (cliente MediaWiki + snapshots) con la API mockeada.

Todo el contenido de páginas aquí es SINTÉTICO; solo imita la forma de las respuestas reales.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from marioqa.ingest.download import download
from marioqa.ingest.mediawiki import MediaWikiClient, MediaWikiError, page_url
from marioqa.ingest.snapshot import (
    list_snapshots,
    load_manifest,
    load_pages,
    read_seed_titles,
    write_snapshot,
)

UA = "MarioOdysseyRAG/test"

# Wiki falsa: título → (pageid, revid, wikitext). Contenido SINTÉTICO.
FAKE_WIKI: dict[str, tuple[int, int, str]] = {
    "Sand Kingdom": (1, 101, "SYNTHETIC sand {{tpl}}"),
    "Cascade Kingdom": (2, 202, "SYNTHETIC cascade"),
}
FAKE_REDIRECTS = {"Tostarena": "Sand Kingdom"}


class FakeApi:
    """Simula la Action API; registra cada request recibido."""

    def __init__(self, fail_first: list[httpx.Response] | None = None) -> None:
        self.calls: list[httpx.Request] = []
        self.fail_first = list(fail_first or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail_first:
            return self.fail_first.pop(0)
        params = dict(request.url.params)
        if request.method == "POST":
            params.update(dict(httpx.QueryParams(request.content.decode())))
        if params["action"] == "expandtemplates":
            text = params["text"].replace("{{tpl}}", "EXPANDED")
            return httpx.Response(200, json={"expandtemplates": {"wikitext": text}})
        return httpx.Response(200, json=self._query(params))

    def _query(self, params: dict[str, Any]) -> dict[str, Any]:
        titles = params["titles"].split("|")
        redirects, pages = [], []
        for t in titles:
            target = FAKE_REDIRECTS.get(t, t)
            if target != t:
                redirects.append({"from": t, "to": target})
            if target in FAKE_WIKI:
                pageid, revid, text = FAKE_WIKI[target]
                if "rvstart" in params:
                    revid, text = revid - 1, text + " (OLD)"
                pages.append(
                    {
                        "pageid": pageid,
                        "title": target,
                        "revisions": [
                            {
                                "revid": revid,
                                "timestamp": "2026-10-24T11:50:11Z",
                                "slots": {"main": {"content": text}},
                            }
                        ],
                    }
                )
            else:
                pages.append({"title": t, "missing": True})
        return {"batchcomplete": True, "query": {"redirects": redirects, "pages": pages}}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make_client(api: FakeApi, clock: FakeClock | None = None, **kw: Any) -> MediaWikiClient:
    clock = clock or FakeClock()
    return MediaWikiClient(
        "https://wiki.test/api.php",
        "https://wiki.test/",
        UA,
        transport=httpx.MockTransport(api),
        sleep=clock.sleep,
        clock=clock.clock,
        **kw,
    )


def test_fetch_latest_resolves_redirects_dedupes_and_reports_missing() -> None:
    api = FakeApi()
    with make_client(api) as client:
        result = client.fetch_latest(["Sand Kingdom", "Tostarena", "Cascade Kingdom", "Nope"])
    by_title = {p.title: p for p in result.pages}
    assert set(by_title) == {"Sand Kingdom", "Cascade Kingdom"}
    assert by_title["Sand Kingdom"].requested_titles == ["Sand Kingdom", "Tostarena"]
    assert by_title["Sand Kingdom"].revid == 101
    assert by_title["Sand Kingdom"].url == "https://wiki.test/Sand_Kingdom"
    assert result.missing == ["Nope"]


def test_requests_send_user_agent_and_maxlag() -> None:
    api = FakeApi()
    with make_client(api) as client:
        client.fetch_latest(["Sand Kingdom"])
    req = api.calls[0]
    assert req.headers["User-Agent"] == UA
    assert req.url.params["maxlag"] == "5"
    assert req.url.params["format"] == "json"


def test_batches_respect_batch_size() -> None:
    api = FakeApi()
    titles = [f"T{i}" for i in range(5)]
    with make_client(api, batch_size=2) as client:
        result = client.fetch_latest(titles)
    assert len(api.calls) == 3
    assert result.missing == titles


def test_rate_limit_waits_between_requests() -> None:
    api, clock = FakeApi(), FakeClock()
    with make_client(api, clock, min_interval_s=1.0, batch_size=1) as client:
        client.fetch_latest(["Sand Kingdom", "Cascade Kingdom", "Nope"])
    assert len(api.calls) == 3
    assert clock.sleeps == [1.0, 1.0]


def test_retries_on_503_and_maxlag_honoring_retry_after() -> None:
    api = FakeApi(
        fail_first=[
            httpx.Response(503),
            httpx.Response(
                200,
                headers={"Retry-After": "7"},
                json={"error": {"code": "maxlag", "info": "lagged"}},
            ),
        ]
    )
    clock = FakeClock()
    with make_client(api, clock, min_interval_s=0.0) as client:
        result = client.fetch_latest(["Sand Kingdom"])
    assert len(api.calls) == 3
    assert 7.0 in clock.sleeps
    assert result.pages[0].title == "Sand Kingdom"


def test_gives_up_after_max_retries() -> None:
    api = FakeApi(fail_first=[httpx.Response(503)] * 10)
    with (
        make_client(api, min_interval_s=0.0, max_retries=2) as client,
        pytest.raises(MediaWikiError),
    ):
        client.fetch_latest(["Sand Kingdom"])
    assert len(api.calls) == 3


def test_non_retryable_api_error_raises() -> None:
    api = FakeApi(fail_first=[httpx.Response(200, json={"error": {"code": "badvalue"}})])
    with make_client(api) as client, pytest.raises(MediaWikiError, match="badvalue"):
        client.fetch_latest(["Sand Kingdom"])


def test_fetch_as_of_uses_rvstart() -> None:
    api = FakeApi()
    with make_client(api) as client:
        page = client.fetch_as_of("Tostarena", "2025-01-01T00:00:00Z")
        gone = client.fetch_as_of("Nope", "2025-01-01T00:00:00Z")
    assert page is not None and page.revid == 100 and page.wikitext.endswith("(OLD)")
    assert api.calls[0].url.params["rvstart"] == "2025-01-01T00:00:00Z"
    assert api.calls[0].url.params["rvdir"] == "older"
    assert gone is None


def test_download_expands_templates_via_post() -> None:
    api = FakeApi()
    with make_client(api) as client:
        pages, missing = download(client, ["Sand Kingdom", "Nope"])
    assert missing == ["Nope"]
    assert pages[0].expanded_wikitext == "SYNTHETIC sand EXPANDED"
    assert api.calls[-1].method == "POST"


def test_snapshot_roundtrip_and_hash_check(tmp_path: Path) -> None:
    api = FakeApi()
    with make_client(api) as client:
        pages, missing = download(client, ["Cascade Kingdom", "Sand Kingdom", "Nope"])
    snap = write_snapshot(
        tmp_path,
        "20261118",
        pages,
        missing,
        cutoff="2026-11-18T00:00:00Z",
        mode="latest",
        source="test",
    )
    manifest = load_manifest(snap)
    assert manifest.snapshot_id == "20261118"
    assert [e.pageid for e in manifest.pages] == [1, 2]
    assert manifest.missing == ["Nope"]
    assert {p.revid for p in load_pages(snap)} == {101, 202}
    assert list_snapshots(tmp_path) == ["20261118"]

    with pytest.raises(FileExistsError):
        write_snapshot(tmp_path, "20261118", pages, [], cutoff="x", mode="latest", source="t")

    page_file = snap / manifest.pages[0].file
    data = json.loads(page_file.read_text(encoding="utf-8"))
    data["wikitext"] = "tampered"
    page_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Hash"):
        load_pages(snap)


def test_read_seed_titles_ignores_comments_and_dupes(tmp_path: Path) -> None:
    f = tmp_path / "seed.txt"
    f.write_text("# c\nSand Kingdom\n\n  Cap Kingdom \nSand Kingdom\n", encoding="utf-8")
    assert read_seed_titles(f) == ["Sand Kingdom", "Cap Kingdom"]


def test_repo_seed_titles_cover_17_kingdoms() -> None:
    seeds = read_seed_titles(Path(__file__).resolve().parents[1] / "data" / "seed_titles.txt")
    kingdoms = [
        t
        for t in seeds
        if t.endswith("Kingdom")
        or t.endswith("Kingdom (Super Mario Odyssey)")
        or t in {"Dark Side", "Darker Side"}
    ]
    assert len([k for k in kingdoms if not k.startswith("List of")]) == 17


def test_page_url_escaping() -> None:
    assert page_url("https://www.mariowiki.com/", "Bowser's Kingdom") == (
        "https://www.mariowiki.com/Bowser's_Kingdom"
    )
    assert page_url("https://w/", "Mushroom Kingdom (Super Mario Odyssey)") == (
        "https://w/Mushroom_Kingdom_(Super_Mario_Odyssey)"
    )
