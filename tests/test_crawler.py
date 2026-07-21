import asyncio
import httpx
import respx

from graph_crawl.classify import is_html_url, url_resource_type
from graph_crawl.crawler import Crawler
from graph_crawl.fetcher import Fetcher
from graph_crawl.frontier import Frontier
from graph_crawl.schemas.graph import ResourceState, ResourceType
from graph_crawl.scope import host_scope

SEED = "https://example.org/"

PAGE_SEED = """
<html><head><base href="https://example.org/"></head><body>
  <a href="/a">A</a>
  <a href="/b">B</a>
  <a href="/c">C</a>
  <a href="https://other.com/x">external</a>
</body></html>
"""
PAGE_A = '<html><body><a href="/d.pdf">pdf</a><a href="/e">E</a><a href="/a">self</a></body></html>'
PAGE_C = '<html><body><a href="/">home</a></body></html>'
PAGE_E = "<html><body>no links</body></html>"


def _mock_site(respx_mock) -> None:
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(
            200, content=PAGE_SEED.encode(), headers={"Content-Type": "text/html; charset=utf-8"}
        )
    )
    respx_mock.get("https://example.org/a").mock(
        return_value=httpx.Response(200, content=PAGE_A.encode(), headers={"Content-Type": "text/html"})
    )
    respx_mock.get("https://example.org/b").mock(return_value=httpx.Response(404, content=b"nope"))
    respx_mock.get("https://example.org/c").mock(
        return_value=httpx.Response(200, content=PAGE_C.encode(), headers={"Content-Type": "text/html"})
    )
    respx_mock.get("https://example.org/e").mock(
        return_value=httpx.Response(200, content=PAGE_E.encode(), headers={"Content-Type": "text/html"})
    )


# --- Frontier ---


def test_frontier_bfs_order_and_dedup():
    fr = Frontier()
    assert fr.push("a", 1) is True
    assert fr.push("b", 1) is True
    assert fr.push("a", 2) is False  # already seen -> not re-enqueued
    assert len(fr) == 2

    first_pop = fr.pop()
    assert first_pop is not None
    assert first_pop.url == "a"  # FIFO

    second_pop = fr.pop()
    assert second_pop is not None
    assert second_pop.url == "b"  # FIFO

    assert fr.pop() is None


def test_frontier_depth_preserved():
    fr = Frontier()
    fr.push("a", 3)
    pop_result = fr.pop()
    assert pop_result is not None
    assert pop_result.depth == 3


# --- scope ---


def test_host_scope_exact_host():
    assert host_scope("https://example.org/a", "https://example.org/") is True
    assert host_scope("https://blog.example.org/a", "https://example.org/") is False
    assert host_scope("https://other.com/x", "https://example.org/") is False
    assert host_scope("https://example.org:443/a", "https://example.org/") is True  # port stripped


# --- classify ---


def test_classify_extensions():
    assert url_resource_type("https://x.org/a.html") is ResourceType.html
    assert url_resource_type("https://x.org/a.PDF") is ResourceType.pdf  # lowercased
    assert url_resource_type("https://x.org/a.pdf") is ResourceType.pdf
    assert url_resource_type("https://x.org/img.jpeg") is ResourceType.image
    assert url_resource_type("https://x.org/about") is ResourceType.unknown  # extensionless
    assert url_resource_type("https://x.org/") is ResourceType.unknown
    assert url_resource_type("https://x.org/dir/") is ResourceType.unknown
    assert url_resource_type("https://x.org/foo.xyz") is ResourceType.other


def test_is_html_url():
    assert is_html_url("https://x.org/a.html") is True
    assert is_html_url("https://x.org/about") is True  # unknown -> potentially html, fetched
    assert is_html_url("https://x.org/a.pdf") is False
    assert is_html_url("https://x.org/img.png") is False


# --- full crawl ---


