"""Repeated plate lookups must retain the index on disposable synthetic data."""

from test_recovery_boundaries import isolated_resources as isolated_resources

import uuid

import pytest
from sqlalchemy import insert, text
from sqlalchemy.dialects.postgresql.asyncpg import dialect

from app.db.session import AsyncSessionLocal
from app.models import Vehicle
from app.services import access_events


@pytest.mark.asyncio
async def test_actual_plate_query_uses_index_with_generic_prepared_plan(monkeypatch):
    captured = []
    async with AsyncSessionLocal() as session:
        await session.execute(insert(Vehicle), [
            {"id": uuid.uuid4(), "registration_number": "ab-10 cde" if i == 1000 else f"PL{i:06d}",
             "is_active": i % 10 != 0 or i == 1000}
            for i in range(2000)
        ])
        await session.execute(text("ANALYZE vehicles"))

        class QuerySession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def scalars(self, statement):
                captured.append(statement)
                return await session.scalars(statement)

        monkeypatch.setattr(access_events, "AsyncSessionLocal", QuerySession)
        assert await access_events.AccessEventService()._matching_vehicle_registrations(("AB10CDE",)) == ["ab-10 cde"]
        assert len(captured) == 1
        compiled = captured[0].compile(dialect=dialect(), compile_kwargs={"render_postcompile": True})
        assert list(compiled.params.values()) == ["AB10CDE"]
        await session.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
        # SQL comes from the actual SQLAlchemy query. Only this synthetic candidate
        # is supplied to EXECUTE; no installation data or dynamic SQL identifiers.
        await session.execute(text(f"PREPARE iacs_plate_lookup (varchar) AS {compiled}"))
        try:
            result = await session.execute(text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE iacs_plate_lookup('AB10CDE')"
            ))
            plan = result.scalar_one()[0]["Plan"]

            def index_names(node):
                return [node.get("Index Name"), *(name for child in node.get("Plans", []) for name in index_names(child))]

            assert "ix_vehicles_normalized_active_plate" in index_names(plan), plan
            assert plan["Actual Rows"] == 1
        finally:
            await session.execute(text("DEALLOCATE iacs_plate_lookup"))
            await session.rollback()
