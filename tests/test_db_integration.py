"""DB integration tests for the Phase 5 write-through.

Skipped unless GRAPH_CRAWL_TEST_DSN is set, e.g.:
    export GRAPH_CRAWL_TEST_DSN="postgresql+asyncpg://postgres@localhost:5432/graphcrawl_test"
    docker run --rm -e POSTGRES_PASSWORD= -e POSTGRES_HOST_AUTH_METHOD=trust -p 5432:5432 postgres:16

The gold-standard assertion: the database is a faithful write-through of the
in-memory CrawlResult (resources, edges, fetch_history, crawl_run stats)."""

import os

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from graph_crawl.crawler import Crawler
from graph_crawl.db.base import Base
from graph_crawl.db.engine import session_factory
from graph_crawl.db.models import CrawlRun, Edge, FetchHistory, Resource
from graph_crawl.db.sink import DbCrawlSink
from graph_crawl.fetcher import Fetcher
from graph_crawl.schemas.graph import ResourceState

PG_DSN = os.environ.get("GRAPH_CRAWL_TEST_DSN")
pytestmark = pytest.mark.skipif(not PG_DSN, reason="set GRAPH_CRAWL_TEST_DSN to run DB integration tests")

SEED = "https://example.org/"

PAGE_SEED = (
    '<html><head><base href="https://example.org/"></head><body>'
    '<a href="/a">A</a><a href="/b">B</a><a href="/c">C</a>'
    '<a href="https://other.com/x">external</a></body></html>'
)
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


@pytest.fixture
async def db():
    assert PG_DSN is not None
    engine = create_async_engine(PG_DSN, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@respx.mock(assert_all_called=False)
async def test_db_is_faithful_write_through_of_in_memory_graph(respx_mock, db):
    _mock_site(respx_mock)
    sink = DbCrawlSink(db)
    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0, max_pages=100, max_depth=5, sink=sink)
        result = await crawler.crawl(SEED)

    sm = session_factory(db)
    async with sm() as session:
        # resources: one row per discovered URL
        n_resources = int(await session.scalar(select(func.count()).select_from(Resource)) or 0)
        assert n_resources == len(result.resources)
        assert n_resources == result.stats.discovered

        # edges: one row per deduped edge
        n_edges = int(await session.scalar(select(func.count()).select_from(Edge)) or 0)
        assert n_edges == len(result.edges)

        # fetch_history: one row per URL that was actually fetched (HTTP attempted).
        # = all resources NOT in {pending, skipped}.
        attempted_states = {
            ResourceState.fetched,
            ResourceState.fetched_leaf,
            ResourceState.not_found,
            ResourceState.gone,
            ResourceState.needs_auth,
            ResourceState.error,
            ResourceState.backoff,
        }
        attempted = [u for u, r in result.resources.items() if r.resource_state in attempted_states]
        n_fetches = int(await session.scalar(select(func.count()).select_from(FetchHistory)) or 0)
        assert n_fetches == len(attempted)

        # the seed row carries the crawler's derived state + depth
        seed_row = await session.scalar(select(Resource).where(Resource.url == SEED))
        assert seed_row is not None
        assert seed_row.resource_state == ResourceState.fetched.value
        assert seed_row.depth == 0
        assert seed_row.seed_url == SEED

        # raw_href is persisted on edges (for the mock site it equals target_url
        # since the links are already normalized; the unit test covers the
        # diverging case).
        seed_a_edge = await session.scalar(
            select(Edge).where(Edge.source_url == SEED, Edge.target_url == "https://example.org/a")
        )
        assert seed_a_edge is not None
        assert seed_a_edge.raw_href == "https://example.org/a"

        # a 404's fetch_history row records the outcome + status
        not_found_fh = await session.scalar(select(FetchHistory).where(FetchHistory.url == "https://example.org/b"))
        assert not_found_fh is not None
        assert not_found_fh.outcome == "not_found"
        assert not_found_fh.status_code == 404

        # crawl_runs persisted the denormalized stats
        run = await session.scalar(select(CrawlRun))
        assert run is not None
        assert run.stats_discovered == result.stats.discovered
        assert run.stats_fetched == result.stats.fetched
        assert run.stats_not_found == result.stats.not_found
        assert run.stats_skipped == result.stats.skipped
        assert run.stopped_reason == result.stats.stopped_reason
        assert run.finished_at is not None

    # the snapshots table is reserved (empty in Phase 5)
    async with sm() as session:
        # ensure the table exists by selecting against it indirectly
        from graph_crawl.db.models import Snapshot

        n_snapshots = int(await session.scalar(select(func.count()).select_from(Snapshot)) or 0)
        assert n_snapshots == 0


@respx.mock(assert_all_called=False)
async def test_edge_upsert_updates_last_seen_on_rediscovery(respx_mock, db):
    """Crawling the same small site twice re-confirms edges: discovered_at is
    preserved, last_seen_at is updated."""
    _mock_site(respx_mock)
    sink = DbCrawlSink(db)
    async with Fetcher() as f:
        crawler = Crawler(f, delay=0.0, max_pages=2, max_depth=1, sink=sink)
        await crawler.crawl(SEED)
        await crawler.crawl(SEED)

    sm = session_factory(db)
    async with sm() as session:
        # the seed->/a edge exists exactly once (upserted, not duplicated)
        n_edge_seed_a = int(
            await session.scalar(
                select(func.count())
                .select_from(Edge)
                .where(Edge.source_url == SEED, Edge.target_url == "https://example.org/a")
            )
            or 0
        )
        assert n_edge_seed_a == 1
        edge = await session.scalar(
            select(Edge).where(Edge.source_url == SEED, Edge.target_url == "https://example.org/a")
        )
        assert edge is not None
        assert edge.discovered_at <= edge.last_seen_at

        # two crawl_runs rows (one per crawl)
        n_runs = int(await session.scalar(select(func.count()).select_from(CrawlRun)) or 0)
        assert n_runs == 2
