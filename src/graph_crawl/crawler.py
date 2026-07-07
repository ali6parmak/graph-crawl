import asyncio
import re
import time
from datetime import datetime, timezone

from graph_crawl.classify import is_html_content_type, is_html_url, resource_type_from_content_type, url_resource_type
from graph_crawl.fetcher import Fetcher
from graph_crawl.frontier import Frontier
from graph_crawl.normalize import normalize
from graph_crawl.parser import parse_html
from graph_crawl.schemas.fetch import FetchOutcome, FetchResult
from graph_crawl.schemas.graph import CrawlResult, CrawlStats, Edge, Resource, ResourceState, ResourceType
from graph_crawl.schemas.parse import ParsedDocument
from graph_crawl.scope import Scope, host_scope
from graph_crawl.urls import UnresolvableReference, is_crawlable, resolve

_CHARSET_RE = re.compile(r"charset=([\w\-]+)", re.IGNORECASE)


class Crawler:
    """Single-domain, breadth-first, sequential async crawler.

    One ``Crawler`` per crawl run. Reuses a ``Fetcher`` (and its connection
    pool). The ``Fetcher`` is owned by the caller; ``Crawler`` does not close
    it.
    """

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        scope: Scope = host_scope,
        delay: float = 1.0,
        max_pages: int = 100,
        max_depth: int = 5,
        max_frontier_size: int = 10000,
    ) -> None:
        self._fetcher: Fetcher = fetcher
        self._scope: Scope = scope
        self._delay: float = delay
        self._max_pages: int = max_pages
        self._max_depth: int = max_depth
        self._max_frontier_size: int = max_frontier_size

    async def crawl(self, seed: str) -> CrawlResult:
        """Crawl a single seed URL and return the discovered graph.

        Never raises on per-URL failures (they become ``Resource.state``).
        Raises only on a malformed seed that cannot be normalized.
        """
        started_at: datetime = datetime.now(timezone.utc)
        seed_norm: str = normalize(seed)

        resources: dict[str, Resource] = {
            seed_norm: Resource(
                url=seed_norm,
                resource_state=ResourceState.pending,
                resource_type=url_resource_type(seed_norm),
                depth=0,
                discovered_at=started_at,
            )
        }
        edges: list[Edge] = []
        edges_seen: set[tuple[str, str]] = set()
        frontier: Frontier = Frontier()
        frontier.push(seed_norm, 0)

        frontier_max: int = len(frontier)
        fetched_count: int = 0
        stopped_reason: str | None = None
        last_fetch_perf: float | None = None
        extra_delay: float = 0.0

        while True:
            frontier_item = frontier.pop()
            if frontier_item is None:
                stopped_reason = "frontier exhausted"
                break
            if fetched_count >= self._max_pages:
                stopped_reason = "max_pages"
                break

            # robots.txt hook (Phase 8 implements the real check)
            if not self._is_allowed_by_robots(frontier_item.url):
                resources[frontier_item.url].resource_state = ResourceState.skipped
                continue

            # Politeness: wait between fetches, honoring any Retry-After from a
            # previous 429/503. Single-flight, so one global backoff applies.
            if last_fetch_perf is not None:
                elapsed = time.perf_counter() - last_fetch_perf
                want = self._delay + extra_delay
                if elapsed < want:
                    await asyncio.sleep(want - elapsed)
                extra_delay = 0.0

            fetch_result = await self._fetcher.fetch(frontier_item.url)
            last_fetch_perf = time.perf_counter()
            if fetch_result.retry_after is not None:
                extra_delay = max(extra_delay, fetch_result.retry_after)

            resource: Resource = resources[frontier_item.url]
            resource.fetched_at = datetime.now(timezone.utc)
            resource.status_code = fetch_result.status_code
            resource.content_type = fetch_result.content_type
            resource.outcome = fetch_result.outcome

            html_content_type: bool = is_html_content_type(fetch_result.content_type)
            resource.resource_state = _resource_state_from_fetch_result(fetch_result, html_content_type)

            resource_type = resource_type_from_content_type(fetch_result.content_type)
            if resource_type is not None:
                resource.resource_type = resource_type

            if resource.resource_state in {ResourceState.fetched, ResourceState.fetched_leaf}:
                fetched_count += 1

            # Parse and discover only on a real HTML fetch with a body present.
            if resource.resource_state is ResourceState.fetched and fetch_result.body:
                resource.resource_type = ResourceType.html
                text: str = _decode_html(fetch_result.body, fetch_result.content_type)
                base_for_parse = fetch_result.final_url or frontier_item.url
                parsed_document: ParsedDocument = parse_html(text, base_url=base_for_parse)
                if parsed_document.is_ok:
                    self._discover_links(
                        parsed_document,
                        source_url=frontier_item.url,
                        source_depth=frontier_item.depth,
                        seed=seed_norm,
                        resources=resources,
                        edges=edges,
                        edges_seen=edges_seen,
                        frontier=frontier,
                    )
            if len(frontier) > frontier_max:
                frontier_max = len(frontier)
            if len(frontier) > self._max_frontier_size:
                stopped_reason = "max_frontier_size"
                break

        finished_at: datetime = datetime.now(timezone.utc)
        stats: CrawlStats = _build_stats(resources, frontier_max, stopped_reason)
        return CrawlResult(
            seed=seed_norm,
            started_at=started_at,
            finished_at=finished_at,
            resources=resources,
            edges=edges,
            stats=stats,
        )

    def _discover_links(
        self,
        parsed_document: ParsedDocument,
        *,
        source_url: str,
        source_depth: int,
        seed: str,
        resources: dict[str, Resource],
        edges: list[Edge],
        edges_seen: set[tuple[str, str]],
        frontier: Frontier,
    ):
        """Resolve, filter, normalize and record every anchor in a parsed page.

        For each new in-scope, within-depth, potentially-HTML URL, enqueue it.
        Out-of-scope and known-non-HTML URLs are recorded as resources/edges
        but not enqueued.
        """
        child_depth: int = source_depth + 1
        time_now: datetime = datetime.now(timezone.utc)
        base_url: str = parsed_document.effective_base_url or source_url

        for anchor in parsed_document.anchors:
            try:
                absolute_url: str = resolve(base_url, anchor.href)
            except UnresolvableReference:
                continue
            if not is_crawlable(absolute_url):
                continue
            normalized_url: str = normalize(absolute_url)

            edge_key = (source_url, normalized_url)
            if edge_key not in edges_seen:
                edges_seen.add(edge_key)
                edges.append(Edge(source=source_url, target=normalized_url, rel=anchor.rel))

            if normalized_url in resources:
                continue

            resource_type: ResourceType = url_resource_type(normalized_url)
            if not self._scope(normalized_url, seed):
                resource_state = ResourceState.skipped
            else:
                resource_state = ResourceState.pending
                if child_depth <= self._max_depth and is_html_url(normalized_url):
                    frontier.push(normalized_url, child_depth)

            resources[normalized_url] = Resource(
                url=normalized_url,
                resource_state=resource_state,
                resource_type=resource_type,
                depth=child_depth,
                discovered_at=time_now,
            )

    def _is_allowed_by_robots(self, url: str) -> bool:
        return True


