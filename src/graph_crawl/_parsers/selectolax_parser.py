"""
This is the single library-specific module in the project. Everything else
imports graph_crawler.parser, never selectolax directly. Swapping the parser
backend (e.g. to lxml) means replacing only this file.
"""

from selectolax.lexbor import LexborHTMLParser


class ParserError(Exception):
    """Internal: raised when the underlying parsre cannot produce a tree.
    graph_crawl.parser converts this into ParseOutcome.parser_error."""


def parse(html: str) -> LexborHTMLParser:
    """Parse HTML into a Lexbor document tree.

    Lexbor follows the WHATWG HTML Standard: malformed input is recovered, never
    raised. We wrap any unexpected library exception as ParserError so the public
    parse_html() contract ("never raises on bad data") holds even if a future
    Lexbor version starts raising on pathological input.
    """
    try:
        return LexborHTMLParser(html)
    except Exception as exc:
        raise ParserError(f"selectolax failed to parse HTML: {exc}") from exc
