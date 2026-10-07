"""Compare directory snapshot queries using synthetic retained history only.

Run in the isolated harness PostgreSQL namespace with a migrated synthetic DB.
The fixture is rolled back; no image files, providers or application lifespan run.
"""
import asyncio
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter
import uuid

assert {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}
assert "@127.0.0.1:5432/iacs_validation_" in os.environ.get("IACS_DATABASE_URL", "")
assert os.environ.get("IACS_VALIDATION_MODE") == "persistence"

from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent
from app.services.directory.representation import latest_snapshot_query


async def main():
    prefix = "SYN" + uuid.uuid4().hex[:8].upper()
    registrations = [f"{prefix}{index:02}" for index in range(50)]
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                INSERT INTO access_events
                    (id, registration_number, direction, decision, confidence, source,
                     occurred_at, timing_classification, snapshot_path, snapshot_bytes,
                     created_at, updated_at)
                SELECT md5(:prefix || number::text)::uuid,
                       :prefix || lpad(((number - 1) % 50)::text, 2, '0'),
                       'ENTRY', 'GRANTED', 1.0, 'synthetic-directory-benchmark',
                       '2024-01-01 00:00:00+00'::timestamptz + number * interval '1 minute',
                       'UNKNOWN', 'synthetic/no-file.jpg', 4, now(), now()
                FROM generate_series(1, 1000000) AS number
            """), {"prefix": prefix})
            await session.execute(text("ANALYZE access_events"))
            ranked = select(AccessEvent.registration_number, AccessEvent.id.label("event_id"), func.row_number().over(partition_by=AccessEvent.registration_number, order_by=AccessEvent.occurred_at.desc()).label("rank")).where(AccessEvent.registration_number.in_(registrations), AccessEvent.snapshot_path.is_not(None), AccessEvent.snapshot_bytes.is_not(None)).subquery()
            old = select(ranked.c.registration_number, ranked.c.event_id).where(ranked.c.rank == 1)
            current = latest_snapshot_query(registrations)
            evidence = {"retained_events": 1000000, "requested_plates": 50}
            for name, query in [("previous_window_scan", old), ("current_bounded_lookups", current)]:
                samples = []
                result = []
                for _ in range(3):
                    started = perf_counter()
                    result = (await session.execute(query)).all()
                    samples.append(round((perf_counter() - started) * 1000, 2))
                sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
                plan = (await session.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql))).scalar_one()[0]
                evidence[name] = {"timings_ms": samples, "median_ms": median(samples), "returned_rows": len(result), "plan": plan}
                assert len(result) == 50
            assert set((await session.execute(old)).all()) == set((await session.execute(current)).all())
            missing = latest_snapshot_query([prefix + "UNSEEN"])
            missing_sql = str(missing.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            missing_plan = (await session.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + missing_sql))).scalar_one()[0]
            evidence["current_unseen_plate"] = {"returned_rows": len((await session.execute(missing)).all()), "plan": missing_plan}
            assert evidence["current_unseen_plate"]["returned_rows"] == 0
            print(json.dumps(evidence, indent=2, sort_keys=True))
            await session.rollback()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
