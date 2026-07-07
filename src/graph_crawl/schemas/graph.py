from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, Field
from graph_crawl.schemas.fetch import FetchOutcome


class ResourceType(StrEnum):
    html = "html"
    pdf = "pdf"
    doc = "doc"
    image = "image"
    video = "video"
    audio = "audio"
    archive = "archive"
    other = "other"
    unknown = "unknown"  # discovered but not yet classified (e.g. extensionless URL)


class ResourceState(StrEnum):
    pending = "pending"  # discovered, not yet fetched
    fetched = "fetched"  # 2xx HTML response, parsed for links
    fetched_leaf = "fetched_leaf"  # 2xx non-HTML response, not parsed
    not_found = "not_found"  # 404 / 410
    error = "error"  # 4xx (except 404/410), 5xx without retry, network error
    backoff = "backoff"  # 429 / 503-with-Retry-After; not retried in
    skipped = "skipped"  # out of scope, or robots-disallowed


class Resource(BaseModel):
    """One discovered URL and its current state. `url` is the normalized
    primary key. This shape is intentionally close to the future resources table
    so persisting it is mostly a write-through."""

    url: str
    resource_state: ResourceState
    resource_type: ResourceType = ResourceType.unknown
    status_code: int | None = None
    content_type: str | None = None
    depth: int  # link distance from the seed (BFS)
    discovered_at: datetime
    fetched_at: datetime | None = None
    outcome: FetchOutcome | None = None  # from FetchResult, when fetched


class Edge(BaseModel):
    """A hyperlink: page `source` contains a link to resource `target`. Both
    endpoints are normalized URLs. `rel` preserves the anchor's rel attribute
    for provenance (e.g. 'nofollow')."""

    source: str
    target: str
    rel: str | None = None


class CrawlStats(BaseModel):
    fetched: int = 0
    fetched_leaf: int = 0
    not_found: int = 0
    error: int = 0
    backoff: int = 0
    skipped: int = 0
    discovered: int = 0  # total unique URLs ever recorded as resources
    max_frontier_size: int = 0
    stopped_reason: str | None = None


class CrawlResult(BaseModel):
    """The output of one crawl run: the in-memory graph.
    `resources` is keyed by normalized URL for O(1) lookup/dedup."""

    seed: str
    started_at: datetime
    finished_at: datetime
    resources: dict[str, Resource] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)
    stats: CrawlStats = Field(default_factory=CrawlStats)

    def summarize(self) -> str:
        s = self.stats
        return "\n".join(
            [
                f"Seed: {self.seed}",
                f"Stopped: {s.stopped_reason}",
                f"Discovered: {s.discovered} unique URLs",
                f"  fetched (HTML):      {s.fetched}",
                f"  fetched leaf (non-HTML): {s.fetched_leaf}",
                f"  not found / gone:    {s.not_found}",
                f"  errors:              {s.error}",
                f"  backoff (429/503):   {s.backoff}",
                f"  skipped (out of scope): {s.skipped}",
                f"  edges:               {len(self.edges)}",
                f"  max frontier size:   {s.max_frontier_size}",
            ]
        )
