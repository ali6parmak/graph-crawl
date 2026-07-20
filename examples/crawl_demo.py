"""Run the crawler against a real website, optionally writing through to Postgres.

Usage:
    uv run python examples/crawl_demo.py https://example.com/
    uv run python examples/crawl_demo.py https://example.com/ --max-pages 50 --max-depth 3 --delay 1.0

Set GRAPH_CRAWL_DB_DSN to persist the crawl graph to Postgres, e.g.
    export GRAPH_CRAWL_DB_DSN="postgresql+asyncpg://user:pass@localhost/graphcrawl"
Without it, the demo runs with NullSink (Phase 4 behavior, in-memory only).

Notes:
    - robots.txt is NOT respected yet (Phase 8). Only crawl sites you own or have
      permission to crawl, and keep `delay` >= 1.0.
    - For dev convenience the demo calls init_schema() (CREATE TABLE IF NOT EXISTS).
      Production should use Alembic migrations instead.
"""

import argparse
import asyncio
import os

from graph_crawl.crawler import Crawler
from graph_crawl.fetcher import Fetcher
from graph_crawl.schemas.graph import ResourceState
from graph_crawl.sink import NullSink


async def main(
    seed: str, max_pages: int = 50, max_depth: int = 3, delay: float = 1.0, user_agent: str = "graph-crawl/0.1"
) -> None:
    sink = NullSink()
    engine = None
    dsn = os.environ.get("GRAPH_CRAWL_DB_DSN")
    if dsn:
        from sqlalchemy.ext.asyncio import create_async_engine

        from graph_crawl.db.engine import init_schema
        from graph_crawl.db.sink import DbCrawlSink

        engine = create_async_engine(dsn, future=True)
        await init_schema(engine)  # dev convenience; production uses Alembic
        sink = DbCrawlSink(engine)
        print(f"Persisting to {dsn}")
    else:
        print("GRAPH_CRAWL_DB_DSN unset — running in-memory only (NullSink)")

    try:
        async with Fetcher(user_agent=user_agent) as fetcher:
            crawler = Crawler(
                fetcher,
                delay=delay,
                max_pages=max_pages,
                max_depth=max_depth,
                sink=sink,
            )
            print(f"Crawling {seed}  (max_pages={max_pages}, max_depth={max_depth}, delay={delay}s)\n")
            try:
                result = await crawler.crawl(seed)
            except KeyboardInterrupt:
                print("\nInterrupted.")
                return
    finally:
        if engine is not None:
            await engine.dispose()

    print(result.summarize())
    print()

    by_state: dict[ResourceState, list[str]] = {}
    for res in result.resources.values():
        by_state.setdefault(res.resource_state, []).append(res.url)

    for state in ResourceState:
        urls = by_state.get(state, [])
        if not urls:
            continue
        print(f"--- {state.value} ({len(urls)}) ---")
        for url in urls[:20]:
            print(f"  {url}")
        if len(urls) > 20:
            print(f"  ... and {len(urls) - 20} more")
        print()

    print(f"--- edges ({len(result.edges)}) ---")
    for edge in result.edges[:20]:
        rel = f" [{edge.rel}]" if edge.rel else ""
        print(f"  {edge.source}  ->  {edge.target}{rel}")
    if len(result.edges) > 20:
        print(f"  ... and {len(result.edges) - 20} more")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("seed", nargs="?", default="https://quotes.toscrape.com")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()
    asyncio.run(main(args.seed, max_pages=args.max_pages, max_depth=args.max_depth, delay=args.delay))
