"""add Alfred semantic embeddings

Revision ID: 20260509_0001
Revises:
Create Date: 2026-05-09 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "20260509_0001"
down_revision = None
branch_labels = None
depends_on = None


TABLES = (
    "alfred_memories",
    "alfred_lessons",
    "alfred_feedback",
    "alfred_eval_examples",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for table_name in TABLES:
        op.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding vector(1536)")

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_alfred_memories_embedding_hnsw
        ON alfred_memories USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_alfred_lessons_embedding_hnsw
        ON alfred_lessons USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_alfred_feedback_embedding_hnsw
        ON alfred_feedback USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_alfred_eval_examples_embedding_hnsw
        ON alfred_eval_examples USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_alfred_eval_examples_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_alfred_feedback_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_alfred_lessons_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_alfred_memories_embedding_hnsw")
    for table_name in reversed(TABLES):
        op.execute(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS embedding")
