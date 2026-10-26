"""Application settings, read from environment variables so the same code runs
locally, in Docker and in tests without edits.

    DATABASE_URL     where PostgreSQL lives
    AEGIS_DETECTOR   "model" (default, Loop 2 logistic regression) or
                     "rules" (Loop 1 hand-weighted rules, kept as a fallback)
    AEGIS_DEMO       "1" (default) shows the demo controls in the web app:
                     load simulated data, advance the feed, answer key.
                     "0" hides them; use that wherever real data lives,
                     because "Restart demo" deletes every row.
"""

import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://aegis:aegis@localhost:5432/aegis"
DETECTORS = ("model", "rules")


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    # Hosting providers (Render, Heroku, Railway) hand out postgres:// or
    # postgresql:// URLs; SQLAlchemy needs to be told to use psycopg 3.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def demo_enabled() -> bool:
    return os.environ.get("AEGIS_DEMO", "1").strip().lower() not in {"0", "false", "no", "off"}


def get_detector_name() -> str:
    name = os.environ.get("AEGIS_DETECTOR", "model").strip().lower()
    if name not in DETECTORS:
        raise ValueError(f"AEGIS_DETECTOR must be one of {DETECTORS}, got {name!r}")
    return name
