"""Compare historical report-state reads with bounded predecessors in a scratch DB.

Run only in an isolated synthetic database named iacs_validation_reports_benchmark.
The table deliberately stores only projected movement columns. It therefore does
not count the old ORM/media/payload overhead, PDF rendering, or timeline output.
No provider I/O, migrations, application lifespan, or production records are used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
import tracemalloc
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models import AccessEvent
from app.models.enums import AccessDirection
from app.services.report_durations import (
    MovementPoint,
    build_duration_lookup,
    format_duration_info,
    is_movement_event,
    normalize_plate,
)
from app.services.reports import _MOVEMENT_COLUMNS, _load_duration_predecessors


def legacy_duration_lookup(events: list[MovementPoint], history: list[MovementPoint]) -> dict[str, Any]:
    """Frozen pre-refactor strict-before policy for result comparison."""
    result = {}
    history = sorted((event for event in history if is_movement_event(event)), key=lambda event: event.occurred_at)
    for event in events:
        if not is_movement_event(event):
            result[str(event.id)] = {"label": "N/A", "tone": "muted"}
            continue
        before = [prior for prior in history if prior.occurred_at < event.occurred_at]
        entry = event.direction == AccessDirection.ENTRY
        if entry:
            before = [prior for prior in before if normalize_plate(prior.registration_number) == normalize_plate(event.registration_number)]
        arrival = next((prior for prior in reversed(before) if prior.direction == AccessDirection.ENTRY), None)
        departure = next((prior for prior in reversed(before) if prior.direction == AccessDirection.EXIT), None)
        if arrival is None:
            result[str(event.id)] = {"label": "New Arrival", "tone": "new"} if entry else {"label": "No arrival found", "tone": "muted"}
        elif departure is not None and departure.occurred_at > arrival.occurred_at:
            result[str(event.id)] = format_duration_info(departure.occurred_at, event.occurred_at, "Time since this vehicle was last on site", timezone=ZoneInfo("UTC")) if entry else {"label": "No active visit", "tone": "muted"}
        else:
            result[str(event.id)] = {"label": "No prior departure", "tone": "muted"} if entry else format_duration_info(arrival.occurred_at, event.occurred_at, "Time on site since last arrival", timezone=ZoneInfo("UTC"))
    return result


async def sample(operation: Callable[..., Any]) -> tuple[Any, dict[str, float]]:
    tracemalloc.start()
    started = time.perf_counter()
    output = await operation()
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return output, {"seconds": elapsed, "python_peak_bytes": peak}


async def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    url = make_url(os.environ["IACS_DATABASE_URL"])
    if url.database != "iacs_validation_reports_benchmark" or os.environ.get("IACS_VALIDATION_MODE") != "db-free":
        raise RuntimeError("Refusing anything except the dedicated synthetic report benchmark database")
    engine = create_async_engine(url)
    epoch = datetime(2020, 1, 1, tzinfo=UTC)
    try:
        async with engine.begin() as connection:
            # Refuse existing objects: reruns need a fresh disposable DB.
            await connection.execute(text("CREATE TYPE accessdecision AS ENUM ('GRANTED','DENIED')"))
            await connection.execute(text("CREATE TYPE accessdirection AS ENUM ('ENTRY','EXIT','DENIED')"))
            await connection.execute(text("""CREATE TABLE access_events (
                id uuid PRIMARY KEY, person_id uuid, vehicle_id uuid,
                registration_number varchar(32) NOT NULL, direction accessdirection NOT NULL,
                decision accessdecision NOT NULL, occurred_at timestamptz NOT NULL
            )"""))
            await connection.execute(text("""INSERT INTO access_events
                SELECT md5(value::text)::uuid,
                    md5(mod(value-1,:people)::text)::uuid,
                    md5(mod(value-1,(:people*2))::text)::uuid,
                    'SYNTH' || lpad(mod(value-1,(:people*2))::text, 5, '0'),
                    CASE WHEN mod((value-1)/(:people*2),2)=0 THEN 'ENTRY' ELSE 'EXIT' END::accessdirection,
                    'GRANTED'::accessdecision, timestamptz '2020-01-01 UTC' + value * interval '1 minute'
                FROM generate_series(1,:rows) AS value"""), {"rows": args.rows, "people": args.people})
            # These existing model indexes are represented, rather than adding
            # a benchmark-only index unavailable to the application.
            for statement in (
                "CREATE INDEX ON access_events (person_id)",
                "CREATE INDEX ON access_events (vehicle_id)",
                "CREATE INDEX ON access_events (registration_number)",
                "CREATE INDEX ON access_events (occurred_at)",
                "CREATE INDEX ON access_events (person_id,direction,occurred_at DESC) WHERE decision='GRANTED' AND person_id IS NOT NULL AND direction IN ('ENTRY','EXIT')",
                "CREATE INDEX ON access_events (vehicle_id,direction,occurred_at DESC) WHERE decision='GRANTED' AND vehicle_id IS NOT NULL AND direction IN ('ENTRY','EXIT')",
            ):
                await connection.execute(text(statement))
            await connection.execute(text("ANALYZE access_events"))
        subject = uuid.UUID("cfcd2084-95d5-65ef-66e7-dff9f98764da")  # md5('0')
        end = epoch + timedelta(minutes=args.rows)
        start = epoch + timedelta(minutes=max(0, args.rows - args.people * args.report_events))
        selected_filter = AccessEvent.person_id == subject
        async with AsyncSession(engine) as session:
            period_query = (select(*_MOVEMENT_COLUMNS).where(selected_filter,
                AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
                .order_by(AccessEvent.occurred_at))
            events = [MovementPoint(*row) for row in await session.execute(period_query)]
            plates = {normalize_plate(event.registration_number) for event in events if event.direction == AccessDirection.ENTRY}
            history_query = (select(*_MOVEMENT_COLUMNS).where(selected_filter, AccessEvent.occurred_at <= end)
                .order_by(AccessEvent.occurred_at))

            async def baseline():
                history = [MovementPoint(*row) for row in await session.execute(history_query)]
                return legacy_duration_lookup(events, history), len(history)

            async def current():
                predecessors = await _load_duration_predecessors(session, selected_filter=selected_filter, period_start=start, plates=plates)
                return build_duration_lookup(events, [*predecessors, *events], timezone=ZoneInfo("UTC")), len(predecessors)

            # Warm both paths before alternating repetitions.
            old, old_rows = await baseline()
            current_statements = []
            def capture(_connection, _cursor, _statement, _parameters, context, _executemany):
                current_statements.append((_statement, _parameters))
            sqlalchemy_event.listen(engine.sync_engine, "before_cursor_execute", capture)
            new, new_rows = await current()
            sqlalchemy_event.remove(engine.sync_engine, "before_cursor_execute", capture)
            assert old == new, "Duration policy changed"
            old_samples, new_samples = [], []
            for _ in range(args.repetitions):
                (old, old_rows), old_sample = await sample(baseline)
                (new, new_rows), new_sample = await sample(current)
                assert old == new, "Duration policy changed"
                old_samples.append(old_sample)
                new_samples.append(new_sample)
            def summary(samples):
                return {"median_seconds": statistics.median(sample["seconds"] for sample in samples),
                        "maximum_python_peak_bytes": max(sample["python_peak_bytes"] for sample in samples),
                        "samples": samples}
            plan = await session.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + str(history_query.compile(engine.sync_engine, compile_kwargs={"literal_binds": True}))))
            current_plans = []
            connection = await session.connection()
            for statement, parameters in current_statements:
                query_plan = await connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters)
                current_plans.append([row[0] for row in query_plan])
            return {"dataset": {"retained_movements": args.rows, "people": args.people, "vehicles": args.people * 2,
                    "selected_period_events": len(events)}, "duration_outputs_equal": old == new,
                    "baseline": {"query_count": 1, "returned_history_rows": old_rows, **summary(old_samples)},
                    "current": {"query_count": len(current_statements), "returned_predecessor_rows": new_rows, **summary(new_samples)},
                    "duration_payload_bytes": len(json.dumps(new).encode()), "current_query_plans": current_plans,
                    "baseline_query_plan": [row[0] for row in plan],
                    "limits": "Projected predecessor-read and duration calculation only; excludes old ORM/payload overhead, timeline output and PDF rendering."}
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--people", type=int, default=1_000)
    parser.add_argument("--report-events", type=int, default=50)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.rows, args.people, args.report_events, args.repetitions) < 1:
        parser.error("All dataset sizes and repetition counts must be positive")
    result = asyncio.run(benchmark(args))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "dataset": result["dataset"], "duration_outputs_equal": result["duration_outputs_equal"]}))


if __name__ == "__main__":
    main()
