"""Repeatable access/auth read and matching workloads in the isolated DB only."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import statistics
import time
import tracemalloc
import uuid

import pytest
from sqlalchemy import event as sql_event, literal

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, Person, Presence, User, Vehicle
from app.models.enums import AccessDecision, AccessDirection, PresenceState, UserRole
from app.modules.lpr.base import PlateRead
from app.services.access.evidence import AccessEvidenceResolver
from app.services.access.reads import GATE_OBSERVATION_PAYLOAD_KEY
from app.services.access.plate_matching import match_candidates
from app.services.access_events import AccessEventService
from app.services.auth import _encode_jwt, authenticate_token, serialize_user
from app.services.directory.reads import list_people
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, runtime_config_from_values
from scripts.benchmarks.evidence import write_evidence
from app.main import auth_guard
from starlette.requests import Request

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
WARMUPS, SAMPLES = 2, 7
BLOB = "data:image/jpeg;base64," + "A"*65536


class Trace:
    trace_id = "synthetic-efficiency"
    def start_span(self, *_, **__):
        return self
    def finish(self, *_, **__):
        pass


def materialization(session):
    photos = payloads = 0
    for row in session.identity_map.values():
        for field in ("profile_photo_data_url", "vehicle_photo_data_url"):
            value = row.__dict__.get(field)
            if isinstance(value, str):
                photos += len(value.encode())
        payload = row.__dict__.get("raw_payload")
        if isinstance(payload, dict):
            payloads += len(str(payload.get("unused") or "").encode())
    return {"orm_instances": len(session.identity_map), "unused_photo_scalar_utf8_bytes": photos,
        "unused_payload_scalar_utf8_bytes": payloads}


async def measure(operation):
    for _ in range(WARMUPS):
        await operation()
    elapsed, peaks, counts, outputs = [], [], [], []
    captured = []
    def capture(_connection, _cursor, sql, parameters, _context, _many):
        captured.append((sql, parameters))
    sql_event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        for _ in range(SAMPLES):
            captured.clear()
            tracemalloc.start()
            started = time.perf_counter()
            output = await operation()
            elapsed.append((time.perf_counter()-started)*1000)
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
            counts.append(len(captured))
            outputs.append(output)
        statements = list(captured)
    finally:
        sql_event.remove(engine.sync_engine, "before_cursor_execute", capture)
        if tracemalloc.is_tracing():
            tracemalloc.stop()
    assert all(output == outputs[0] for output in outputs)
    return {"output": outputs[0], "p50_ms": statistics.median(elapsed),
        "p95_ms": statistics.quantiles(elapsed, n=20, method="inclusive")[18],
        "samples_ms": elapsed, "query_counts": counts, "python_peak_bytes": peaks,
        "json_utf8_bytes": len(json.dumps(outputs[0], sort_keys=True).encode())}, statements


async def plans(statements):
    retained = []
    async with engine.connect() as connection:
        for index, (sql, parameters) in enumerate(statements):
            if not sql.lstrip().upper().startswith("SELECT"):
                continue
            actual = await connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, parameters)
            name = f"iacs_efficiency_{uuid.uuid4().hex}"
            await connection.exec_driver_sql("SET LOCAL plan_cache_mode = force_generic_plan")
            await connection.exec_driver_sql(f"PREPARE {name} AS {sql}")
            try:
                supplied = ",".join(str(literal(str(value) if isinstance(value, uuid.UUID) else value).compile(dialect=engine.dialect,
                    compile_kwargs={"literal_binds": True})) for value in parameters)
                arguments = f"({supplied})" if parameters else ""
                generic = await connection.exec_driver_sql(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) EXECUTE {name}{arguments}")
                retained.append({"sql": sql, "parameters": [str(value) for value in parameters],
                    "actual_plan": actual.scalar_one(), "generic_plan": generic.scalar_one()})
            except BaseException:
                await connection.rollback()
                raise
            finally:
                await connection.exec_driver_sql(f"DEALLOCATE {name}")
        await connection.rollback()
    return retained


@pytest.mark.parametrize("size", [16, 2000])
async def test_access_auth_and_matching_workload(isolated_resources, tmp_path, size):
    runtime = runtime_config_from_values({key: default for key, (_, default, _) in DEFAULT_DYNAMIC_SETTINGS.items()})
    async with AsyncSessionLocal() as session:
        people = [Person(id=uuid.UUID(int=i+1000), first_name="Synthetic", last_name=str(i), display_name=f"Synthetic {i}",
            profile_photo_data_url=BLOB, created_at=NOW, updated_at=NOW) for i in range(size//2)]
        session.add_all(people)
        await session.flush()
        vehicles = [Vehicle(id=uuid.UUID(int=i+10000), person_id=people[i//2].id, registration_number=f"AA{i:04}Z",
            vehicle_photo_data_url=BLOB, created_at=NOW, updated_at=NOW) for i in range(size)]
        session.add_all(vehicles)
        await session.flush()
        session.add_all([Presence(person_id=person.id, state=PresenceState.EXITED) for person in people])
        session.add_all([AccessEvent(id=uuid.UUID(int=i+100000), person_id=vehicle.person_id, vehicle_id=vehicle.id,
            registration_number=vehicle.registration_number, direction=AccessDirection.EXIT, decision=AccessDecision.GRANTED,
            confidence=1, source="synthetic-live", occurred_at=NOW-timedelta(minutes=i+1), raw_payload={"unused": "x"*65536})
            for i, vehicle in enumerate(vehicles)])
        user = User(id=uuid.UUID(int=900), username="synthetic-efficiency", full_name="Synthetic User", password_hash="unused",
            profile_photo_data_url=BLOB, role=UserRole.ADMIN, is_active=True, auth_session_version=1, created_at=NOW, updated_at=NOW)
        session.add(user)
        await session.commit()
    token = _encode_jwt({"sub": str(user.id), "ver": 1, "exp": int(datetime.now(UTC).timestamp())+3600, "jti": "synthetic-efficiency"})
    resolver, worker = AccessEvidenceResolver(runtime), AccessEventService()
    known = vehicles[0].registration_number
    def read(plate=known, gate="open"):
        return PlateRead(plate, 1, "synthetic-live", NOW, {GATE_OBSERVATION_PAYLOAD_KEY: {"state": gate}})
    async def lookup(plate=known):
        async with AsyncSessionLocal() as session:
            row = await resolver._lookup_active_vehicle(session, read(plate), Trace())
            return {"identity": (str(row.id), str(row.person_id), row.owner.display_name) if row else None,
                **materialization(session)}
    async def probe(gate):
        result = await resolver.prepare_camera_evidence(read(gate=gate), read(gate=gate), None, Trace())
        assert result is None
        return {"camera_required": False}
    async def authentication():
        async with AsyncSessionLocal() as session:
            row = await authenticate_token(session, token)
            return {"user": serialize_user(row), **materialization(session)}
    async def middleware_authentication():
        request = Request({"type": "http", "method": "GET", "path": "/api/v1/settings", "headers": [(b"authorization", f"Bearer {token}".encode())], "query_string": b""})
        async def next_request(current):
            row = current.state.user
            return {"user": serialize_user(row), "unused_photo_scalar_utf8_bytes": len((row.__dict__.get("profile_photo_data_url") or "").encode())}
        return await auth_guard(request, next_request)
    async def predecessor():
        async with AsyncSessionLocal() as session:
            row = await resolver._latest_live_person_or_vehicle_event(session, person=people[0], vehicle=vehicles[0], before=NOW)
            return {"id": str(row.id), "direction": row.direction.value, **materialization(session)}
    async def matching(plate):
        registrations = await worker._matching_vehicle_registrations((plate,))
        return {"match": match_candidates((plate,), registrations, runtime.lpr_similarity_threshold),
            "registration_rows": len(registrations), "registration_scalar_utf8_bytes": sum(len(item.encode()) for item in registrations)}
    async def ui_read():
        async with AsyncSessionLocal() as session:
            page = await list_people(session, limit=25)
            return page.model_dump(mode="json")
    async def concurrent_ui():
        return await asyncio.gather(*(ui_read() for _ in range(4)))
    operations = {"known_photo_identity": lambda: lookup(), "unknown_identity": lambda: lookup("ZZZZZZZ"),
        "no_camera_open_probe": lambda: probe("open"), "no_camera_closed_absent_probe": lambda: probe("closed"),
        "authentication": authentication, "middleware_authentication": middleware_authentication, "live_predecessor_person_or_vehicle": predecessor,
        "exact_matching": lambda: matching(known), "unknown_fuzzy_matching": lambda: matching("ZZZZZZZ"),
        "ocr_fuzzy_matching": lambda: matching("A00000Z"), "concurrent_ui_reads": concurrent_ui}
    measurements, query_plans = {}, {}
    for name, operation in operations.items():
        measurements[name], queries = await measure(operation)
        if name in {"known_photo_identity", "live_predecessor_person_or_vehicle", "exact_matching", "unknown_fuzzy_matching"}:
            query_plans[name] = await plans(queries)
    artifact = Path(tmp_path)/f"access-efficiency-{size}.json"
    write_evidence(artifact, {"dataset": {"people": size//2, "vehicles": size, "events": size, "photo_scalar_bytes_each": len(BLOB.encode())},
        "warmups": WARMUPS, "samples": SAMPLES, "concurrent_ui_reads": 4, "measurements": measurements, "query_plans": query_plans,
        "limits": "Synthetic read/substep timings with tracemalloc enabled; seven samples give coarse p95. Scalar UTF-8 and JSON sizes are proxies, not database wire bytes/RSS. Concurrent UI reads do not parallelize access processing."})
    if size == 2000:
        def nodes(value):
            if isinstance(value, dict):
                if "Node Type" in value:
                    yield value
                for child in value.values():
                    yield from nodes(child)
            elif isinstance(value, list):
                for child in value:
                    yield from nodes(child)
        for query in query_plans["live_predecessor_person_or_vehicle"]:
            for kind in ("actual_plan", "generic_plan"):
                seeks = [node for node in nodes(query[kind])
                    if str(node.get("Index Name", "")).startswith("ix_access_events_live_")]
                assert len(seeks) == 2
                assert all(node["Actual Rows"] <= 1 and node.get("Rows Removed by Filter", 0) == 0 for node in seeks)
                conditions = [node.get("Index Cond", "") for node in seeks]
                assert any("person_id" in condition and "occurred_at" in condition for condition in conditions)
                assert any("vehicle_id" in condition and "occurred_at" in condition for condition in conditions)
    print("ACCESS_EFFICIENCY_ARTIFACT", artifact)
