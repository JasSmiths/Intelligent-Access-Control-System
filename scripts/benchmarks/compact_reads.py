"""Retain equivalent compact-read costs in an isolated migrated scratch database.

Run this identical fixture in immutable baseline and candidate snapshots, with the
same exact dependencies/image and arguments. It never starts application services
or contacts providers. Generated images are unused synthetic data-URL strings,
not raster-content fixtures. Whole-history SQL/forecast density is intentionally
retained; tracemalloc is Python allocation, never a process-RSS guarantee.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from scripts.benchmarks.evidence import write_evidence
from sqlalchemy import event, text
from sqlalchemy.engine import make_url

from app.api.v1.events import list_presence
from app.db.session import AsyncSessionLocal, engine
from app.services.expected_presence import expected_presence_today
from app.services.leaderboard import LeaderboardService

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def assert_isolated() -> None:
    url = make_url(os.environ.get("IACS_DATABASE_URL", ""))
    if url.host != "127.0.0.1" or not (url.database or "").startswith("iacs_validation_"):
        raise RuntimeError("Compact-read evidence requires an explicitly isolated validation database")
    if {entry.name for entry in Path("/sys/class/net").iterdir()} != {"lo"}:
        raise RuntimeError("Compact-read evidence permits only the isolated loopback namespace")


async def seed(args: argparse.Namespace) -> dict[str, int | str]:
    people = args.people if args.suite != "charts" else (100 if args.events == 10_000 else 1000)
    photo_bytes = 256 * 1024 if args.fat_unused else 1024
    raw_bytes = 4096 if args.fat_unused else 64
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE people, vehicles, access_events CASCADE"))
        await session.execute(text("""
            INSERT INTO people (id,first_name,last_name,display_name,is_active,profile_photo_data_url,
                                garage_door_entity_ids,missed_exit_recovery_enabled,
                                home_assistant_presence_input_boolean_entity_ids,
                                home_assistant_presence_input_boolean_entry_action,
                                home_assistant_presence_input_boolean_exit_action,created_at,updated_at)
            SELECT md5('compact-person-'||n)::uuid,'Synthetic',n::text,'Synthetic '||lpad(n::text,6,'0'),true,
                   'data:image/png;base64,'||repeat('A',:photo_bytes),'[]'::jsonb,false,
                   '[]'::jsonb,'turn_off','turn_off',CAST(:now AS timestamptz),CAST(:now AS timestamptz)
            FROM generate_series(1,CAST(:people AS integer)) n
        """), {"photo_bytes": photo_bytes, "now": NOW, "people": people})
        await session.execute(text("""
            INSERT INTO vehicles (id,person_id,registration_number,is_active,make,model,color,vehicle_photo_data_url,information_outcome,created_at,updated_at)
            SELECT md5('compact-vehicle-'||n)::uuid,
                   CASE WHEN n%3<>0 THEN md5('compact-person-'||n)::uuid ELSE NULL END,
                   'KNOWN'||lpad(n::text,6,'0'),true,'Synthetic','Model','Blue',
                   'data:image/png;base64,'||repeat('B',:photo_bytes),
                   jsonb_build_object('unused',repeat('C',:information_bytes)),CAST(:now AS timestamptz),CAST(:now AS timestamptz)
            FROM generate_series(1,CAST(:people AS integer)) n
        """), {"photo_bytes": photo_bytes, "information_bytes": 65536 if args.fat_unused else 64, "now": NOW, "people": people})
        await session.execute(text("""
            INSERT INTO vehicle_person_assignments (vehicle_id,person_id,created_at,updated_at)
            SELECT md5('compact-vehicle-'||n)::uuid,md5('compact-person-'||n)::uuid,CAST(:now AS timestamptz),CAST(:now AS timestamptz)
            FROM generate_series(1,CAST(:people AS integer)) n WHERE n%3=0
        """), {"now": NOW, "people": people})
        if args.suite == "charts":
            await session.execute(text("""
                INSERT INTO access_events (id,vehicle_id,person_id,registration_number,direction,decision,confidence,source,timing_classification,occurred_at,created_at,updated_at,raw_payload,snapshot_path,snapshot_bytes)
                SELECT md5('compact-chart-event-'||n)::uuid,
                       CASE WHEN n%2=0 THEN md5('compact-vehicle-'||(1+(n/2)%:people))::uuid ELSE NULL END,
                       CASE WHEN n%2=0 THEN md5('compact-person-'||(1+(n/2)%:people))::uuid ELSE NULL END,
                       CASE WHEN n%2=0 THEN 'KNOWN' ELSE 'UNKNOWN' END||lpad((1+(n/2)%:people)::text,6,'0'),
                       CASE WHEN n%2=0 THEN 'ENTRY'::accessdirection ELSE 'DENIED'::accessdirection END,
                       CASE WHEN n%2=0 THEN 'GRANTED'::accessdecision ELSE 'DENIED'::accessdecision END,
                       .99,'synthetic-compact','UNKNOWN'::timingclassification,CAST(:now AS timestamptz)-(n||' seconds')::interval,CAST(:now AS timestamptz)-(n||' seconds')::interval,CAST(:now AS timestamptz),
                       jsonb_build_object('unused',repeat('D',:raw_bytes)),
                       CASE WHEN n%17=0 THEN 'synthetic/no-file-'||n||'.jpg' ELSE NULL END,
                       CASE WHEN n%34=0 THEN 100 ELSE NULL END
                FROM generate_series(1,CAST(:events AS integer)) n
            """), {"people": people, "now": NOW, "raw_bytes": raw_bytes, "events": args.events})
            count = args.events
        elif args.suite == "forecast":
            days, movements = (1, 2) if args.density == "low" else (5, 4)
            await session.execute(text("""
                INSERT INTO access_events (id,vehicle_id,person_id,registration_number,direction,decision,confidence,source,timing_classification,occurred_at,created_at,updated_at,raw_payload)
                SELECT md5('compact-forecast-'||p||'-'||w||'-'||d||'-'||m)::uuid,
                       md5('compact-vehicle-'||p)::uuid,
                       CASE WHEN p%3=1 THEN md5('compact-person-'||p)::uuid ELSE NULL END,
                       'KNOWN'||lpad(p::text,6,'0'),
                       CASE WHEN m%2=1 THEN 'ENTRY'::accessdirection ELSE 'EXIT'::accessdirection END,
                       'GRANTED'::accessdecision,.99,'synthetic-compact','UNKNOWN'::timingclassification,
                       CAST(:now AS timestamptz)-(w*7+d||' days')::interval+((m-1)*2-4||' hours')::interval,
                       CAST(:now AS timestamptz),CAST(:now AS timestamptz),jsonb_build_object('unused',repeat('D',:raw_bytes))
                FROM generate_series(1,CAST(:people AS integer)) p CROSS JOIN generate_series(0,11) w
                     CROSS JOIN generate_series(0,CAST(:days AS integer)-1) d CROSS JOIN generate_series(1,CAST(:movements AS integer)) m
            """), {"people": people, "days": days, "movements": movements, "raw_bytes": raw_bytes, "now": NOW})
            count = people * 12 * days * movements
        else:
            await session.execute(text("""
                INSERT INTO presence (person_id,state,last_changed_at,created_at,updated_at)
                SELECT md5('compact-person-'||n)::uuid,
                       CASE WHEN n%3=0 THEN 'PRESENT'::presencestate WHEN n%3=1 THEN 'EXITED'::presencestate ELSE 'UNKNOWN'::presencestate END,
                       CAST(:now AS timestamptz)-(n||' seconds')::interval,CAST(:now AS timestamptz),CAST(:now AS timestamptz)-(n||' seconds')::interval
                FROM generate_series(1,CAST(:people AS integer)) n
            """), {"now": NOW, "people": people})
            count = 0
        await session.commit()
        for table in ("people", "vehicles", "vehicle_person_assignments", "access_events", "presence"):
            await session.execute(text(f"ANALYZE {table}"))
        await session.commit()
    return {"people": people, "events": count, "density": args.density,
            "unused_photo_string_bytes_each": photo_bytes, "unused_raw_string_bytes_each": raw_bytes,
            "chart_limit": 25, "snapshot_files": "deliberately absent; latest metadata query still executes"}


async def operation(suite: str):
    if suite == "charts":
        result = await LeaderboardService().get_leaderboard(limit=25, enrich_unknowns=False)
        return {key: value for key, value in result.items() if key != "generated_at"}
    if suite == "presence":
        return await list_presence()
    async with AsyncSessionLocal() as session:
        return await expected_presence_today(session, timezone_name="Europe/London", now=NOW)


def parameter_value(value):
    if isinstance(value, (tuple, list)):
        return [parameter_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): parameter_value(item) for key, item in value.items()}
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value if value is None or isinstance(value, (str, int, float, bool)) else str(value)}


async def run(args: argparse.Namespace) -> None:
    assert_isolated()
    captured = []
    def before(_connection, _cursor, statement, parameters, _context, _many):
        captured.append((statement, parameters))
    try:
        workload = await seed(args)
        await operation(args.suite)  # Warm pool/compiled structures, outside all samples.
        samples = []
        output = None
        first_queries = []
        event.listen(engine.sync_engine, "before_cursor_execute", before)
        try:
            for index in range(3):
                gc.collect()
                captured.clear()
                tracemalloc.start()
                started = perf_counter()
                result = await operation(args.suite)
                seconds = perf_counter() - started
                _current, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                if index == 0:
                    first_queries = list(captured)
                    output = result
                else:
                    assert result == output, "Repeated fixture output changed"
                samples.append({"seconds": seconds, "python_peak_bytes": peak, "queries": len(captured)})
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before)
        plans = []
        async with AsyncSessionLocal() as session:
            connection = await session.connection()
            for sql, parameters in first_queries:
                if not sql.lstrip().upper().startswith("SELECT"):
                    continue
                # Execute the ACTUAL driver SQL/tuple; never recompile cached SQL with new values.
                plan = await connection.run_sync(lambda sync, sql=sql, parameters=parameters:
                    sync.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, parameters).scalar_one())
                if isinstance(plan, str):
                    plan = json.loads(plan)
                actual_rows = int(plan[0]["Plan"]["Actual Rows"])
                if args.suite == "charts":
                    assert actual_rows == 25, (actual_rows, sql)
                if args.suite == "forecast" and "from access_events" in sql.lower():
                    expected_rows = int(workload["events"]) - (int(workload["people"]) if args.density == "high" else 0)
                    assert actual_rows == expected_rows, (actual_rows, expected_rows, sql)
                plans.append({"sql": sql, "executed_parameters": parameter_value(parameters),
                              "actual_root_rows": actual_rows, "plan": plan})
        # Compare the entire public response, including all unknown-row keys;
        # a renamed latest_snapshot must fail even when every image file is absent.
        public_keys = None
        if args.suite == "charts":
            public_keys = {"response": sorted(output),
                           "known": [sorted(row) for row in output["known"]],
                           "unknown": [sorted(row) for row in output["unknown"]]}
            assert all("latest_snapshot" in row and "snapshot" not in row for row in output["unknown"])
        encoded = json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".output.json").write_bytes(encoded)
        write_evidence(path, {"phase": args.phase, "suite": args.suite, "fixed_now": NOW.isoformat(),
            "fixture_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "workload": workload, "samples": samples, "queries_and_plans": plans,
            "output_bytes": len(encoded), "output_sha256": hashlib.sha256(encoded).hexdigest(),
            "public_response_keys": public_keys,
            "limits": "Python allocation only; same supported public output, generated_at removed for charts only. Full-history chart aggregation and population/density-dependent prediction/public presence remain. Source SQL plans are actual driver statement+parameters."})
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("charts", "presence", "forecast"), required=True)
    parser.add_argument("--phase", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--events", type=int, choices=(10_000, 1_000_000), default=10_000)
    parser.add_argument("--people", type=int, choices=(20, 2000), default=20)
    parser.add_argument("--density", choices=("low", "high"), default="low")
    parser.add_argument("--fat-unused", action="store_true")
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    if arguments.fat_unused and (arguments.events == 1_000_000 or arguments.people == 2000):
        parser.error("Fat-unused-column experiment is deliberately small, not a baseline OOM stress")
    asyncio.run(run(arguments))
