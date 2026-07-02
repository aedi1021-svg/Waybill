"""initial schema: shipments, exceptions, resolutions, resolution_actions

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("tracking_number", sa.String(32), index=True),
        sa.Column("carrier", sa.String(64)),
        sa.Column("origin", sa.String(64)),
        sa.Column("destination", sa.String(64)),
        sa.Column("status", sa.String(48), server_default="in_transit"),
        sa.Column("value_usd", sa.Float, server_default="0"),
        sa.Column("customer", sa.String(128), server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "exceptions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("shipment_id", sa.String(40), sa.ForeignKey("shipments.id"), index=True),
        sa.Column("tracking_number", sa.String(32), index=True),
        sa.Column("carrier", sa.String(64)),
        sa.Column("raw_message", sa.Text),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("true_type", sa.String(32), nullable=True),
        sa.Column("true_severity", sa.String(16), nullable=True),
    )

    op.create_table(
        "resolutions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("exception_id", sa.String(40), sa.ForeignKey("exceptions.id"), index=True),
        sa.Column("exception_type", sa.String(32)),
        sa.Column("severity", sa.String(16)),
        sa.Column("confidence", sa.Float),
        sa.Column("disposition", sa.String(24), index=True),
        sa.Column("rationale", sa.Text, server_default=""),
        sa.Column("notes", sa.Text, server_default=""),
        sa.Column("latency_ms", sa.Integer, server_default="0"),
        sa.Column("model_name", sa.String(64), server_default=""),
        sa.Column("decided_at", sa.DateTime(timezone=True), index=True),
        sa.Column("human_override_type", sa.String(32), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "resolution_actions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("resolution_id", sa.String(40), sa.ForeignKey("resolutions.id"), index=True),
        sa.Column("kind", sa.String(48)),
        sa.Column("summary", sa.Text),
        sa.Column("payload", sa.JSON),
    )


def downgrade() -> None:
    op.drop_table("resolution_actions")
    op.drop_table("resolutions")
    op.drop_table("exceptions")
    op.drop_table("shipments")
