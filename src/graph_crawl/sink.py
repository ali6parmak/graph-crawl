"""The crawler↔persistence boundary.

``Crawler`` depends on the ``CrawlSink`` Protocol only — it never imports
SQLAlchemy. The default ``NullSink`` does nothing, preserving Phase 4 behavior
when no database is configured. The DB-backed implementation lives in
``graph_crawl.db.sink``.

This mirrors how selectolax is confined behind ``graph_crawl.parser``: the sink
is the single seam where the crawl loop meets durable storage, and a future
Redis/NoSQL sink is a drop-in replacement at that one file."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from graph_crawl.schemas.fetch import FetchResult
from graph_crawl.schemas.graph import CrawlResult, Edge, Resource


@runtime_checkable
class CrawlSink(Protocol):
    """Write-through target for one crawl run.

    Methods are async and best-effort: a sink failure surfaces as a crawl-level
    error (the run aborts), not as a per-URL ``resource_state``. The crawler
    calls these after each state change so the database is the source of truth
    for the artifact graph while the in-memory ``Frontier`` remains the fast
    working queue.
    """

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
        """Record the start of a crawl run. Returns the new crawl_run id (0 for NullSink)."""
        ...

    async def finish_run(self, run_id: int, result: CrawlResult) -> None:
        """Record the end of a crawl run: finished_at, stopped_reason, denormalized stats."""
        ...

    async def record_resource(self, resource: Resource, *, run_id: int, seed_url: str) -> None:
        """Upsert a resource row. Called at discovery time (state=pending or skipped)."""
        ...

    async def record_fetch(self, resource: Resource, fetch_result: FetchResult, *, run_id: int) -> None:
        """Insert a fetch_history row and update the resources row with the latest fetch snapshot."""
        ...

    async def record_edge(self, edge: Edge, *, run_id: int) -> None:
        """Upsert an edge. First insert sets discovered_at + discovery_run_id;
        re-discovery updates last_seen_at + last_seen_run_id."""
        ...


class NullSink:
    """Default sink. No-op. Phase 4 behavior preserved when no DB is configured."""

    async def start_run(
        self, seed: str, started_at: datetime, *, max_pages: int, max_depth: int, max_frontier_size: int, delay: float
    ) -> int:
        return 0

    async def finish_run(self, run_id: int, result: CrawlResult) -> None:
        pass

    async def record_resource(self, resource: Resource, *, run_id: int, seed_url: str) -> None:
        pass

    async def record_fetch(self, resource: Resource, fetch_result: FetchResult, *, run_id: int) -> None:
        pass

    async def record_edge(self, edge: Edge, *, run_id: int) -> None:
        pass
