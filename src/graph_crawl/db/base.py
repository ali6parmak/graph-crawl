"""Declarative base + naming conventions for autogenerate-friendly migrations.

Explicit naming conventions make Alembic autogenerate produce stable, reviewable
constraint/index names instead of dialect-dependent defaults (which drift across
revisions)."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase
from typing import ClassVar

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)
