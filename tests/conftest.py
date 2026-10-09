"""Shared test fixtures.

Tests run against a real PostgreSQL database (not SQLite) so that types,
constraints and timezone handling behave exactly as in production. Point
TEST_DATABASE_URL at a throwaway database; every table is emptied between
tests. `docker compose up -d db` provides one (see README).
"""

import os

# Must be set before `app` is imported, because app.db builds its engine at
# import time from DATABASE_URL.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://aegis:aegis@localhost:5432/aegis_test"
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import Base, engine, init_db
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _schema():
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


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
