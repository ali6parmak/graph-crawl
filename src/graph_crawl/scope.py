from typing import Protocol
from urllib.parse import urlsplit


class Scope(Protocol):
    """A crawl-scope policy. Given a normalized URL and the normalized seed,
    return True if the URL is in scope to be crawled (fetched and parsed).

    Scope controls what we CRAWL, not what we RECORD: out-of-scope URLs are
    still stored as resources (state=skipped) with their edges preserved, so the
    graph keeps the site's outgoing links. The scope only stops us from
    recursively fetching into other sites."""

    def __call__(self, url: str, seed: str) -> bool: ...


def host_scope(url: str, seed: str) -> bool:
    """Exact-host scope: crawl only URLs whose host equals the seed's host.

    The most conservative reading of 'one domain'. Subdomains are NOT crawled
    (seed ``example.org`` will not crawl ``blog.example.org``).

    This is the Phase 4 default. A later phase will add eTLD+1 scope (via the
    Public Suffix List) so subdomains of the same registrable domain are
    included — important for sites that spread content across subdomains
    (UN, courts, governments frequently do this).
    """
    try:
        url_host = urlsplit(url).hostname
        seed_host = urlsplit(seed).hostname
    except ValueError:
        return False
    if url_host is None or seed_host is None:
        return False
    return url_host.lower() == seed_host.lower()
