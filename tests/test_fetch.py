import httpx
import respx
from datetime import datetime, timedelta, timezone
from graph_crawl.schemas.fetch import FetchOutcome, ResourceStatus
from graph_crawl.fetcher import Fetcher


@respx.mock
async def test_success_2xx_returns_body_hash_and_headers():
    respx.get("https://example.org/page").mock(
        return_value=httpx.Response(
            200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "ETag": '"abc123"',
                "Last-Modified": "Mon, 23 Jun 2026 09:11:00 GMT",
            },
            content=b"<html>hello</html>",
        )
    )
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/page")

    assert result.outcome is FetchOutcome.success
    assert result.status is ResourceStatus.done
    assert result.status_code == 200
    assert result.body == b"<html>hello</html>"
    assert result.content_hash and len(result.content_hash) == 64
    assert result.content_type == "text/html; charset=utf-8"
    assert result.etag == '"abc123"'
    assert result.last_modified == "Mon, 23 Jun 2026 09:11:00 GMT"
    assert result.final_url == "https://example.org/page"
    assert result.redirect_chain == []
    assert result.error is None
    assert result.is_success is True


@respx.mock
async def test_conditional_get_returns_not_modified():
    respx.get("https://example.org/page").mock(return_value=httpx.Response(304))
    async with Fetcher() as f:
        result = await f.fetch(
            "https://example.org/page",
            etag='"abc123"',
            last_modified="Mon, 23 Jun 2026 09:11:00 GMT",
        )

    assert result.outcome is FetchOutcome.not_modified
    assert result.status is ResourceStatus.done
    assert result.status_code == 304
    assert result.body is None
    assert result.content_hash is None
    assert result.content_length == 0
    assert result.is_success is False  # 304 is "successful" but not in our success set


@respx.mock
async def test_conditional_headers_actually_sent():
    route = respx.get("https://example.org/page").mock(return_value=httpx.Response(304))
    async with Fetcher() as f:
        await f.fetch(
            "https://example.org/page",
            etag='"abc123"',
            last_modified="Mon, 23 Jun 2026 09:11:00 GMT",
        )

    sent = route.calls.last.request.headers
    assert sent.get("if-none-match") == '"abc123"'
    assert sent.get("if-modified-since") == "Mon, 23 Jun 2026 09:11:00 GMT"


@respx.mock
async def test_redirect_chain_is_captured():
    respx.get("https://example.org/old").mock(
        return_value=httpx.Response(
            301,
            headers={"Location": "https://example.org/new"},
        )
    )
    respx.get("https://example.org/new").mock(return_value=httpx.Response(200, content=b"<html>new</html>"))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/old")

    assert result.outcome is FetchOutcome.redirected
    assert result.status_code == 200
    assert result.final_url == "https://example.org/new"
    assert len(result.redirect_chain) == 1  # only the redirect hop, not the final

    hop0 = result.redirect_chain[0]
    assert hop0.url == "https://example.org/old"
    assert hop0.status_code == 301
    assert hop0.location == "https://example.org/new"

    assert result.body == b"<html>new</html>"
    assert result.is_success is True


@respx.mock
async def test_not_found_404_marks_dead():
    respx.get("https://example.org/missing").mock(return_value=httpx.Response(404, content=b"not found"))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/missing")

    assert result.outcome is FetchOutcome.not_found
    assert result.status is ResourceStatus.dead
    assert result.body is None  # 4xx bodies are not kept
    assert result.is_success is False


@respx.mock
async def test_gone_410_marks_dead():
    respx.get("https://example.org/gone").mock(return_value=httpx.Response(410))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/gone")

    assert result.outcome is FetchOutcome.gone
    assert result.status is ResourceStatus.dead


@respx.mock
async def test_forbidden_401_marks_needs_auth():
    respx.get("https://example.org/protected").mock(return_value=httpx.Response(401))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/protected")

    assert result.outcome is FetchOutcome.forbidden
    assert result.status is ResourceStatus.needs_auth


