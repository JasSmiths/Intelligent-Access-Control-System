"""Original schema baseline, frozen from the introducing source revision.

Revision ID: 20260531_0000
Revises:
Create Date: 2026-05-31 00:00:00.000000
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from alembic import op
from sqlalchemy import inspect


revision = "20260531_0000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _execute_frozen("upgrade")


def downgrade() -> None:
    _execute_frozen("downgrade")


def _execute_frozen(direction: str) -> None:
    """Retain this revision's former checkfirst behavior without live ORM input.

    Existing tables are not altered by the baseline; subsequent revisions own
    upgrades. Only objects named in the immutable baseline are created/dropped.
    This is migration-local compatibility, never application startup bootstrap.
    """
    artifact = Path(__file__).parent.parent / "schema" / "20260531_0000.json"
    statements = json.loads(artifact.read_text())[direction]
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    enums = {item["name"] for item in inspector.get_enums()}
    for statement in statements:
        if direction == "upgrade":
            match = re.match(r"CREATE TABLE (\w+)\s", statement)
            if match and match[1] in tables:
                continue
            match = re.match(r"CREATE (?:UNIQUE )?INDEX \w+ ON (\w+)\s", statement)
            if match and match[1] in tables:
                continue
            match = re.match(r"ALTER TABLE (\w+) ADD ", statement)
            if match and match[1] in tables:
                continue
            match = re.match(r"CREATE TYPE (\w+) AS ENUM", statement)
            if match and match[1] in enums:
                continue
        else:
            match = re.match(r"DROP TABLE (\w+)$", statement)
            if match and match[1] not in tables:
                continue
            match = re.match(r"DROP TYPE (\w+)$", statement)
            if match and match[1] not in enums:
                continue
            match = re.match(r"ALTER TABLE (\w+) DROP CONSTRAINT (\w+)$", statement)
            if match:
                if match[1] not in tables:
                    continue
                if match[2] not in {
                    fk["name"] for fk in inspector.get_foreign_keys(match[1])
                }:
                    continue
        op.execute(statement)
