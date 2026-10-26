"""Alembic migrations stay in step with the ORM models and can adopt a Loop 1 database."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db import Base, alembic_config, engine, init_db


def current_revision() -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def test_migrations_match_models():
    # If this fails, a model changed without a migration (or vice versa):
    # run `alembic revision --autogenerate -m "..."` and review the result.
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_loop1_database_is_adopted_without_data_loss():
    cfg = alembic_config()
    # Recreate what Loop 1 left behind: its tables (revision 0001) holding a
    # row, and no alembic_version table because Loop 1 used create_all.
    command.downgrade(cfg, "0001")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO transactions (transaction_id, customer_id, amount, currency, timestamp, merchant,"
                " location, device_id, status, risk_score, risk_reasons, received_at) VALUES"
                " ('old', 'c1', 10, 'GBP', now(), 'Tesco', 'GB', 'd1', 'approved', 0, '[]', now())"
            )
        )
        conn.execute(text("DROP TABLE alembic_version"))

    init_db()

    assert current_revision() == "0003"
    columns = {c["name"] for c in inspect(engine).get_columns("transactions")}
    assert {"detector", "model_version"} <= columns
    with engine.connect() as conn:
        assert conn.execute(text("SELECT detector FROM transactions WHERE transaction_id = 'old'")).scalar() == "rules"
