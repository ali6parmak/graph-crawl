"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- crawl_runs ---
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("seed_url", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_reason", sa.Text(), nullable=True),
        sa.Column("max_pages", sa.Integer(), nullable=True),
        sa.Column("max_depth", sa.Integer(), nullable=True),
        sa.Column("max_frontier_size", sa.Integer(), nullable=True),
        sa.Column("delay", sa.Float(), nullable=True),
        sa.Column("stats_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_fetched_leaf", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_not_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_gone", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_needs_auth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_backoff", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats_max_frontier_size", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_runs")),
    )

    # --- resources ---
    op.create_table(
        "resources",
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("resource_state", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("content_length", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("seed_url", sa.Text(), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fetch_outcome", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("retry_after", sa.Float(), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("url", name=op.f("pk_resources")),
    )
    op.create_index("ix_resources_state", "resources", ["resource_state"])
    op.create_index("ix_resources_discovered_at", "resources", ["discovered_at"])
    op.create_index("ix_resources_type", "resources", ["resource_type"])
    op.create_index("ix_resources_content_hash", "resources", ["content_hash"])

    # --- edges ---
    op.create_table(
        "edges",
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("rel", sa.Text(), nullable=True),
        sa.Column("raw_href", sa.Text(), nullable=True),  # resolved-absolute, pre-normalize
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovery_run_id", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_run_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_url"], ["resources.url"], name=op.f("fk_edges_source_url_resources"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_url"], ["resources.url"], name=op.f("fk_edges_target_url_resources"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["discovery_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_edges_discovery_run_id_crawl_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_edges_last_seen_run_id_crawl_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("source_url", "target_url", name=op.f("pk_edges")),
    )
    op.create_index("ix_edges_source", "edges", ["source_url"])
    op.create_index("ix_edges_target", "edges", ["target_url"])

    # --- fetch_history ---
    op.create_table(
        "fetch_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("crawl_run_id", sa.BigInteger(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("content_length", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("retry_after", sa.Float(), nullable=True),
        sa.Column("redirect_chain", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["url"], ["resources.url"], name=op.f("fk_fetch_history_url_resources"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_runs.id"],
            name=op.f("fk_fetch_history_crawl_run_id_crawl_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fetch_history")),
    )
    op.create_index("ix_fetch_history_url_time", "fetch_history", ["url", "attempted_at"])
    op.create_index("ix_fetch_history_run", "fetch_history", ["crawl_run_id"])

    # --- snapshots (reserved; Phase 11 populates) ---
    op.create_table(
        "snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fetch_history_id", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("headers_json", sa.JSON(), nullable=True),
        sa.Column("storage_uri", sa.Text(), nullable=True),
        sa.Column("body_bytes", sa.LargeBinary(), nullable=True),
        sa.ForeignKeyConstraint(
            ["url"], ["resources.url"], name=op.f("fk_snapshots_url_resources"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["fetch_history_id"],
            ["fetch_history.id"],
            name=op.f("fk_snapshots_fetch_history_id_fetch_history"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_snapshots")),
        comment="Reserved for Phase 11. Not populated in Phase 5.",
    )


def downgrade() -> None:
    op.drop_table("snapshots")
    op.drop_table("fetch_history")
    op.drop_table("edges")
    op.drop_table("resources")
    op.drop_table("crawl_runs")
