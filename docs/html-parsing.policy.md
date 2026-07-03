# Phase 3 — HTML Parsing Policy

Locked decisions for the HTML parsing layer, recorded so future work stay aligned.

## Parser backend: selectolax (Lexbor)

Chosen over BeautifulSoup and lxml. Reasons:
- Lexbor follows the modern WHATWG HTML Standard (HTML5-era recovery, raw-text
  element handling, foster-parenting). libxml2 (lxml / BS+lxml) uses the older
  HTML4-era "tagsoup" recovery.
- ~5–15x faster than lxml for large pages and far lower memory, because it does
  not materialise a Python object per node. This matters at crawl scale.
- We are a read-only parser. selectolax's weaknesses (no XPath, limited tree
  mutation) don't apply to a discovery engine.

The backend lives behind `_parsers/selectolax_parser.py`. The rest of the
codebase imports `graph_crawl.parser` only and never touches selectolax. If we
ever need a second backend (lxml fallback for a pathological page, or a
Playwright-rendered pass), it is a drop-in replacement at that one file.

## What this layer does and does not do

Does:
- Parse HTML into a tree per WHATWG (broken markup recovered, never raised).
- Extract the five Phase 3 tags: `<a href>`, `<link rel=canonical>`,
  `<base href>`, `<meta http-equiv=refresh>`, `<meta name=robots>`.
- Apply `<base>` and return `effective_base_url` for callers to pass to
  `resolve()`. `<base>` is a parsing concern (RFC 3986 §5.1), not a discovery
  concern, so the parser owns it.

Does NOT:
- Resolve or normalize any href. That is the caller's job and preserves the
  locked pipeline: `extract -> resolve() -> is_crawlable() -> normalize() -> store`.
- Detect charset / accept bytes. The fetcher layer is responsible for decoding
  response bytes to `str` using the HTTP `Content-Type` charset (with
  `<meta charset>` fallback, per the Encoding Standard) before calling
  `parse_html`. This is deferred to a later phase to keep the parser simple.
- Discover all link types. `<iframe>`, `<img>`, `<video>`, `<source>`,
  `<embed>`, `<object>`, `<area>`, `<script src>`, `<form action>` etc. are
  Phase 6. Phase 3 is parsing + the five structural tags only.

## Locked rules

| Rule | Source | Behaviour |
|---|---|---|
| Malformed HTML | WHATWG §13 | Recover, never raise. Fatal parser errors surface as `ParseOutcome.parser_error`. |
| Empty / whitespace input | — | `ParseOutcome.empty`, no anchors. |
| `None` / non-str input | — | `TypeError` (programmer error, not data). |
| Duplicate attributes | WHATWG | First occurrence wins. We rely on Lexbor to enforce. |
| `<base>` first-wins | HTML §4.2.3 | First `<base href>` in tree order sets the effective base. |
| `<link rel=canonical>` first-wins | — | First canonical link with an href wins. |
| `<a>` without href | WHATWG | Not a hyperlink (it is an anchor target); skipped. |
| `href=""` | WHATWG | Means "current document"; kept, `resolve()` handles it. |
| Anchor href values | — | Raw, entity-decoded by the parser (e.g. `&amp;` -> `&`), unresolved. |
| `<base>` resolution | RFC 3986 §5.1 | `effective_base_url = resolve(doc_url, base.href)` when `<base>` present, else `doc_url`. |
| Comments / `<script>` / `<style>` / `<textarea>` | WHATWG raw-text rules | Content is not markup; `<a>` inside these is never a link. |
| `<noscript>` | WHATWG | Parsed as live markup (scripting disabled — matches a no-JS crawler like Googlebot's first pass). Links inside are real. |
| `<meta name=robots>` multiple | Google docs | Unioned: if any meta says `noindex`, treat as `noindex`. |
| `none` directive | Google docs | Equivalent to `noindex, nofollow`. |
| Unknown robots directives | — | Ignored for the booleans, kept verbatim in `MetaRobots.raw`. |
| `<meta http-equiv=refresh>` | HTML §4.6.4 | `delay; url=target`. `url=` case-insensitive. Unparseable content -> no refresh. (Note: a refresh URL wrapped in single quotes inside the attribute is not handled — Lexbor fails to parse the `<meta>` element in that case, which is rare and non-spec.) |

## Why not regex

A regex `<a href="...">` extractor breaks on: unquoted / single-quoted /
whitespace-padded attributes, duplicate attributes, anchors inside comments or
raw-text elements, `<base>` changing resolution, and entity-encoded values. A
spec-aware parser internalises the whole "what is a tag / attribute / raw
text" body of knowledge. No production crawler uses regex for link extraction.