def _resource_state_from_fetch_result(fetch_result: FetchResult, html_content_type: bool) -> ResourceState:
    if fetch_result.outcome in {FetchOutcome.success, FetchOutcome.redirected}:
        return ResourceState.fetched if html_content_type else ResourceState.fetched_leaf
    if fetch_result.outcome is FetchOutcome.not_modified:
        return ResourceState.fetched  # no body to parse; counted as fetched
    if fetch_result.outcome in {FetchOutcome.not_found, FetchOutcome.gone}:
        return ResourceState.not_found
    if fetch_result.outcome is FetchOutcome.rate_limited:
        return ResourceState.backoff
    if fetch_result.outcome is FetchOutcome.server_error:
        return ResourceState.backoff if fetch_result.retry_after is not None else ResourceState.error
    return ResourceState.error  # forbidden, client_error, network_error, redirect loop


def _build_stats(resources: dict[str, Resource], frontier_max: int, stopped_reason: str | None) -> CrawlStats:
    stats: CrawlStats = CrawlStats(max_frontier_size=frontier_max, stopped_reason=stopped_reason)
    for resource in resources.values():
        stats.discovered += 1
        if resource.resource_state is ResourceState.fetched:
            stats.fetched += 1
        elif resource.resource_state is ResourceState.fetched_leaf:
            stats.fetched_leaf += 1
        elif resource.resource_state is ResourceState.not_found:
            stats.not_found += 1
        elif resource.resource_state is ResourceState.error:
            stats.error += 1
        elif resource.resource_state is ResourceState.backoff:
            stats.backoff += 1
        elif resource.resource_state is ResourceState.skipped:
            stats.skipped += 1
    return stats


def _decode_html(body: bytes, content_type: str | None) -> str:
    """Minimal byte->str decode for Phase 4.

    Proper charset handling (HTTP Content-Type charset -> <meta charset> ->
    default per the Encoding Standard) is a later phase; this is enough to make
    the loop run. latin-1 is the final fallback because it never raises.
    """
    charset = None
    if content_type:
        m = _CHARSET_RE.search(content_type)
        if m:
            charset = m.group(1)
    for enc in (charset, "utf-8"):
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("latin-1")
