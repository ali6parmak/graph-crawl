import pytest

from graph_crawl.parser import parse_html
from graph_crawl.schemas.parse import ParseOutcome


# --- happy path: well-formed HTML, all five tags ---


def test_well_formed_html_extracts_all_tags():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <base href="https://site.org/blog/" target="_blank">
        <link rel="canonical" href="https://site.org/blog/post-1">
        <meta http-equiv="refresh" content="0; url=/moved">
        <meta name="robots" content="noindex, nofollow">
      </head>
      <body>
        <a href="/post-2">Next</a>
        <a href="https://other.org/x" rel="nofollow noopener">External</a>
        <a name="anchor-only">No href here</a>
      </body>
    </html>
    """
    doc = parse_html(html, base_url="https://site.org/blog/post-1")

    assert doc.outcome is ParseOutcome.ok
    assert doc.effective_base_url == "https://site.org/blog/"  # base applied
    assert doc.base and doc.base.href == "https://site.org/blog/"
    assert doc.base.target == "_blank"
    assert doc.canonical and doc.canonical.href == "https://site.org/blog/post-1"
    assert doc.meta_refresh and doc.meta_refresh.delay_seconds == 0.0
    assert doc.meta_refresh and doc.meta_refresh.target_url == "/moved"
    assert doc.meta_robots.noindex is True
    assert doc.meta_robots.nofollow is True

    assert [a.href for a in doc.anchors] == ["/post-2", "https://other.org/x"]
    assert doc.anchors[0].text == "Next"
    assert doc.anchors[1].rel == "nofollow noopener"
    assert doc.anchors[1].text == "External"


# --- anchors: malformed HTML recovery ---


def test_unclosed_anchors_auto_closed_by_parser():
    # Parser inserts missing end tags: <a href=a><a href=b> -> two siblings.
    doc = parse_html('<a href="a">x<a href="b">y', base_url="https://x/")
    assert [a.href for a in doc.anchors] == ["a", "b"]


def test_duplicate_attributes_first_wins():
    # WHATWG: the first occurrence of a duplicate attribute is the one that counts.
    doc = parse_html('<a href="first" href="second">x</a>', base_url="https://x/")
    assert doc.anchors[0].href == "first"


def test_unquoted_and_single_quoted_attributes():
    doc = parse_html("<a href=x>u</a><a href='y'>s</a>", base_url="https://x/")
    assert [a.href for a in doc.anchors] == ["x", "y"]


def test_whitespace_around_equals():
    doc = parse_html('<a  href = "x" >t</a>', base_url="https://x/")
    assert doc.anchors[0].href == "x"


def test_entity_decoded_in_href():
    # The parser decodes &amp; -> & in attribute values. normalize() re-encodes.
    doc = parse_html('<a href="?a=1&amp;b=2">x</a>', base_url="https://x/")
    assert doc.anchors[0].href == "?a=1&b=2"


def test_empty_href_kept():
    # href="" means "current document" per WHATWG; resolve() handles it. Keep it.
    doc = parse_html('<a href="">self</a>', base_url="https://x/")
    assert doc.anchors[0].href == ""


def test_anchor_without_href_skipped():
    doc = parse_html('<a name="foo">label</a><a href="x">link</a>', base_url="https://x/")
    assert [a.href for a in doc.anchors] == ["x"]


# --- comments / raw-text elements: no false anchors ---


def test_anchor_inside_comment_not_extracted():
    html = '<div><!-- <a href="x">not a link</a> --><a href="y">real</a></div>'
    doc = parse_html(html, base_url="https://x/")
    assert [a.href for a in doc.anchors] == ["y"]


def test_anchor_inside_textarea_not_extracted():
    # <textarea> is a raw-text element; its content is literal text, not markup.
    html = '<textarea><a href="x">not a link</a></textarea><a href="y">real</a>'
    doc = parse_html(html, base_url="https://x/")
    assert [a.href for a in doc.anchors] == ["y"]


def test_anchor_inside_script_not_extracted():
    html = '<script>var x = \'<a href="x">\';</script><a href="y">real</a>'
    doc = parse_html(html, base_url="https://x/")
    assert [a.href for a in doc.anchors] == ["y"]


# --- <noscript>: parsed as live markup in our no-JS world ---


def test_anchor_inside_noscript_is_extracted():
    # Our crawler runs with scripting disabled (like Googlebot's first pass), so
    # <noscript> content is live markup, not raw text. If this test fails after a
    # selectolax upgrade, the backend's scripting flag changed and we must
    # re-evaluate how noscript content is handled.
    html = '<noscript><a href="/x">fallback link</a></noscript><a href="/y">main</a>'
    doc = parse_html(html, base_url="https://x/")
    hrefs = [a.href for a in doc.anchors]
    assert "/x" in hrefs
    assert "/y" in hrefs


# --- <base> semantics ---


def test_no_base_returns_document_url_as_effective_base():
    doc = parse_html('<a href="x">x</a>', base_url="https://site.org/p")
    assert doc.base is None
    assert doc.effective_base_url == "https://site.org/p"


def test_first_base_wins_when_multiple():
    html = '<base href="/first/"><base href="/second/"><a href="x">x</a>'
    doc = parse_html(html, base_url="https://site.org/p")
    assert doc.base and doc.base.href == "/first/"
    assert doc.effective_base_url == "https://site.org/first/"


def test_base_with_only_target_ignored():
    html = '<base target="_blank"><a href="x">x</a>'
    doc = parse_html(html, base_url="https://site.org/p")
    assert doc.base is None
    assert doc.effective_base_url == "https://site.org/p"


def test_base_without_base_url_returns_raw_base_href():
    # No document URL to resolve against: pass the (usually absolute) base.href.
    doc = parse_html('<base href="https://cdn.org/x/"><a href="y">y</a>', base_url=None)
    assert doc.effective_base_url == "https://cdn.org/x/"


# --- <link rel=canonical> ---


def test_canonical_case_insensitive_rel():
    doc = parse_html('<link rel="Canonical" href="https://x/c">', base_url="https://x/")
    assert doc.canonical and doc.canonical.href == "https://x/c"


def test_canonical_rel_with_multiple_tokens():
    doc = parse_html('<link rel="canonical alternate" href="https://x/c">', base_url="https://x/")
    assert doc.canonical and doc.canonical.href == "https://x/c"


def test_canonical_first_wins_when_multiple():
    html = '<link rel="canonical" href="https://x/first"><link rel="canonical" href="https://x/second">'
    doc = parse_html(html, base_url="https://x/")
    assert doc.canonical and doc.canonical.href == "https://x/first"


def test_canonical_absent():
    doc = parse_html("<p>no canonical here</p>", base_url="https://x/")
    assert doc.canonical is None


def test_canonical_without_href_skipped():
    html = '<link rel="canonical"><link rel="canonical" href="https://x/c">'
    doc = parse_html(html, base_url="https://x/")
    assert doc.canonical and doc.canonical.href == "https://x/c"


# --- <meta http-equiv=refresh> ---


def test_meta_refresh_redirect():
    doc = parse_html('<meta http-equiv="refresh" content="5; url=/next">', base_url="https://x/")
    assert doc.meta_refresh and doc.meta_refresh.delay_seconds == 5.0
    assert doc.meta_refresh and doc.meta_refresh.target_url == "/next"


def test_meta_refresh_immediate():
    doc = parse_html('<meta http-equiv="refresh" content="0; url=/now">', base_url="https://x/")
    assert doc.meta_refresh and doc.meta_refresh.delay_seconds == 0.0
    assert doc.meta_refresh and doc.meta_refresh.target_url == "/now"


def test_meta_refresh_case_insensitive_keyword():
    doc = parse_html('<meta http-equiv="refresh" content="3; URL=/x">', base_url="https://x/")
    assert doc.meta_refresh and doc.meta_refresh.delay_seconds == 3.0
    assert doc.meta_refresh and doc.meta_refresh.target_url == "/x"


def test_meta_refresh_pure_refresh_no_url():
    doc = parse_html('<meta http-equiv="refresh" content="10">', base_url="https://x/")
    assert doc.meta_refresh and doc.meta_refresh.delay_seconds == 10.0
    assert doc.meta_refresh and doc.meta_refresh.target_url is None


def test_meta_refresh_malformed_returns_none():
    doc = parse_html('<meta http-equiv="refresh" content="not-a-number">', base_url="https://x/")
    assert doc.meta_refresh is None


def test_meta_refresh_case_insensitive_http_equiv():
    doc = parse_html('<meta Http-Equiv="Refresh" content="2; url=/x">', base_url="https://x/")
    assert doc.meta_refresh is not None
    assert doc.meta_refresh.target_url == "/x"


def test_meta_refresh_absent():
    doc = parse_html("<p>none</p>", base_url="https://x/")
    assert doc.meta_refresh is None


# --- <meta name=robots> ---


def test_meta_robots_directives():
    doc = parse_html('<meta name="robots" content="noindex, nofollow, noarchive">', base_url="https://x/")
    assert doc.meta_robots.noindex is True
    assert doc.meta_robots.nofollow is True
    assert doc.meta_robots.noarchive is True
    assert "noindex" in doc.meta_robots.raw


def test_meta_robots_none_means_noindex_and_nofollow():
    doc = parse_html('<meta name="robots" content="none">', base_url="https://x/")
    assert doc.meta_robots.noindex is True
    assert doc.meta_robots.nofollow is True


def test_meta_robots_case_insensitive():
    doc = parse_html('<meta name="ROBOTS" content="NOINDEX, Follow">', base_url="https://x/")
    assert doc.meta_robots.noindex is True
    assert doc.meta_robots.nofollow is False


def test_meta_robots_unknown_directives_kept_in_raw_but_ignored():
    doc = parse_html('<meta name="robots" content="noindex, max-image-preview:large">', base_url="https://x/")
    assert doc.meta_robots.noindex is True
    assert "max-image-preview:large" in doc.meta_robots.raw


def test_meta_robots_multiple_metas_unioned():
    html = '<meta name="robots" content="noindex"><meta name="robots" content="noarchive">'
    doc = parse_html(html, base_url="https://x/")
    assert doc.meta_robots.noindex is True
    assert doc.meta_robots.noarchive is True


def test_meta_robots_absent_is_default_allowed():
    doc = parse_html("<p>none</p>", base_url="https://x/")
    assert doc.meta_robots.noindex is False
    assert doc.meta_robots.nofollow is False
    assert doc.meta_robots.raw == ""


# --- input edge cases ---


def test_empty_string_returns_empty_outcome():
    doc = parse_html("", base_url="https://x/")
    assert doc.outcome is ParseOutcome.empty
    assert doc.anchors == []
    assert doc.effective_base_url == "https://x/"


def test_whitespace_only_returns_empty_outcome():
    doc = parse_html("   \n\t  ", base_url="https://x/")
    assert doc.outcome is ParseOutcome.empty


def test_none_raises_type_error():
    with pytest.raises(TypeError):
        parse_html(None)  # type: ignore[arg-type]


def test_non_str_raises_type_error():
    with pytest.raises(TypeError):
        parse_html(b"<html></html>")  # type: ignore[arg-type]


def test_no_base_url_is_allowed():
    doc = parse_html('<a href="x">x</a>', base_url=None)
    assert doc.outcome is ParseOutcome.ok
    assert doc.effective_base_url is None
    assert doc.anchors[0].href == "x"


def test_is_ok_property():
    assert parse_html("<a href=x>x</a>").is_ok is True
    assert parse_html("").is_ok is False
