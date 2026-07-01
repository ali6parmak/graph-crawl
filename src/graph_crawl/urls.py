from urllib.parse import SplitResult, urljoin, urlsplit

CRAWLABLE_SCHEMES: frozenset[str] = frozenset({"http", "https"})


class UnresolvableReference(ValueError):
    """Raised when a URL reference cannot be resolved against a base."""


def resolve(base: str, ref: str) -> str:
    """
    Resolve a possibly-relative URL reference against an absolute base URL.

      Implements RFC 3986 §5.3 (merge) + §5.2.2 (transform references) by
      wrapping urllib.parse.urljoin with project-level guarantees:

        - base MUST be absolute (raises UnresolvableReference otherwise)
        - raises UnresolvableReference on malformed input rather than
          silently returning junk
        - does NOT normalize the result — call normalize() on the output
          if you want a canonical key
        - does NOT strip fragments — normalize() does that
        - preserves all five reference categories from RFC 3986 §4.2,
          including non-http(s) schemes (mailto:, javascript:, ...) which
          pass through unchanged and are filtered out separately via
          is_crawlable()

      Args:
          base: absolute base URL (must have scheme and authority).
          ref: a URL reference, possibly relative (may be empty string).

      Returns:
          An absolute URL string. If ref is already absolute, returns ref
          unchanged (including non-http(s) schemes).

      Raises:
          UnresolvableReference: if base is not absolute, or if urljoin
          cannot resolve ref against base.
    """
    if not base:
        raise UnresolvableReference("base URL is required")
    if ref is None:
        raise UnresolvableReference("ref is None")
    base_parts: SplitResult = urlsplit(base)
    if not base_parts.scheme or not base_parts.netloc:
        raise UnresolvableReference(f"base URL must be absolute: {base!r}")
    try:
        return urljoin(base, ref)
    except (ValueError, TypeError) as exc:
        raise UnresolvableReference(f"could not resolve {ref!r} against {base!r}: {exc}") from exc


def is_crawlable(url: str) -> bool:
    """
    Return True if the URL uses an http(s) scheme.

    Use this after resolve() to filter out mailto:, javascript:, data:,
    tel:, and any other non-crawlable schemes. Resolving them was correct
    (they're already absolute); crawling them is not.

    Returns False for malformed URLs (urlsplit produces an empty scheme).
    """
    if not url:
        return False
    try:
        scheme: str = urlsplit(url).scheme
    except ValueError:
        return False
    return scheme.lower() in CRAWLABLE_SCHEMES
