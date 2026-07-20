"""Database-backed CrawlSink. Writes through to Postgres via SQLAlchemy async.

One session (and one transaction) per ``record_*`` call: a crawl run is NOT one
giant transaction (that would hold locks for the whole crawl). Per-record commits
keep the DB lock surface tiny and make a crash lose at most one record. For
``DbCrawlSink`` construction in tests, pass ``sessionmaker=`` to inject a fake."""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from graph_crawl.db import repository
from graph_crawl.db.engine import session_factory
from graph_crawl.schemas.fetch import FetchResult
from graph_crawl.schemas.graph import CrawlResult, Edge, Resource


class DbCrawlSink:
    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        if sessionmaker is not None:
            self._sessionmaker: async_sessionmaker[AsyncSession] = sessionmaker
        elif engine is not None:
            self._sessionmaker = session_factory(engine)
        else:
            raise ValueError("DbCrawlSink requires either engine or sessionmaker.")

    async def start_run(
        self,
        seed: str,
        started_at: datetime,
        *,
        max_pages: int,
        max_depth: int,
        max_frontier_size: int,
        delay: float,
    ) -> int:
        async with self._sessionmaker() as session:
            run_id = await repository.insert_crawl_run(
                session,
                seed_url=seed,
                started_at=started_at,
                max_pages=max_pages,
                max_depth=max_depth,
                max_frontier_size=max_frontier_size,
                delay=delay,
            )
            await session.commit()
            return run_id

    async def finish_run(self, run_id: int, result: CrawlResult) -> None:
        async with self._sessionmaker() as session:
            await repository.update_crawl_run(
                session,
                run_id,
                finished_at=result.finished_at,
                stopped_reason=result.stats.stopped_reason,
                stats=result.stats,
            )
            await session.commit()

    async def record_resource(self, resource: Resource, *, run_id: int, seed_url: str) -> None:
        # run_id is unused: resources has no discovery_run_id column — provenance
        # is recorded on fetch_history/edges, which are per-run. The parameter
        # exists only to keep CrawlSink's record_* signatures uniform.
        async with self._sessionmaker() as session:
            await repository.upsert_resource(session, resource, seed_url=seed_url)
            await session.commit()

    async def record_fetch(self, resource: Resource, fetch_result: FetchResult, *, run_id: int) -> None:
        async with self._sessionmaker() as session:
            await repository.insert_fetch_history(session, resource.url, fetch_result, run_id=run_id)
            await repository.update_resource_from_fetch(session, resource, fetch_result)
            await session.commit()

    async def record_edge(self, edge: Edge, *, run_id: int) -> None:
        now = datetime.now(timezone.utc)
        async with self._sessionmaker() as session:
            await repository.upsert_edge(session, edge, run_id=run_id, now=now)
            await session.commit()
