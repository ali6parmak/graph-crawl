"""Async engine + session factory.

One engine per process; sessions are short-lived (one per write-through call).
The engine uses asyncpg via SQLAlchemy's async API."""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from graph_crawl.db.base import Base


def create_engine(dsn: str, **engine_kwargs) -> AsyncEngine:
    """Create an async engine for the given Postgres DSN.

    ``dsn`` must be an asyncpg-style URL, e.g.
    ``postgresql+asyncpg://user:pass@host/db``.
    """
    return create_async_engine(dsn, future=True, **engine_kwargs)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build an async sessionmaker bound to ``engine``.

    ``expire_on_commit=False`` keeps loaded attributes usable after commit,
    which matters because the sink commits once per record_* call.
    """
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_schema(engine: AsyncEngine) -> None:
    """Create all tables. For dev/test convenience only — production uses Alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema(engine: AsyncEngine) -> None:
    """Drop all tables. Test fixture helper."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
