"""Alembic environment: always run from migrations.py, which hands over an
open connection (no alembic.ini, no URL here)."""

from alembic import context

from app.infrastructure.persistence.sql.tables import metadata

context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=metadata,
    render_as_batch=True,  # SQLite rebuilds a table to alter it
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
