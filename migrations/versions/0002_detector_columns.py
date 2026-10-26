"""Record which detector (and which version of it) scored each transaction.

Existing rows were all scored by the Loop 1 rules, so the server default
"rules" back-fills them correctly.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("detector", sa.String(32), nullable=False, server_default="rules"))
    op.add_column("transactions", sa.Column("model_version", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "model_version")
    op.drop_column("transactions", "detector")
