import pytest
from graph_crawl.normalize import normalize

TRACKING = {"utm_source", "utm_medium", "fbclid", "sessionid"}


# --- Idempotence (the most important property) ---
@pytest.mark.parametrize(
    "u",
    [
        "http://example.org/a/b",
        "HTTP://Example.ORG:80/x?b=2&a=1&fbclid=ABC#frag",
        "https://site.org/%7Euser/foo/../bar/.",
        "http://münster.de/path",
        "http://site.org/a//b/../c?utm_source=x&a=1",
        "http://site.org/a//b/../c?utm_source=x&a=1",  # already normalized
    ],
)
def test_idempotent(u):
    once = normalize(u, strip_params=TRACKING)
    twice = normalize(once, strip_params=TRACKING)
    assert once == twice, f"not idempotent: {once!r} vs {twice!r}"


# --- Syntactic transformations (RFC syntax-based) ---
def test_lowercase_scheme_and_host():
    assert normalize("HTTP://EXAMPLE.ORG/X").startswith("http://example.org/")


def test_strip_default_http_port():
    assert normalize("http://example.org:80/x") == "http://example.org/x"


def test_strip_default_https_port():
    assert normalize("https://example.org:443/x") == "https://example.org/x"


def test_keep_nondefault_port():
    assert normalize("http://example.org:8080/x") == "http://example.org:8080/x"


def test_uppercase_pct_encoding():
    assert normalize("http://e.org/%7e") == "http://e.org/~"


def test_decode_unreserved_only():
    # %2F is reserved ("/") — must stay encoded.
    assert normalize("http://e.org/a%2Fb").split("://", 1)[1].endswith("/a%2Fb")


def test_remove_dot_segments():
    assert normalize("http://e.org/a/../b") == "http://e.org/b"
    assert normalize("http://e.org/a/./b") == "http://e.org/a/b"
    assert normalize("http://e.org/./") == "http://e.org/"
    assert normalize("http://e.org/a/b/../..") == "http://e.org/"


def test_strip_fragment():
    assert "#" not in normalize("http://e.org/x#section")


def test_empty_path_becomes_root():
    assert normalize("http://e.org") == "http://e.org/"


# --- Policy: trailing slash preserved ---
def test_trailing_slash_preserved_distinct():
    assert normalize("http://e.org/page") != normalize("http://e.org/page/")
    assert normalize("http://e.org/page") == "http://e.org/page"
    assert normalize("http://e.org/page/") == "http://e.org/page/"


# --- Policy: query params sorted + tracking stripped + others kept ---
def test_query_sorted():
    assert normalize("http://e.org/x?b=2&a=1") == "http://e.org/x?a=1&b=2"


def test_tracking_param_stripped():
    assert normalize("http://e.org/x?a=1&fbclid=ABC", strip_params=TRACKING) == "http://e.org/x?a=1"


def test_real_param_kept():
    # lang is NOT in the strip list — it's real content for multilingual sites.
    assert (
        normalize("http://corteidh.or.cr/doc?lang=en&lang=es", strip_params=TRACKING)
        == "http://corteidh.or.cr/doc?lang=en&lang=es"
    )

    assert (
        normalize("http://corteidh.or.cr/doc?lang=es&lang=en", strip_params=TRACKING)
        == "http://corteidh.or.cr/doc?lang=en&lang=es"
    )


def test_duplicate_keys_preserved_and_sorted():
    assert normalize("http://e.org/x?tag=b&tag=a") == "http://e.org/x?tag=a&tag=b"


def test_empty_query_dropped():
    assert normalize("http://e.org/x?") == "http://e.org/x"
    assert normalize("http://e.org/x?fbclid=ABC", strip_params=TRACKING) == "http://e.org/x"


# --- NOT applied here (these belong to canonicalize(), confirm) ---
def test_no_http_to_https_rewrite():
    assert normalize("http://e.org/x").startswith("http://")


def test_no_www_strip():
    assert normalize("http://www.e.org/x").endswith("www.e.org/x")


def test_no_path_case_lowercasing():
    # Path is case-sensitive by spec. NEVER lowercase it.
    assert normalize("http://e.org/SomePage") == "http://e.org/SomePage"


# --- IDN hosts ---
def test_idn_to_punycode():
    assert normalize("http://münster.de/x").split("://", 1)[1].startswith("xn--")
