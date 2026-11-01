"""Remove command cards: delete them, then the links' kind and command columns.

Command cards launched programs on the machine running OpsDeck, which is
unsafe for a server that runs as a service. The titles of the deleted cards
are logged; a backup taken before still holds them.

Revision ID: 0002
Revises: 0001
"""

import logging

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

logger = logging.getLogger("opsdeck.migrations")


def upgrade() -> None:
    connection = op.get_bind()
    titles = [row[0] for row in connection.execute(sa.text("SELECT title FROM links WHERE kind = 'command'"))]
    if titles:
        logger.warning(
            "Removed %d command card(s), since OpsDeck no longer runs commands: %s",
            len(titles),
            ", ".join(titles),
        )
        connection.execute(sa.text("DELETE FROM links WHERE kind = 'command'"))
    with op.batch_alter_table("links") as batch:
        batch.drop_column("command")
        batch.drop_column("kind")


def downgrade() -> None:
    with op.batch_alter_table("links") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="url"))
        batch.add_column(sa.Column("command", sa.String(1024), nullable=False, server_default=""))
