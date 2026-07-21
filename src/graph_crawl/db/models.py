"""SQLAlchemy ORM models for the artifact graph.

Tables:
  crawl_runs    one row per crawl execution (provenance + denormalized stats)
  resources     one row per normalized URL ever discovered (the seen-set + frontier source)
  edges         one row per deduped hyperlink (source -> target)
  fetch_history append-only, one row per fetch attempt (the status-history timeline)
  snapshots     reserved for Phase 11; not populated in Phase 5

Enums are stored as TEXT (the StrEnum string value), not Postgres ENUM types:
Postgres ENUMs are painful to migrate (adding/removing members is an expensive,
blocking operation), and the app layer (pydantic StrEnum) is the integrity
boundary — the same principle that keeps ``normalize()`` the boundary for URL
identity. URLs are TEXT (unbounded) for the same reason: a column length cap
will eventually reject a legal URL, and Postgres TEXT/VARCHAR share storage."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, LargeBinary, Float, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from graph_crawl.db.base import Base


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seed_url: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Config snapshot
    max_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_frontier_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delay: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Denormalized CrawlStats for quick reporting without a full join.
    stats_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_fetched_leaf: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_not_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_gone: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_needs_auth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_error: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_backoff: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stats_max_frontier_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Resource(Base):
    __tablename__ = "resources"

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_state: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    seed_url: Mapped[str] = mapped_column(Text, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fetch_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # Phase 9
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_resources_state", "resource_state"),
        Index("ix_resources_discovered_at", "discovered_at"),
        Index("ix_resources_type", "resource_type"),
        Index("ix_resources_content_hash", "content_hash"),
    )


class Edge(Base):
    __tablename__ = "edges"

    source_url: Mapped[str] = mapped_column(
        Text, ForeignKey("resources.url", ondelete="RESTRICT"), nullable=False, primary_key=True
    )
    target_url: Mapped[str] = mapped_column(
        Text, ForeignKey("resources.url", ondelete="RESTRICT"), nullable=False, primary_key=True
    )
    rel: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_href: Mapped[str | None] = mapped_column(Text, nullable=True)  # resolved-absolute, pre-normalize
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    discovery_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_seen_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True
    )


class FetchHistory(Base):
    __tablename__ = "fetch_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, ForeignKey("resources.url", ondelete="RESTRICT"), nullable=False)
    crawl_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True
    )
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    redirect_chain: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # The "history of this URL" query: ORDER BY attempted_at DESC.
        Index("ix_fetch_history_url_time", "url", "attempted_at"),
        Index("ix_fetch_history_run", "crawl_run_id"),
    )


class Snapshot(Base):
    """Reserved for Phase 11. Created now so the schema and FKs are stable; not
    populated in Phase 5 (storage_uri / body_bytes stay NULL)."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(Text, ForeignKey("resources.url", ondelete="RESTRICT"), nullable=False)
    fetch_history_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("fetch_history.id", ondelete="RESTRICT"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)
    headers_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)  # NULL until Phase 11
    body_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # NULL when external
