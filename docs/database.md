# Phase 5 — Database

Locked decisions for the persistence layer, recorded so later phases stay aligned.

## The two-kinds-of-state principle

Crawl state is two different things; conflating them is the classic crawler mistake:

| Kind | Holds | Lifetime | Where it lives |
|---|---|---|---|
| Operational | frontier queue + already-seen set | short, hot | Redis / Berkeley DB (Heritrix `BdbUriUniqFilter`), in-memory |
| Artifact | every URL, edge, fetch attempt, snapshot | permanent | Postgres / BigTable / WARC-on-S3 |

Phase 5 builds the **artifact store**. The frontier stays in-memory (Phase 19 makes it
distributed). The `resources` table IS the seen-set; `pending` rows ARE the frontier,
so resumability (a later phase) is `SELECT url FROM resources WHERE resource_state='pending'`.

## Stack

PostgreSQL + SQLAlchemy 2.x async (`create_async_engine`, `AsyncSession`) + asyncpg driver.
Alembic for migrations (async `env.py`, reads `GRAPH_CRAWL_DB_DSN`).

## State taxonomy (locked here)

`ResourceState` (the resource row's lifecycle — drives crawl decisions):
`pending, fetched, fetched_leaf, not_found, gone, needs_auth, error, backoff, skipped`.

`FetchOutcome` (one fetch attempt's raw HTTP result — stored on `fetch_history.outcome`)
is unchanged and is the source of truth; `ResourceState` is derived from it losslessly.

- `gone` (410): permanently deleted; never retry. Distinct from `not_found` (404: may reappear).
- `needs_auth` (401/403): crawlable with credentials; distinct from generic `error`.

Previously 401/403 collapsed to `error` and 410 to `not_found`, losing scheduling signal.

## Tables

- `resources` — one row per normalized URL. PK = `url` (= `normalize()` output). The seen-set.
- `edges` — one row per deduped `(source, target)` hyperlink. FK→resources (both ends).
- `fetch_history` — append-only, one row per fetch attempt. The status-history timeline.
- `crawl_runs` — one row per crawl execution; denormalized `CrawlStats` for quick reporting.
- `snapshots` — reserved for Phase 11 (created now for stable FKs; `storage_uri`/`body_bytes` NULL).

## Write-through design

- `Crawler` depends on the `CrawlSink` Protocol only — never imports SQLAlchemy (mirrors the
  selectolax-behind-`parser.py` pattern). `NullSink` is the default (Phase 4 behavior preserved).
- `DbCrawlSink` writes through after each state change. One session/transaction per `record_*`
  call (a crawl run is NOT one giant transaction — that would hold locks for the whole crawl).
- Writes use SQLAlchemy Core `INSERT ... ON CONFLICT` (not ORM `merge`): correct
  preserve-`discovered_at`-on-conflict semantics in one statement, and markedly faster.
- In `_discover_links`, the target resource is recorded BEFORE the edge, so the `edges→resources`
  FK holds. The in-memory result is unchanged (edges still deduped by `(source, target)`).

## Enums as TEXT, not Postgres ENUM

Postgres ENUM types are painful to migrate (adding/removing members is blocking). The app layer
(pydantic `StrEnum`) is the integrity boundary — same principle as `normalize()` for URLs. So
`resource_state`/`resource_type`/`outcome` are `TEXT` columns holding the enum's string value.

## URLs as TEXT, not VARCHAR(n)

URLs have no bounded length (RFC 3986 permits arbitrarily long URIs; real sites exceed 2 KB).
`VARCHAR(n)` would eventually reject a legal URL. Postgres `TEXT` and `VARCHAR` share storage;
`normalize()` is the integrity boundary, not a column constraint.

## Testing

Two-tier: pure unit tests (respx mocks, `NullSink` — no DB needed, always green) + opt-in
integration tests gated on `GRAPH_CRAWL_TEST_DSN` that assert the DB is a faithful write-through
of the in-memory `CrawlResult`.

## Deferred

- DB-backed frontier / resumability — Phase 13/19 (schema already supports it via `pending` rows).
- Edge history (did an edge survive across crawls?) — append table later if needed; for now
  `edges` is unique on `(source, target)` with `discovered_at`/`last_seen_at`.
- Snapshots population — Phase 11.
- Batched writes (grouping many `record_*` calls per transaction) — Phase 19 scaling.