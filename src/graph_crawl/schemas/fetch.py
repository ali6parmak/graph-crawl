from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, Field


class ResourceStatus(StrEnum):
    pending = "pending"
    queued = "queued"
    fetching = "fetching"
    done = "done"
    error = "error"
    dead = "dead"
    backoff = "backoff"  # hit 429 / 503 - will try later
    needs_auth = "needs_auth"


class FetchOutcome(StrEnum):
    success = "success"  # 2xx
    not_modified = "not_modified"  # 304
    redirected = "redirected"  # final 2xx after redirects
    not_found = "not_found"  # 404
    gone = "gone"  # 410
    forbidden = "forbidden"  # 401 / 403
    rate_limited = "rate_limited"  # 429
    server_error = "server_error"  # 5xx
    client_error = "client_error"  # other 4xx
    network_error = "network_error"  # timeout, DNS, TLS, connection reset
    redirect_loop = "redirect_loop"


class RedirectHop(BaseModel):
    """One hop in a redirect chain. The original request is hop 0."""

    url: str
    status_code: int
    location: str | None = None


class FetchResult(BaseModel):
    """Everything the HTTP layer returns about one fetch attempt."""

    requested_url: str  # the normalized url we asked for
    final_url: str | None = None  # response.url after all redirects (None if request fails)

    outcome: FetchOutcome
    status_code: int | None = None
    status: ResourceStatus

    started_at: datetime
    finished_at: datetime
    duration_ms: int

    content_type: str | None = None
    content_length: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    retry_after: float | None = None  # seconds, for 429/503

    # body only present on 2xx with a body, None for 304, HEAD, errors
    body: bytes | None = None
    content_hash: str | None = None  # sha256(body) hex

    redirect_chain: list[RedirectHop] = Field(default_factory=list)

    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.outcome in {FetchOutcome.success, FetchOutcome.redirected}
