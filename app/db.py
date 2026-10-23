"""Database plumbing: engine, session factory and the declarative Base.

- The *engine* owns the connection pool to PostgreSQL.
- A *session* is a unit of work: we open one per HTTP request, do our reads and
  writes, commit, and close it (see `get_session`).
- `Base` is the parent class for every ORM model in `app/models.py`.
"""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, inspect
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


ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
LOOP1_REVISION = "0001"


def alembic_config():
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["configure_logging"] = False  # leave uvicorn's/pytest's logging alone
    return cfg


def init_db() -> None:
    """Bring the database schema up to date by running Alembic migrations.

    A database created by Loop 1 (tables made by `create_all`, no
    `alembic_version` table) is first stamped as revision 0001, which matches
    the Loop 1 schema exactly, so its data is kept and only newer migrations
    run.
    """
    from alembic import command

    cfg = alembic_config()
    names = set(inspect(engine).get_table_names())
    if "transactions" in names and "alembic_version" not in names:
        command.stamp(cfg, LOOP1_REVISION)
    command.upgrade(cfg, "head")
