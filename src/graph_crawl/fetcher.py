import hashlib
import httpx
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from graph_crawl.schemas.fetch import FetchOutcome, FetchResult, RedirectHop, ResourceStatus

DEFAULT_USER_AGENT: str = "graph-crawl/0.1"
DEFAULT_TIMEOUT: httpx.Timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
DEFAULT_LIMITS: httpx.Limits = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=30.0)
DEFAULT_MAX_REDIRECTS: int = 10


def _parse_retry_after(value: str | None) -> float | None:
    """
    Retry-After can be either:
        - delta-seconds (e.g. "120")
        - HTTP-date (e.g. "Wed, 21 Oct 2026 07:28:00 GMT")
    Returns seconds as a non-negative float, or None if unparseable.
    """
    if not value:
        return None
    value = value.strip()

    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())


# The order of these are important & intentional
def _classify_status(status_code: int, had_redirects: bool) -> tuple[FetchOutcome, ResourceStatus]:
    if status_code == 304:
        return FetchOutcome.not_modified, ResourceStatus.done
    if 200 <= status_code < 300:
        outcome = FetchOutcome.redirected if had_redirects else FetchOutcome.success
        return outcome, ResourceStatus.done
    if status_code == 429:
        return FetchOutcome.rate_limited, ResourceStatus.backoff
    if status_code == 503:
        # 503 is often accompanied by Retry-After, treat as transient backoff
        return FetchOutcome.server_error, ResourceStatus.backoff
    if 500 <= status_code < 600:
        return FetchOutcome.server_error, ResourceStatus.error
    if status_code == 410:
        return FetchOutcome.gone, ResourceStatus.dead
    if status_code == 404:
        return FetchOutcome.not_found, ResourceStatus.dead
    if status_code in {401, 403}:
        return FetchOutcome.forbidden, ResourceStatus.needs_auth
    if 400 <= status_code < 500:
        return FetchOutcome.client_error, ResourceStatus.error
    return FetchOutcome.client_error, ResourceStatus.error


def _build_redirect_chain(history: list[httpx.Response]) -> list[RedirectHop]:
    """
    Convert httpx's redirect history into our RedirectHop list.

    `history` contains the 3xx responses, in order (empty if no redirects).
    Each hop records the URL that was requested, the 3xx status returned,
    and the Location header that pointed to the next URL.

    The chain contains ONLY redirect hops. The starting URL is in
    FetchResult.requested_url; the terminating URL is in FetchResult.final_url.
    An empty chain means "no redirects happened".
    """
    return [
        RedirectHop(
            url=str(r.request.url),
            status_code=r.status_code,
            location=r.headers.get("location"),
        )
        for r in history
    ]


class Fetcher:
    """
    Async HTTP fetcher wrapping an httpx.AsyncClient with project defaults.

    One Fetcher per crawl run. Reuses TCP/TLS connections across requests via
    httpx's connection pool. Use as an async context manager:

        async with Fetcher() as fetcher:
            result = await fetcher.fetch("https://example.org/page")

    fetch() never raises, network/protocol errors surface as a FetchResult
    with outcome=network_error or redirect_loop.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        limits: httpx.Limits = DEFAULT_LIMITS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        follow_redirects: bool = True,
        accept: str = "text/html,application/xhtml+xml,*/*;q=0.8",
        accept_encoding: str = "gzip, br, zstd",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_redirects: int = max_redirects
        self.follow_redirects: bool = follow_redirects
        self._default_headers: dict[str, str] = {
            "User-Agent": user_agent,
            "Accept": accept,
            "Accept-Encoding": accept_encoding,
        }
        # Caller may pass a pre-configured client (e.g. a mocked transport in tests).
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            headers=self._default_headers,
        )
        self._owns_client: bool = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        etag: str | None = None,
        last_modified: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> FetchResult:
        """
        Fetch a URL and return a FetchResult. Never raises.

        Args:
            url: normalized URL to fetch.
            method: HTTP method (typically "GET" or "HEAD").
            etag: if set, sends If-None-Match for a conditional GET.
            last_modified: if set, sends If-Modified-Since for a conditional GET.
            extra_headers: additional request headers (overrides defaults).

        Returns:
            FetchResult. Network and protocol errors are encoded in the result,
            not raised.
        """
        started_at: datetime = datetime.now(timezone.utc)
        start_perf: float = time.perf_counter()

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        if extra_headers:
            headers.update(extra_headers)

        try:
            response = await self._client.request(method, url, headers=headers)
        except httpx.TooManyRedirects as exc:
            return self._error_result(
                url,
                started_at,
                start_perf,
                outcome=FetchOutcome.redirect_loop,
                status=ResourceStatus.error,
                error=f"exceeded {self.max_redirects} redirects: {exc}",
            )
        except httpx.TimeoutException as exc:
            return self._error_result(
                url,
                started_at,
                start_perf,
                outcome=FetchOutcome.network_error,
                status=ResourceStatus.error,
                error=f"timeout: {exc}",
            )
        except httpx.ConnectError as exc:
            return self._error_result(
                url,
                started_at,
                start_perf,
                outcome=FetchOutcome.network_error,
                status=ResourceStatus.error,
                error=f"http error: {exc}",
            )
        except httpx.HTTPError as exc:
            return self._error_result(
                url,
                started_at,
                start_perf,
                outcome=FetchOutcome.network_error,
                status=ResourceStatus.error,
                error=f"http error: {exc}",
            )

        finished_at: datetime = datetime.now(timezone.utc)
        duration_ms: int = int((time.perf_counter() - start_perf) * 1000)

        had_redirects: bool = bool(response.history)
        outcome, status = _classify_status(response.status_code, had_redirects)
        redirect_chain: list[RedirectHop] = _build_redirect_chain(response.history)

        body: bytes | None = None
        content_hash: str | None = None
        content_length: int | None = None

        if method.upper() == "HEAD":
            # No body on HEAD, even on 2xx. Don't hash empty bytes.
            pass
        elif outcome in {FetchOutcome.success, FetchOutcome.redirected}:
            body = response.content
            content_length = len(body)
            content_hash = hashlib.sha256(body).hexdigest()
        elif outcome == FetchOutcome.not_modified:
            # 304: no body, content_length=0 signals "we got a response but no bytes".
            content_length = 0

        # Retry-After only meaningful on rate-limit / transient-server responses.
        retry_after: float | None = None
        if outcome in {FetchOutcome.rate_limited, FetchOutcome.server_error}:
            retry_after = _parse_retry_after(response.headers.get("retry-after"))

        return FetchResult(
            requested_url=url,
            final_url=str(response.url),
            outcome=outcome,
            status_code=response.status_code,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            content_type=response.headers.get("content-type"),
            content_length=content_length,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            retry_after=retry_after,
            body=body,
            content_hash=content_hash,
            redirect_chain=redirect_chain,
            error=None,
        )

    def _error_result(
        self,
        url: str,
        started_at: datetime,
        start_perf: float,
        *,
        outcome: FetchOutcome,
        status: ResourceStatus,
        error: str,
    ) -> FetchResult:
        finished_at: datetime = datetime.now(timezone.utc)
        duration_ms: int = int((time.perf_counter() - start_perf) * 1000)
        return FetchResult(
            requested_url=url,
            final_url=None,
            outcome=outcome,
            status_code=None,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error=error,
        )
