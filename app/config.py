"""Application settings, read from environment variables so the same code runs
locally, in Docker and in tests without edits.

    DATABASE_URL     where PostgreSQL lives
    AEGIS_DETECTOR   "model" (default, Loop 2 logistic regression) or
                     "rules" (Loop 1 hand-weighted rules, kept as a fallback)
"""

import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://aegis:aegis@localhost:5432/aegis"
DETECTORS = ("model", "rules")


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_detector_name() -> str:
    name = os.environ.get("AEGIS_DETECTOR", "model").strip().lower()
    if name not in DETECTORS:
        raise ValueError(f"AEGIS_DETECTOR must be one of {DETECTORS}, got {name!r}")
    return name
