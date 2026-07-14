"""Run the Phase 4 crawler against a real website.

Usage:
    uv run python examples/crawl_demo.py https://example.com/
    uv run python examples/crawl_demo.py https://example.com/ --max-pages 50 --max-depth 3 --delay 1.0

Notes:
    - robots.txt is NOT respected yet (Phase 8). Only crawl sites you own or have
      permission to crawl, and keep `delay` >= 1.0.
    - The default User-Agent is `graph-crawl/0.1`. Some sites 403 unknown bots;
      if you see a lot of `needs_auth`, that is likely why.
    - Non-HTML links (PDFs, images, ...) are recorded as `pending` leaves and
      NOT fetched, so a crawl of an HTML site is cheap.
"""

import asyncio

from graph_crawl.crawler import Crawler
from graph_crawl.fetcher import Fetcher
from graph_crawl.schemas.graph import ResourceState


async def main(
    seed: str, max_pages: int = 50, max_depth: int = 3, delay: float = 1.0, user_agent: str = "graph-crawl/0.1"
) -> None:

    async with Fetcher(user_agent=user_agent) as fetcher:
        crawler = Crawler(
            fetcher,
            delay=delay,
            max_pages=max_pages,
            max_depth=max_depth,
        )
        print(f"Crawling {seed}  (max_pages={max_pages}, max_depth={max_depth}, delay={delay}s)\n")
        try:
            result = await crawler.crawl(seed)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            return

    print(result.summarize())
    print()

    # Group resources by state.
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
    seed = "https://quotes.toscrape.com"
    asyncio.run(main(seed))
