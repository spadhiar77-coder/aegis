"""Database plumbing: engine, session factory and the declarative Base.

- The *engine* owns the connection pool to PostgreSQL.
- A *session* is a unit of work: we open one per HTTP request, do our reads and
  writes, commit, and close it (see `get_session`).
- `Base` is the parent class for every ORM model in `app/models.py`.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_database_url


class Base(DeclarativeBase):
    pass


engine = create_engine(get_database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session and always closes it afterwards."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create tables that don't exist yet.

    Loop 1 shortcut: no migrations tool. If a model changes, drop the tables
    (or the database) and restart. Alembic is planned for when the schema
    starts evolving with real data in it.
    """
    from app import models  # noqa: F401  (import registers the models on Base)

    Base.metadata.create_all(bind=engine)
