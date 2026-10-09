"""Application settings.

Loop 1 has exactly one setting: where the database lives. It is read from the
DATABASE_URL environment variable so the same code runs locally, in Docker and
in tests without edits.
"""

import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://aegis:aegis@localhost:5432/aegis"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
