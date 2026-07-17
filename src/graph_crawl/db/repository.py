"""Low-level write helpers using SQLAlchemy Core upserts (INSERT ... ON CONFLICT).

These are the hot path of the crawler's write-through. Using Core (not ORM
``session.merge``) gives correct 'preserve discovered_at on conflict' semantics
in one statement and is markedly faster than ORM merge for the crawler's
per-record upsert pattern — the standard Scrapy-at-scale choice."""

from datetime import datetime

from graph_crawl.db.base import Base
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from graph_crawl.db import models
from graph_crawl.schemas.fetch import FetchResult
from graph_crawl.schemas.graph import CrawlStats, Edge as EdgeDTO, Resource as ResourceDTO


async def insert_crawl_run(
    session: AsyncSession,
    *,
    seed_url: str,
    started_at: datetime,
    max_pages: int,
    max_depth: int,
    max_frontier_size: int,
    delay: float,
) -> int:
    stmt = (
        insert(models.CrawlRun)
        .values(
            seed_url=seed_url,
            started_at=started_at,
            max_pages=max_pages,
            max_depth=max_depth,
            max_frontier_size=max_frontier_size,
            delay=delay,
        )
        .returning(models.CrawlRun.id)
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def update_crawl_run(
    session: AsyncSession,
    run_id: int,
    *,
    finished_at: datetime,
    stopped_reason: str | None,
    stats: CrawlStats,
) -> None:
    await session.execute(
        update(models.CrawlRun)
        .where(models.CrawlRun.id == run_id)
        .values(
            finished_at=finished_at,
            stopped_reason=stopped_reason,
            stats_fetched=stats.fetched,
            stats_fetched_leaf=stats.fetched_leaf,
            stats_not_found=stats.not_found,
            stats_gone=stats.gone,
            stats_needs_auth=stats.needs_auth,
            stats_error=stats.error,
            stats_backoff=stats.backoff,
            stats_skipped=stats.skipped,
            stats_discovered=stats.discovered,
            stats_max_frontier_size=stats.max_frontier_size,
        )
    )


async def upsert_resource(session: AsyncSession, resource: ResourceDTO, *, seed_url: str) -> None:
    """Insert a new resource row. On conflict (URL already present) do nothing —
    first discovery wins, matching the in-memory dict semantics where a resource
    is only created once."""
    stmt = (
        insert(models.Resource)
        .values(
            url=resource.url,
            resource_state=resource.resource_state.value,
            resource_type=resource.resource_type.value,
            depth=resource.depth,
            seed_url=seed_url,
            discovered_at=resource.discovered_at,
        )
        .on_conflict_do_nothing(index_elements=["url"])
    )
    await session.execute(stmt)


async def insert_fetch_history(session: AsyncSession, url: str, fetch_result: FetchResult, *, run_id: int) -> None:
    redirect_chain = [hop.model_dump() for hop in fetch_result.redirect_chain] if fetch_result.redirect_chain else None
    await session.execute(
        insert(models.FetchHistory).values(
            url=url,
            crawl_run_id=run_id or None,
            attempted_at=fetch_result.started_at,
            finished_at=fetch_result.finished_at,
            duration_ms=fetch_result.duration_ms,
            outcome=fetch_result.outcome.value,
            status_code=fetch_result.status_code,
            content_type=fetch_result.content_type,
            content_length=fetch_result.content_length,
            content_hash=fetch_result.content_hash,
            final_url=fetch_result.final_url,
            etag=fetch_result.etag,
            last_modified=fetch_result.last_modified,
            retry_after=fetch_result.retry_after,
            redirect_chain=redirect_chain,
            error=fetch_result.error,
        )
    )


async def update_resource_from_fetch(session: AsyncSession, resource: ResourceDTO, fetch_result: FetchResult) -> None:
    """Overwrite the resource row's 'current snapshot' columns with the latest
    fetch. The lifecycle state (fetched/not_found/gone/needs_auth/...) is the
    crawler's derived decision (``resource.resource_state``); the raw HTTP detail
    (status_code, content_hash, etag, ...) comes from ``fetch_result``."""
    await session.execute(
        update(models.Resource)
        .where(models.Resource.url == resource.url)
        .values(
            resource_state=resource.resource_state.value,
            resource_type=resource.resource_type.value,
            status_code=fetch_result.status_code,
            content_type=fetch_result.content_type,
            content_length=fetch_result.content_length,
            content_hash=fetch_result.content_hash,
            final_url=fetch_result.final_url,
            fetched_at=resource.fetched_at,
            last_fetch_outcome=fetch_result.outcome.value,
            etag=fetch_result.etag,
            last_modified=fetch_result.last_modified,
            retry_after=fetch_result.retry_after,
            error=fetch_result.error,
        )
    )


async def upsert_edge(session: AsyncSession, edge: EdgeDTO, *, run_id: int, now: datetime) -> None:
    """Insert a new edge; on conflict (source,target) update last_seen + rel.
    Both endpoints must already exist in ``resources`` (the crawler records the
    target resource before the edge — see ``Crawler._discover_links``)."""
    stmt = (
        insert(models.Edge)
        .values(
            source_url=edge.source,
            target_url=edge.target,
            rel=edge.rel,
            discovered_at=now,
            last_seen_at=now,
            discovery_run_id=run_id or None,
            last_seen_run_id=run_id or None,
        )
        .on_conflict_do_update(
            index_elements=["source_url", "target_url"],
            set_={
                "last_seen_at": now,
                "last_seen_run_id": run_id or None,
                "rel": edge.rel,
            },
        )
    )
    await session.execute(stmt)


async def count_rows(session: AsyncSession, model: type[Base]) -> int:
    """Convenience for integration tests."""
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)
