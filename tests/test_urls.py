import pytest

from graph_crawl.urls import (
    CRAWLABLE_SCHEMES,
    UnresolvableReference,
    is_crawlable,
    resolve,
)

BASE = "http://a/b/c/d;p?q"

# --- RFC 3986 §5.4.1 normal cases ---
NORMAL_CASES = [
    ("g:h", "g:h"),
    ("g", "http://a/b/c/g"),
    ("./g", "http://a/b/c/g"),
    ("g/", "http://a/b/c/g/"),
    ("/g", "http://a/g"),
    ("//g", "http://g"),
    ("?y", "http://a/b/c/d;p?y"),
    ("g?y", "http://a/b/c/g?y"),
    ("#s", "http://a/b/c/d;p?q#s"),
    ("g#s", "http://a/b/c/g#s"),
    ("", "http://a/b/c/d;p?q"),
    (".", "http://a/b/c/"),
    ("./", "http://a/b/c/"),
    ("..", "http://a/b/"),
    ("../", "http://a/b/"),
    ("../g", "http://a/b/g"),
    ("../..", "http://a/"),
    ("../../", "http://a/"),
    ("../../g", "http://a/g"),
]

# --- RFC 3986 §5.4.2 abnormal cases (the boundary-breaking ones) ---
ABNORMAL_CASES = [
    ("../../../g", "http://a/g"),
    ("../../../../g", "http://a/g"),
    ("/./g", "http://a/g"),
    ("/../g", "http://a/g"),
    ("g.", "http://a/b/c/g."),
    (".g", "http://a/b/c/.g"),
    ("g..", "http://a/b/c/g.."),
    ("..g", "http://a/b/c/..g"),
    ("./../g", "http://a/b/g"),
    ("./g/.", "http://a/b/c/g/"),
    ("g/./h", "http://a/b/c/g/h"),
    ("g/../h", "http://a/b/c/h"),
    ("g;x=1/./y", "http://a/b/c/g;x=1/y"),
    ("g;x=1/../y", "http://a/b/c/y"),
]


@pytest.mark.parametrize(
    "ref, expected", NORMAL_CASES + ABNORMAL_CASES, ids=lambda v: v if isinstance(v, str) and v else "empty"
)
def test_resolve_rfc3986_vectors(ref, expected):
    assert resolve(BASE, ref) == expected


# --- Real-world cases ---
def test_resolve_real_world_absolute_with_https_base():
    assert resolve("https://www.corteidh.or.cr/en/page.html", "../doc.pdf") == "https://www.corteidh.or.cr/doc.pdf"


def test_resolve_scheme_relative_keeps_base_scheme():
    # //host uses base's scheme, not hard-coded http.
    assert resolve("https://a.org/x", "//b.org/y") == "https://b.org/y"
    assert resolve("http://a.org/x", "//b.org/y") == "http://b.org/y"


def test_resolve_path_absolute_uses_base_authority():
    assert resolve("https://a.org/dir/page", "/root/page") == "https://a.org/root/page"


def test_resolve_empty_ref_returns_base():
    # Same-document reference — base URL unchanged.
    assert resolve("https://a.org/x?q=1#frag", "") == "https://a.org/x?q=1#frag"


def test_resolve_fragment_only_replaces_fragment():
    # #sec replaces the base's fragment with sec, keeps everything else.
    assert resolve("https://a.org/x?q=1#old", "#sec") == "https://a.org/x?q=1#sec"


def test_resolve_non_http_scheme_passes_through():
    # mailto: is already absolute — resolve() returns it unchanged.
    # Filtering happens separately via is_crawlable().
    assert resolve("https://a.org/x", "mailto:contact@a.org") == "mailto:contact@a.org"
    assert resolve("https://a.org/x", "javascript:void(0)") == "javascript:void(0)"
    assert resolve("https://a.org/x", "data:text/plain,hello") == "data:text/plain,hello"


def test_resolve_idn_host_in_ref():
    # Punycode conversion is normalize()'s job, not resolve()'s. resolve()
    # should preserve the IRI as-is; normalize() turns it into Punycode.
    ref = resolve("https://example.org/x", "//münster.de/y")
    assert "münster.de" in ref or "xn--" in ref  # accept either, since urljoin may encode


def test_resolve_does_not_normalize_host_or_port():
    # urljoin does lowercase the scheme — that's fine, RFC says scheme is
    # case-insensitive and normalize() would lowercase it anyway. What we
    # care about is that resolve() does NOT touch the host case or the
    # default port: those are normalize()'s responsibility, and conflating
    # them would make resolve() and normalize() overlap in confusing ways.
    assert resolve("HTTP://A.ORG/x", "/y") == "http://A.ORG/y"
    assert resolve("http://a.org:80/x", "/y") == "http://a.org:80/y"


# --- Error cases ---
def test_resolve_rejects_empty_base():
    with pytest.raises(UnresolvableReference, match="base URL is required"):
        resolve("", "/x")


def test_resolve_rejects_relative_base():
    with pytest.raises(UnresolvableReference, match="must be absolute"):
        resolve("/relative/base", "/x")


def test_resolve_rejects_base_without_authority():
    with pytest.raises(UnresolvableReference, match="must be absolute"):
        resolve("mailto:foo@bar.org", "/x")


def test_resolve_rejects_none_ref():
    with pytest.raises(UnresolvableReference, match="ref is None"):
        resolve("https://a.org/x", None)  # type: ignore[arg-type]


# --- is_crawlable ---
@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://a.org/x", True),
        ("https://a.org/x", True),
        ("HTTPS://a.org/x", True),  # case-insensitive
        ("Http://a.org/x", True),
        ("mailto:foo@bar.org", False),
        ("javascript:void(0)", False),
        ("data:text/plain,hello", False),
        ("tel:+1234", False),
        ("ftp://a.org/x", False),
        ("ws://a.org/x", False),
        ("", False),
    ],
)
def test_is_crawlable(url, expected):
    assert is_crawlable(url) is expected


def test_crawlable_schemes_constant():
    # Make sure nobody accidentally removes https or adds ftp.
    assert CRAWLABLE_SCHEMES == frozenset({"http", "https"})
