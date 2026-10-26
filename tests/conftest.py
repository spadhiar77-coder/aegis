"""Shared test fixtures.

Tests run against a real PostgreSQL database (not SQLite) so that types,
constraints and timezone handling behave exactly as in production. Point
TEST_DATABASE_URL at a throwaway database; every table is emptied between
tests. `docker compose up -d db` provides one (see README).

The schema is built by running the Alembic migrations (not `create_all`), so
every test run also proves the migrations work, and torn down by running them
backwards, which proves the downgrades work too.
"""

import os

# Must be set before `app` is imported, because app.db builds its engine at
# import time from DATABASE_URL.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://aegis:aegis@localhost:5432/aegis_test"
)

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import Base, alembic_config, engine, init_db
from app.main import app

ALL_TABLES = "incident_events, incident_transactions, incidents, transactions, alembic_version"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    with engine.begin() as conn:  # start clean even if a previous run crashed
        conn.execute(text(f"DROP TABLE IF EXISTS {ALL_TABLES} CASCADE"))
    init_db()
    yield
    command.downgrade(alembic_config(), "base")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def use_rules(monkeypatch):
    """Score with the Loop 1 rules instead of the default model."""
    monkeypatch.setenv("AEGIS_DETECTOR", "rules")


@pytest.fixture
def use_model(monkeypatch):
    monkeypatch.setenv("AEGIS_DETECTOR", "model")
