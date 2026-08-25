"""Track whether a local Hoardarr account is active.

Revision ID: 0029_user_active_state
Revises: 0028_physical_disk_identity_aliases
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_user_active_state"
down_revision = "0028_physical_disk_identity_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_active")
