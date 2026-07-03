import re
from graph_crawl._parsers.selectolax_parser import ParserError, parse as _parse_tree
from graph_crawl.schemas.parse import (
    Anchor,
    BaseElement,
    CanonicalLink,
    MetaRefresh,
    MetaRobots,
    ParsedDocument,
    ParseOutcome,
)
from graph_crawl.urls import UnresolvableReference, resolve
from selectolax.lexbor import LexborHTMLParser

_WHITESPACE_ONLY = re.compile(r"\A\s*\Z")
_ROBOTS_TOKEN_SEP = re.compile(r"[,\s]+")


def parse_html(html: str, *, base_url: str | None = None) -> ParsedDocument:
    if not isinstance(html, str):
        raise TypeError(f"html must be a str, not {type(html).__name__}")
    if _WHITESPACE_ONLY.match(html):
        return ParsedDocument(outcome=ParseOutcome.empty, effective_base_url=base_url)

    try:
        tree: LexborHTMLParser = _parse_tree(html)
    except ParserError:
        return ParsedDocument(outcome=ParseOutcome.parser_error, effective_base_url=base_url)

    base = _extract_base(tree)
    effective_base = _resolve_effective_base(base_url, base)

    return ParsedDocument(
        outcome=ParseOutcome.ok,
        effective_base_url=effective_base,
        anchors=_extract_anchors(tree),
        canonical=_extract_canonical(tree),
        base=base,
        meta_refresh=_extract_meta_refresh(tree),
        meta_robots=_extract_meta_robots(tree),
    )


def _extract_base(tree: LexborHTMLParser) -> BaseElement | None:
    """First <base href> in tree order wins (WHATWG §4.2.3). A <base> with no
    href (only a target) is not a base override and is ignored here."""
    node = tree.css_first("base")
    if node is None:
        return None
    href = node.attributes.get("href")
    if not href:
        return None
    return BaseElement(href=href, target=node.attributes.get("target"))


def _resolve_effective_base(base_url: str | None, base: BaseElement | None) -> str | None:
    if base is None:
        return base_url
    if not base_url:
        return base.href
    try:
        return resolve(base_url, base.href)
    except UnresolvableReference:
        return base.href


def _extract_anchors(tree: LexborHTMLParser) -> list[Anchor]:
    """All <a> elements with an href attribute, in document order.

    Comments, <script>, <style>, <textarea> etc. are comment / raw-text contexts
    and never produce <a> elements in a spec-aware parser, so css('a') already
    excludes links that only "look like" anchors inside those. An <a> without
    href is an anchor target, not a hyperlink, and is skipped.
    """
    anchors: list[Anchor] = []
    for node in tree.css("a"):
        href = node.attributes.get("href")
        if href is None:
            continue
        rel_raw = node.attributes.get("rel")
        rel: str | None = None
        if rel_raw:
            rel = " ".join(rel_raw.lower().split()) or None
        text = " ".join(node.text(deep=True, separator=" ", strip=True, skip_empty=True).split())
        anchors.append(Anchor(href=href, rel=rel, text=text))
    return anchors


def _extract_canonical(tree: LexborHTMLParser) -> CanonicalLink | None:
    """First <link> whose rel token list contains 'canonical' (case-insensitive)
    wins. rel is a space-separated list, so 'canonical alternate' still counts.
    A canonical link without href is skipped (search continues)."""
    for node in tree.css("link"):
        rel = node.attributes.get("rel")
        if not rel:
            continue
        if "canonical" in {t.lower() for t in rel.split()}:
            href = node.attributes.get("href")
            if href is None:
                continue
            return CanonicalLink(href=href)
    return None


def _extract_meta_refresh(tree: LexborHTMLParser) -> MetaRefresh | None:
    """First <meta http-equiv=refresh content=...> in tree order.

    Content format (HTML §4.6.4, browser-lenient):
        "5"                 -> refresh same URL after 5s
        "0; url=/path"      -> redirect to /path immediately
        "5;url=https://x"   -> redirect after 5s
    The 'url=' keyword is case-insensitive and may be surrounded by spaces.
    Unparseable content yields None (treated as no refresh).
    """
    for node in tree.css("meta"):
        http_equiv = node.attributes.get("http-equiv")
        if not http_equiv or http_equiv.strip().lower() != "refresh":
            continue
        content = node.attributes.get("content")
        if content is None:
            continue
        parsed = _parse_meta_refresh_content(content)
        if parsed is not None:
            return parsed
    return None


def _parse_meta_refresh_content(content: str) -> MetaRefresh | None:
    if not content:
        return None
    if ";" in content:
        delay_part, _, rest = content.partition(";")
    else:
        delay_part, rest = content, ""
    try:
        delay = float(delay_part.strip())
    except ValueError:
        return None
    if delay < 0:
        return None
    target_url: str | None = None
    rest = rest.strip()
    if rest:
        match = re.match(r"url\s*=\s*(.*)\Z", rest, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        target_url = match.group(1).strip() or None
    return MetaRefresh(delay_seconds=delay, target_url=target_url)


def _extract_meta_robots(tree) -> MetaRobots:
    """Merge all <meta name=robots content=...> tags. Per Google's docs, multiple
    robots metas are unioned; we follow the same conservative approach (if ANY
    meta says noindex, treat as noindex). Unknown directives are ignored but kept
    verbatim in MetaRobots.raw. 'none' == noindex+nofollow."""

    raw_parts: list[str] = []
    tokens: set[str] = set()

    for node in tree.css("meta"):
        name = node.attributes.get("name")
        if not name or name.strip().lower() != "robots":
            continue
        content = node.attributes.get("content") or ""
        if content:
            raw_parts.append(content)
        for token in _ROBOTS_TOKEN_SEP.split(content):
            token = token.strip().lower()
            if token:
                tokens.add(token)

    if not raw_parts:
        return MetaRobots()

    raw = ", ".join(raw_parts)
    noindex = "noindex" in tokens or "none" in tokens
    nofollow = "nofollow" in tokens or "none" in tokens
    noarchive = "noarchive" in tokens
    return MetaRobots(raw=raw, noindex=noindex, nofollow=nofollow, noarchive=noarchive)