@respx.mock
async def test_forbidden_403_marks_needs_auth():
    respx.get("https://example.org/protected").mock(return_value=httpx.Response(403))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/protected")

    assert result.outcome is FetchOutcome.forbidden
    assert result.status is ResourceStatus.needs_auth


@respx.mock
async def test_rate_limited_with_retry_after_seconds():
    respx.get("https://example.org/x").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "120"},
        )
    )
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/x")

    assert result.outcome is FetchOutcome.rate_limited
    assert result.status is ResourceStatus.backoff
    assert result.retry_after == 120.0


@respx.mock
async def test_rate_limited_with_retry_after_http_date():
    future_iso = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    respx.get("https://example.org/x").mock(return_value=httpx.Response(429, headers={"Retry-After": future_iso}))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/x")

    assert result.outcome is FetchOutcome.rate_limited
    assert result.retry_after is not None
    assert 50.0 <= result.retry_after <= 70.0  # ~60s with slack for test runtime


@respx.mock
async def test_server_error_500_marks_error_no_backoff():
    respx.get("https://example.org/x").mock(return_value=httpx.Response(500, content=b"oops"))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/x")

    assert result.outcome is FetchOutcome.server_error
    assert result.status is ResourceStatus.error
    assert result.retry_after is None


@respx.mock
async def test_server_error_503_marks_backoff_with_retry_after():
    respx.get("https://example.org/x").mock(
        return_value=httpx.Response(
            503,
            headers={"Retry-After": "30"},
        )
    )
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/x")

    assert result.outcome is FetchOutcome.server_error
    assert result.status is ResourceStatus.backoff  # 503 is transient
    assert result.retry_after == 30.0


@respx.mock
async def test_client_error_400_marks_error():
    respx.get("https://example.org/x").mock(return_value=httpx.Response(400, content=b"bad request"))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/x")

    assert result.outcome is FetchOutcome.client_error
    assert result.status is ResourceStatus.error


@respx.mock
async def test_network_error_connect():
    respx.get("https://example.org/x").mock(side_effect=httpx.ConnectError("connection refused"))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/x")

    assert result.outcome is FetchOutcome.network_error
    assert result.status is ResourceStatus.error
    assert result.status_code is None
    assert result.final_url is None
    assert result.error and "connection refused" in result.error
    assert result.is_success is False


@respx.mock
async def test_network_error_timeout():
    respx.get("https://example.org/x").mock(side_effect=httpx.ReadTimeout("read timed out"))
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/x")

    assert result.outcome is FetchOutcome.network_error
    assert result.status is ResourceStatus.error
    assert result.error and "read timed out" in result.error


@respx.mock
async def test_redirect_loop_detected():
    # /a → /b → /a → /b ... httpx raises TooManyRedirects after max_redirects.
    respx.get("https://example.org/a").mock(
        return_value=httpx.Response(302, headers={"Location": "https://example.org/b"})
    )
    respx.get("https://example.org/b").mock(
        return_value=httpx.Response(302, headers={"Location": "https://example.org/a"})
    )
    async with Fetcher(max_redirects=4) as f:
        result = await f.fetch("https://example.org/a")

    assert result.outcome is FetchOutcome.redirect_loop
    assert result.status is ResourceStatus.error
    assert result.error and "redirect" in result.error.lower()


@respx.mock
async def test_custom_user_agent_is_sent():
    route = respx.get("https://example.org/x").mock(return_value=httpx.Response(200, content=b"ok"))
    async with Fetcher(user_agent="custom-crawler/1.0") as f:
        await f.fetch("https://example.org/x")

    assert route.calls.last.request.headers.get("user-agent") == "custom-crawler/1.0"


@respx.mock
async def test_head_request_skips_body_and_hash():
    respx.head("https://example.org/x").mock(
        return_value=httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "ETag": '"pdf-123"',
            },
        )
    )
    async with Fetcher() as f:
        result = await f.fetch("https://example.org/x", method="HEAD")

    assert result.outcome is FetchOutcome.success
    assert result.body is None
    assert result.content_hash is None
    assert result.content_type == "application/pdf"
    assert result.etag == '"pdf-123"'
