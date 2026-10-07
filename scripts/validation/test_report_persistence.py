"""Bounded report SQL/source authority; disposable PostgreSQL and inert renderer."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import json
import os
import resource
import tracemalloc
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, perf_counter

import pytest
import pytest_asyncio
from sqlalchemy import event, func, insert, select, text, update

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, Anomaly, AuditLog, Person, ReportExport, SystemSetting, User, Vehicle
from app.models.enums import AccessDecision, AccessDirection, AnomalySeverity, AnomalyType, TimingClassification, UserRole
from app.services import auth, report_exports, report_reads, reports
from app.services.report_contracts import ReportAuthority, ReportAuthorityError, ReportLimitError, ReportRequest, ReportStoppedError, ReportTimeoutError, ReportWork

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
OPTIONS = {"include_denied": True, "include_snapshots": False, "include_confidence": True}


@pytest_asyncio.fixture(autouse=True)
async def report_resources(isolated_resources):
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE system_settings CASCADE"))
        await session.commit()


async def subject():
    person = Person(id=uuid.uuid4(), first_name="Synthetic", last_name="Report", display_name="Synthetic Report")
    async with AsyncSessionLocal() as session:
        session.add(person)
        await session.commit()
    return person.id


def observation(person_id, index, *, denied=False, at=None, **values):
    return {"id": uuid.UUID(int=index + 1), "person_id": person_id, "registration_number": f"SYN{index:08d}",
            "direction": AccessDirection.ENTRY, "decision": AccessDecision.DENIED if denied else AccessDecision.GRANTED,
            "confidence": .9, "source": "synthetic", "occurred_at": at or NOW,
            "timing_classification": TimingClassification.UNKNOWN,
            "raw_payload": {"unused_candidate_blob": "x" * 4096, "visitor_pass": {"id": "historical", "visitor_name": "Retained", "mode": "duration"}}, **values}


async def save_events(rows):
    async with AsyncSessionLocal() as session:
        await session.execute(insert(AccessEvent), rows)
        await session.commit()


async def preview(person_id):
    async with AsyncSessionLocal() as session:
        return await reports.preview_person_movement_report(
            session, person_id=person_id, period_start=NOW - timedelta(minutes=1), period_end=NOW + timedelta(minutes=1), **OPTIONS,
        )


async def test_compact_projection_exact_membership_duplicate_or_denied_and_anomaly_count():
    person_id = await subject()
    vehicle_id, assigned_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add_all([Vehicle(id=vehicle_id, person_id=person_id, registration_number="ab-12 cde", is_active=False,
                                 vehicle_photo_data_url="data:image/png;base64," + "z" * 1_000_000),
                         Vehicle(id=assigned_id, registration_number="ASSIGNED")])
        await session.commit()
    rows = [observation(person_id, 0, vehicle_id=vehicle_id, registration_number="AB12CDE"),
            observation(None, 1, vehicle_id=vehicle_id), observation(None, 2, registration_number="AB12CDE"),
            observation(None, 3, registration_number="ab-12 cde"), observation(None, 4, vehicle_id=assigned_id),
            observation(person_id, 5, denied=True)]
    await save_events(rows)
    async with AsyncSessionLocal() as session:
        session.add_all([Anomaly(event_id=rows[0]["id"], anomaly_type=AnomalyType.DUPLICATE_ENTRY,
                                severity=AnomalySeverity.INFO, message="synthetic", context={"unused": "z" * 100_000}) for _ in range(2)])
        await session.commit()
    queries = []
    def capture(_connection, _cursor, statement, _parameters, context, _many):
        compiled = context.compiled
        if compiled is not None and getattr(compiled.statement, "is_select", False):
            queries.append((statement, list(compiled.statement.selected_columns)))
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with AsyncSessionLocal() as session:
            async with report_reads.report_read_transaction(session):
                person = await report_reads.load_report_person(session, person_id)
                values = await report_reads.load_report_events(session, report_reads.person_event_filter(person_id), include_denied=False)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert [value.id for value in values] == [rows[i]["id"] for i in (0, 1, 2)]
    assert values[0].anomaly_count == 2 and values[0].visitor_name == "Retained"
    assert person.vehicles[0].vehicle_photo_url.startswith("/api/v1/vehicles/")
    assert not any(column.compare(AccessEvent.raw_payload) for _, columns in queries for column in columns)
    assert not any(column.compare(Vehicle.vehicle_photo_data_url) for _, columns in queries for column in columns)
    assert not any("anomalies.context" in sql or "anomalies.message" in sql for sql, _ in queries)
    assert any("LIMIT" in sql and "visitor_pass" not in sql for sql, _ in queries)
    # Inactive direct owner membership stays eligible; unnormalized event-side
    # plate and a vehicle with no direct owner remain excluded, as before.


@pytest.mark.parametrize("count", [10_000, 10_001])
async def test_zero_activity_subject_vehicle_metadata_exact_ceiling_and_sentinel(count):
    person_id = await subject()
    async with AsyncSessionLocal() as session:
        await session.execute(insert(Vehicle), [{"id": uuid.uuid4(), "person_id": person_id, "registration_number": f"META{i:08d}"} for i in range(count)])
        await session.commit()
    if count == 10_001:
        with pytest.raises(ReportLimitError, match="10,000 vehicles"):
            await preview(person_id)
    else:
        result = await preview(person_id)
        assert len(result["report"]["person"]["vehicles"]) == 10_000
        assert result["report"]["events"] == []


@pytest.mark.parametrize("extra", [0, 1])
async def test_one_shared_activity_representation_budget_and_target_memory(extra):
    person_id = await subject()
    await save_events([observation(person_id if i < 2500 else None, i) for i in range(5000 + extra)])
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    started = perf_counter()
    try:
        if extra:
            with pytest.raises(ReportLimitError, match="combined activity"):
                await preview(person_id)
            result = None
        else:
            result = await preview(person_id)
            report = result["report"]
            counts = [len(report["events"]), len(report["timeline"]["all"]), len(report["timeline"]["selected"])]
            assert counts == [2500, 5000, 2500]
            # New deterministic equal-occurred_at ordering is explicit; strict
            # predecessor semantics still ignore every same-time observation.
            assert report["events"][0]["id"] == str(uuid.UUID(int=2500))
            assert all(value["duration"]["label"] == "New Arrival" for value in report["events"])
        elapsed = perf_counter() - started
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    artifact = Path(os.environ["IACS_DATA_DIR"]).parent / "artifacts" / f"report-shared-budget-{extra}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"rows": [2500, 5000 + extra, 2500], "overflow": bool(extra),
                                    "seconds": elapsed, "tracemalloc_current": current, "tracemalloc_peak": peak,
                                    "process_peak_kib_before": before, "process_peak_kib_after": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                    "public_json_bytes": len(json.dumps(result).encode()) if result else None}, indent=2))


async def test_requested_denied_filter_applied_before_capped_materialization():
    person_id = await subject()
    await save_events([observation(person_id, i, denied=True) for i in range(10_001)])
    async with AsyncSessionLocal() as session:
        async with report_reads.report_read_transaction(session):
            assert await report_reads.load_report_events(session, report_reads.person_event_filter(person_id), include_denied=False) == []
    with pytest.raises(ReportLimitError, match="combined activity"):
        await preview(person_id)


async def test_preview_projects_presence_only_without_profile_blob_and_preserves_external_url():
    person_id = await subject()
    async with AsyncSessionLocal() as session:
        await session.execute(update(Person).where(Person.id == person_id).values(profile_photo_data_url="data:image/png;base64," + "z" * (26 * 1024 * 1024)))
        await session.commit()
    async with AsyncSessionLocal() as session:
        async with report_reads.report_read_transaction(session):
            person = await report_reads.load_report_person(session, person_id)
            assert person.profile_photo_data_url is None and person.profile_photo_url.startswith("/api/v1/people/")
            with pytest.raises(ReportLimitError, match="image source"):
                await report_reads.load_report_person(session, person_id, include_profile_source=True)
    async with AsyncSessionLocal() as session:
        await session.execute(update(Person).where(Person.id == person_id).values(profile_photo_data_url="https://inert.invalid/profile.png"))
        await session.commit()
    result = await preview(person_id)
    assert result["report"]["person"]["profile_photo_url"] == "https://inert.invalid/profile.png"


async def test_source_runtime_config_and_data_share_readonly_repeatable_snapshot():
    person_id = await subject()
    async with AsyncSessionLocal() as session:
        session.add(SystemSetting(key="site_timezone", category="general", value={"value": "UTC"}))
        await session.commit()
    async with AsyncSessionLocal() as session:
        async with report_reads.report_read_transaction(session) as config:
            assert config.site_timezone == "UTC"
            assert await session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
            async with AsyncSessionLocal() as concurrent:
                await concurrent.execute(update(SystemSetting).where(SystemSetting.key == "site_timezone").values(value={"value": "Europe/London"}))
                await concurrent.execute(update(Person).where(Person.id == person_id).values(display_name="Changed outside snapshot"))
                await concurrent.commit()
            assert (await report_reads.load_report_person(session, person_id)).display_name == "Synthetic Report"
            assert (await report_reads.get_runtime_config_for_session(session)).site_timezone == "UTC"
        assert not session.in_transaction()


async def test_whole_source_deadline_cancels_real_blocked_sql_and_closes_transaction():
    async with AsyncSessionLocal() as session:
        with pytest.raises(ReportTimeoutError):
            async with report_reads.report_read_transaction(session, ReportWork(monotonic() + .05)):
                await session.execute(text("SELECT pg_sleep(5)"))
        assert not session.in_transaction()
        assert await session.scalar(text("SELECT 1")) == 1


@pytest.mark.parametrize("change", ["inactive", "version", "logout", "expired", "unchanged"])
async def test_final_fresh_authority_atomic_receipt_and_no_pool_checkout_during_render(monkeypatch, change):
    person_id = await subject()
    user = User(id=uuid.uuid4(), username="report-fixture", full_name="Synthetic Report", password_hash="synthetic-not-usable", role=UserRole.STANDARD)
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
    now = int(datetime.now(UTC).timestamp())
    token = auth._encode_jwt({"sub": str(user.id), "ver": 0, "iat": now, "exp": now - 1 if change == "expired" else now + 600, "jti": uuid.uuid4().hex})
    owner = report_exports.ReportExportPipeline()
    async def render(snapshot, path, *, work):
        assert engine.pool.checkedout() == 0
        async with AsyncSessionLocal() as session:
            if change == "inactive":
                await session.execute(update(User).where(User.id == user.id).values(is_active=False))
            elif change == "version":
                await session.execute(update(User).where(User.id == user.id).values(auth_session_version=1))
            elif change == "logout":
                await auth.revoke_access_token(session, token)
            await session.commit()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-synthetic-owned")
        return path.stat().st_size
    monkeypatch.setattr(report_exports, "render_person_movement_report_pdf", render)
    await owner.start()
    request = ReportRequest(person_id, None, NOW-timedelta(minutes=1), NOW+timedelta(minutes=1), False, False, True)
    try:
        if change == "unchanged":
            receipt = await owner.export(request, ReportAuthority(user.id, token))
            assert receipt["report_id"] and receipt["pdf_bytes"] > 0
        else:
            with pytest.raises(ReportAuthorityError):
                await owner.export(request, ReportAuthority(user.id, token))
    finally:
        await owner.stop()
    async with AsyncSessionLocal() as session:
        exports_count = await session.scalar(select(func.count()).select_from(ReportExport))
        audits_count = await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "report.export"))
        assert exports_count == audits_count == int(change == "unchanged")
    assert engine.pool.checkedout() == 0