@respx.mock(assert_all_called=False)
async def test_full_crawl_bfs_states_edges_and_stats(respx_mock):
    _mock_site(respx_mock)
    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0, max_pages=100, max_depth=5)
        result = await crawler.crawl(SEED)

    r = result.resources
    # fetched HTML pages, with correct BFS depths
    assert r["https://example.org/"].resource_state is ResourceState.fetched
    assert r["https://example.org/"].depth == 0
    assert r["https://example.org/a"].resource_state is ResourceState.fetched
    assert r["https://example.org/a"].depth == 1
    assert r["https://example.org/c"].resource_state is ResourceState.fetched
    assert r["https://example.org/e"].resource_state is ResourceState.fetched
    assert r["https://example.org/e"].depth == 2

    # 404 -> not_found
    assert r["https://example.org/b"].resource_state is ResourceState.not_found

    # PDF leaf: discovered, pending, NEVER fetched (respx would raise if it were)
    assert r["https://example.org/d.pdf"].resource_state is ResourceState.pending
    assert r["https://example.org/d.pdf"].resource_type is ResourceType.pdf

    # out-of-scope external: recorded as skipped, edge preserved
    assert r["https://other.com/x"].resource_state is ResourceState.skipped

    # edges (deduped by (source, target))
    edge_pairs = {(e.source, e.target) for e in result.edges}
    assert ("https://example.org/", "https://example.org/a") in edge_pairs
    assert ("https://example.org/", "https://example.org/b") in edge_pairs
    assert ("https://example.org/", "https://example.org/c") in edge_pairs
    assert ("https://example.org/", "https://other.com/x") in edge_pairs
    assert ("https://example.org/a", "https://example.org/d.pdf") in edge_pairs
    assert ("https://example.org/a", "https://example.org/e") in edge_pairs
    assert ("https://example.org/a", "https://example.org/a") in edge_pairs  # self link kept
    assert ("https://example.org/c", "https://example.org/") in edge_pairs
    assert (
        sum(1 for e in result.edges if e.source == "https://example.org/" and e.target == "https://example.org/a") == 1
    )  # deduped to a single edge

    # stats
    assert result.stats.fetched == 4  # /, /a, /c, /e
    assert result.stats.not_found == 1
    assert result.stats.skipped == 1
    assert result.stats.discovered == 7
    assert result.stats.stopped_reason == "frontier exhausted"

    # summarize() runs and is informative
    assert "Seed:" in result.summarize()


# --- limits ---


@respx.mock(assert_all_called=False)
async def test_max_pages_limit_stops_crawl(respx_mock):
    _mock_site(respx_mock)
    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0, max_pages=2)
        result = await crawler.crawl(SEED)

    assert result.stats.fetched == 2  # seed + /a
    assert result.stats.stopped_reason == "max_pages"
    assert result.resources["https://example.org/"].resource_state is ResourceState.fetched
    assert result.resources["https://example.org/a"].resource_state is ResourceState.fetched
    assert result.resources["https://example.org/b"].resource_state is ResourceState.pending  # enqueued, not fetched
    assert result.resources["https://example.org/c"].resource_state is ResourceState.pending


@respx.mock(assert_all_called=False)
async def test_max_depth_limit_prevents_enqueue_beyond_depth(respx_mock):
    _mock_site(respx_mock)
    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0, max_depth=1)
        result = await crawler.crawl(SEED)

    # depth-1 pages fetched; /e (depth 2) discovered but NOT enqueued/fetched
    assert result.resources["https://example.org/a"].resource_state is ResourceState.fetched
    assert result.resources["https://example.org/e"].resource_state is ResourceState.pending
    assert result.resources["https://example.org/e"].depth == 2
    assert result.stats.stopped_reason == "frontier exhausted"


# --- politeness ---


@respx.mock(assert_all_called=False)
async def test_politeness_delay_applied_between_fetches(respx_mock, monkeypatch):
    _mock_site(respx_mock)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.5, max_pages=3)
        await crawler.crawl(SEED)

    # first fetch: no sleep. Each later fetch waits ~= delay.
    assert len(sleeps) >= 2
    assert all(abs(s - 0.5) < 0.05 for s in sleeps)


@respx.mock(assert_all_called=False)
async def test_retry_after_honored_as_backoff(respx_mock, monkeypatch):
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(200, content=PAGE_SEED.encode(), headers={"Content-Type": "text/html"})
    )
    respx_mock.get("https://example.org/a").mock(
        return_value=httpx.Response(200, content=PAGE_A.encode(), headers={"Content-Type": "text/html"})
    )
    respx_mock.get("https://example.org/b").mock(return_value=httpx.Response(429, headers={"Retry-After": "10"}))
    respx_mock.get("https://example.org/c").mock(
        return_value=httpx.Response(200, content=PAGE_C.encode(), headers={"Content-Type": "text/html"})
    )
    respx_mock.get("https://example.org/e").mock(
        return_value=httpx.Response(200, content=PAGE_E.encode(), headers={"Content-Type": "text/html"})
    )

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.2)
        result = await crawler.crawl(SEED)

    assert result.resources["https://example.org/b"].resource_state is ResourceState.backoff
    assert result.stats.backoff == 1
    # the wait after the 429 must include the Retry-After seconds
    assert any(s >= 10.0 for s in sleeps)


# --- resilience ---


@respx.mock(assert_all_called=False)
async def test_network_error_does_not_stop_crawl(respx_mock):
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(
            200, content=b'<a href="/a">a</a><a href="/b">b</a>', headers={"Content-Type": "text/html"}
        )
    )
    respx_mock.get("https://example.org/a").mock(side_effect=httpx.ConnectError("boom"))
    respx_mock.get("https://example.org/b").mock(
        return_value=httpx.Response(200, content=b"ok", headers={"Content-Type": "text/html"})
    )

    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0)
        result = await crawler.crawl(SEED)

    assert result.resources["https://example.org/a"].resource_state is ResourceState.error
    assert result.resources["https://example.org/b"].resource_state is ResourceState.fetched
    assert result.stats.error == 1
    assert result.stats.fetched == 2  # seed + /b


