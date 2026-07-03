from enum import StrEnum
from pydantic import BaseModel, Field


class ParseOutcome(StrEnum):
    ok = "ok"
    empty = "empty"  # input was empty or space only
    parser_error = "parser_error"


class Anchor(BaseModel):
    """One <a href> element. The href is raw: entity-decoded by the parser, but
    unresolved and unnormalized. Resolution/normalization is the caller's job
    (the discovery pipeline: resolve -> is_crawlable -> normalize)."""

    href: str
    rel: str | None = None  # lowercased, whitespace-normalized, None if absent
    text: str = ""  # collapsed inner text, for context/debugging only


class CanonicalLink(BaseModel):
    """A <link rel="canonical" href="...">. href is raw; canonicalize() resolves it
    against effective_base_url later. None (on ParsedDocument) means absent."""

    href: str


class BaseElement(BaseModel):
    """A <base href="..."> element. Target is recorded but does not affect URL
    resolution; it only sets the default window target for clickable links."""

    href: str
    target: str | None = None


class MetaRefresh(BaseModel):
    """A meta http-equiv="refresh" content="N; url=..."> directive. target_url is
    None for a pure same-URL refresh (no url = part). target_url is raw/unresolved."""

    delay_seconds: float
    target_url: str | None = None


class MetaRobots(BaseModel):
    """Aggregated <meta name="robots"> directives across the document. The three
    booleans are what the crawl-policy layer branches on; raw preserves the
    verbatim content(s) for debugging and any directive we don't yet model.
    'none' is treated as noindex+nofollow (per Google's docs)."""

    raw: str = ""
    noindex: bool = False
    nofollow: bool = False
    noarchive: bool = False


class ParsedDocument(BaseModel):
    """Everything parse_html() returns about one HTML document. Plain data only:
    no DOM handle is exposed, so callers never need to import the parser backend."""

    outcome: ParseOutcome
    effective_base_url: str | None = None  # doc URL, possibly overridden by <base>

    anchors: list[Anchor] = Field(default_factory=list)
    canonical: CanonicalLink | None = None
    base: BaseElement | None = None
    meta_refresh: MetaRefresh | None = None
    meta_robots: MetaRobots = Field(default_factory=lambda: MetaRobots())

    @property
    def is_ok(self) -> bool:
        return self.outcome is ParseOutcome.ok