@respx.mock(assert_all_called=False)
async def test_html_url_serving_pdf_becomes_fetched_leaf(respx_mock):
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(200, content=b'<a href="/page.html">p</a>', headers={"Content-Type": "text/html"})
    )
    respx_mock.get("https://example.org/page.html").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 ...", headers={"Content-Type": "application/pdf"})
    )

    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0)
        result = await crawler.crawl(SEED)

    page = result.resources["https://example.org/page.html"]
    assert page.resource_state is ResourceState.fetched_leaf
    assert page.resource_type is ResourceType.pdf
    assert result.stats.fetched_leaf == 1
    assert result.stats.fetched == 1  # only the seed


@respx.mock(assert_all_called=False)
async def test_seed_is_normalized(respx_mock):
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(200, content=b"<a href='/a'>x</a>", headers={"Content-Type": "text/html"})
    )
    respx_mock.get("https://example.org/a").mock(
        return_value=httpx.Response(200, content=b"ok", headers={"Content-Type": "text/html"})
    )

    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0)
        result = await crawler.crawl("https://EXAMPLE.org/")

    assert result.seed == "https://example.org/"
    assert "https://example.org/" in result.resources


@respx.mock(assert_all_called=False)
async def test_410_maps_to_gone_state(respx_mock):
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(200, content=b'<a href="/gone">g</a>', headers={"Content-Type": "text/html"})
    )
    respx_mock.get("https://example.org/gone").mock(return_value=httpx.Response(410))

    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0)
        result = await crawler.crawl(SEED)

    assert result.resources["https://example.org/gone"].resource_state is ResourceState.gone
    assert result.stats.gone == 1


@respx.mock(assert_all_called=False)
async def test_401_and_403_map_to_needs_auth(respx_mock):
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(
            200,
            content=b'<a href="/secret">s</a><a href="/denied">d</a>',
            headers={"Content-Type": "text/html"},
        )
    )
    respx_mock.get("https://example.org/secret").mock(return_value=httpx.Response(401))
    respx_mock.get("https://example.org/denied").mock(return_value=httpx.Response(403))

    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0)
        result = await crawler.crawl(SEED)

    assert result.resources["https://example.org/secret"].resource_state is ResourceState.needs_auth
    assert result.resources["https://example.org/denied"].resource_state is ResourceState.needs_auth
    assert result.stats.needs_auth == 2


async def test_default_sink_is_nullsink():
    """A Crawler with no sink configured uses NullSink and runs unchanged (Phase 4 behavior)."""
    async with Fetcher() as f:
        c = Crawler(f)
    from graph_crawl.sink import NullSink

    assert isinstance(c._sink, NullSink)


@respx.mock(assert_all_called=False)
async def test_edge_raw_href_preserves_pre_normalize_url(respx_mock):
    """raw_href is the resolved-absolute URL BEFORE normalize(); target is the
    normalized key. A default port (:443) is stripped by normalize() but kept
    in raw_href, so the two diverge — confirming the original is preserved."""
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(
            200,
            content=b'<a href="https://example.org:443/x">x</a>',
            headers={"Content-Type": "text/html"},
        )
    )
    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0, max_depth=0, max_pages=1)  # discover only, don't enqueue children
        result = await crawler.crawl(SEED)

    edge = next(e for e in result.edges if e.target == "https://example.org/x")
    assert edge.raw_href == "https://example.org:443/x"  # original port preserved
    assert edge.target == "https://example.org/x"  # normalized: default port stripped
    # the resource is keyed by the normalized URL; the :443 form is not a separate resource
    assert "https://example.org/x" in result.resources
    assert "https://example.org:443/x" not in result.resources


@respx.mock(assert_all_called=False)
async def test_tracking_params_stripped_for_identity_but_preserved_in_raw_href(respx_mock):
    """The two halves of docs/normalization-policy.md working together:
    `fbclid` is stripped from the resource's primary key (identity), but the
    original href is preserved verbatim in `raw_href` (reconstruction)."""
    respx_mock.get("https://example.org/").mock(
        return_value=httpx.Response(
            200,
            content=b'<a href="/page?fbclid=ABC">x</a>',
            headers={"Content-Type": "text/html"},
        )
    )
    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0, max_depth=0, max_pages=1)  # discover only
        result = await crawler.crawl(SEED)

    edge = next(e for e in result.edges if e.target == "https://example.org/page")
    assert edge.target == "https://example.org/page"  # fbclid stripped for identity
    assert edge.raw_href == "https://example.org/page?fbclid=ABC"  # original preserved
    # one resource (the stripped key), not two
    assert "https://example.org/page" in result.resources
    assert "https://example.org/page?fbclid=ABC" not in result.resources
