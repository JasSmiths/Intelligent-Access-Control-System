import base64
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from app.api.dependencies import current_user
from app.api.v1 import reports as reports_api
from app.db.session import get_db_session
from app.models import AccessEvent, ReportExport, User, VisitorPass
from app.models.enums import AccessDecision, AccessDirection, TimingClassification, UserRole, VisitorPassStatus, VisitorPassType
from app.services import auth as auth_service
from app.services import reports as reports_service
from app.services import snapshots as snapshots_service


def make_user() -> User:
    now = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
    return User(
        id=uuid.uuid4(),
        username="admin",
        first_name="Admin",
        last_name="User",
        full_name="Admin User",
        email="admin@example.com",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_event(
    *,
    occurred_at: datetime,
    direction: AccessDirection,
    decision: AccessDecision = AccessDecision.GRANTED,
    registration_number: str = "AGS7X",
) -> AccessEvent:
    event = AccessEvent(
        id=uuid.uuid4(),
        registration_number=registration_number,
        direction=direction,
        decision=decision,
        confidence=0.94,
        source="ubiquiti",
        occurred_at=occurred_at,
        timing_classification=TimingClassification.UNKNOWN,
        raw_payload={},
    )
    event.anomalies = []
    return event


def test_report_duration_formatting() -> None:
    start = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)

    short = reports_service.format_duration_info(
        start,
        start + timedelta(hours=6, minutes=12),
        "Short duration",
    )
    medium = reports_service.format_duration_info(
        start,
        start + timedelta(days=12, hours=3, minutes=8),
        "Medium duration",
    )
    long = reports_service.format_duration_info(
        start,
        start + timedelta(days=78),
        "Long duration",
        timezone=reports_service._timezone("UTC"),
    )

    assert short["label"] == "6hrs 12m"
    assert medium["label"] == "12 Days, 3hrs 8m"
    assert long["label"] == "01/05/2026 - 08:00"
    assert long["tooltip"] == "2 Months, 18 Days"


def test_report_duration_lookup_handles_arrivals_and_departures() -> None:
    first_arrival = make_event(
        occurred_at=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        direction=AccessDirection.ENTRY,
    )
    departure = make_event(
        occurred_at=datetime(2026, 5, 1, 14, 12, tzinfo=UTC),
        direction=AccessDirection.EXIT,
    )
    next_arrival = make_event(
        occurred_at=datetime(2026, 5, 2, 9, 0, tzinfo=UTC),
        direction=AccessDirection.ENTRY,
    )

    durations = reports_service.build_duration_lookup(
        [first_arrival, departure, next_arrival],
        [first_arrival, departure, next_arrival],
    )

    assert durations[str(first_arrival.id)]["label"] == "New Arrival"
    assert durations[str(departure.id)]["label"] == "6hrs 12m"
    assert durations[str(next_arrival.id)]["label"] == "18hrs 48m"


async def test_report_id_generation_skips_existing(monkeypatch) -> None:
    values = iter([0, 42])

    class FakeSession:
        def __init__(self) -> None:
            self.calls = 0

        async def scalar(self, _statement):
            self.calls += 1
            return uuid.uuid4() if self.calls == 1 else None

    monkeypatch.setattr(reports_service.secrets, "randbelow", lambda _limit: next(values))

    report_id = await reports_service.generate_report_number(FakeSession())

    assert report_id == "100042"


def test_snapshot_toggle_controls_serialized_event_media(monkeypatch) -> None:
    event = make_event(
        occurred_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        direction=AccessDirection.ENTRY,
    )
    event.snapshot_path = "snapshots/access-events/example.jpg"
    event.snapshot_content_type = "image/jpeg"
    monkeypatch.setattr(
        reports_service,
        "access_event_snapshot_payload",
        lambda item, **_kwargs: {
            "snapshot_url": f"/api/v1/events/{item.id}/snapshot",
            "snapshot_captured_at": None,
            "snapshot_bytes": 100,
            "snapshot_width": 640,
            "snapshot_height": 360,
            "snapshot_camera": "Gate",
        },
    )

    without_snapshot = reports_service.serialize_report_event(
        event,
        include_snapshots=False,
        duration={"label": "New Arrival"},
        timezone=reports_service._timezone("Europe/London"),
    )
    with_snapshot = reports_service.serialize_report_event(
        event,
        include_snapshots=True,
        duration={"label": "New Arrival"},
        timezone=reports_service._timezone("Europe/London"),
    )

    assert without_snapshot["snapshot_url"] is None
    assert without_snapshot["_snapshot_path"] is None
    assert with_snapshot["snapshot_url"] == f"/api/v1/events/{event.id}/snapshot"
    assert with_snapshot["_snapshot_path"] == "snapshots/access-events/example.jpg"


def test_pdf_safe_profile_image_keeps_browser_supported_data_url() -> None:
    data_url = "data:image/jpeg;base64,aGVsbG8="

    assert reports_service._pdf_safe_image_data_url(data_url) == data_url


def test_pdf_safe_profile_image_resizes_converted_images() -> None:
    source = BytesIO()
    Image.new("RGB", (1200, 900), "#8ab4f8").save(source, format="TIFF")
    data_url = f"data:image/tiff;base64,{base64.b64encode(source.getvalue()).decode('ascii')}"

    converted = reports_service._pdf_safe_image_data_url(data_url)

    assert converted is not None
    assert converted.startswith("data:image/jpeg;base64,")
    encoded = converted.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as image:
        assert max(image.size) == reports_service.PDF_SAFE_PROFILE_IMAGE_MAX_EDGE_PX


def test_pdf_report_uses_pdf_safe_profile_photo(monkeypatch) -> None:
    monkeypatch.setattr(
        reports_service,
        "_pdf_safe_image_data_url",
        lambda value: f"safe:{value}" if value else None,
    )

    report = reports_service._report_for_pdf(
        {
            "person": {"profile_photo_data_url": "data:image/heic;base64,abc"},
            "brand": {},
            "events": [],
        }
    )

    assert report["person"]["pdf_profile_photo_data_url"] == "safe:data:image/heic;base64,abc"


def test_public_report_snapshot_strips_embedded_media() -> None:
    public = reports_service.public_report_snapshot(
        {
            "person": {
                "profile_photo_data_url": "data:image/jpeg;base64,person",
                "profile_photo_url": "/api/v1/people/person-id/photo",
                "pdf_profile_photo_data_url": "data:image/jpeg;base64,pdf",
                "vehicles": [
                    {
                        "vehicle_photo_data_url": "data:image/jpeg;base64,vehicle",
                        "vehicle_photo_url": "/api/v1/vehicles/vehicle-id/photo",
                    }
                ],
            },
            "events": [
                {
                    "_snapshot_path": "snapshots/access-events/example.jpg",
                    "_snapshot_content_type": "image/jpeg",
                    "pdf_snapshot_data_url": "data:image/jpeg;base64,snapshot",
                }
            ],
        }
    )

    assert "profile_photo_data_url" not in public["person"]
    assert public["person"]["profile_photo_url"] == "/api/v1/people/person-id/photo"
    assert "vehicle_photo_data_url" not in public["person"]["vehicles"][0]
    assert public["person"]["vehicles"][0]["vehicle_photo_url"] == "/api/v1/vehicles/vehicle-id/photo"
    assert "_snapshot_path" not in public["events"][0]


def test_visitor_pass_serializes_as_report_subject() -> None:
    visitor_pass = VisitorPass(
        id=uuid.uuid4(),
        visitor_name="Taylor Visitor",
        pass_type=VisitorPassType.DURATION,
        visitor_phone=None,
        expected_time=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
        window_minutes=60,
        status=VisitorPassStatus.ACTIVE,
        number_plate="VIS123",
        vehicle_make="Ford",
        vehicle_colour="White",
    )

    subject = reports_service.serialize_report_visitor_pass(visitor_pass)

    assert subject["id"] == str(visitor_pass.id)
    assert subject["display_name"] == "Taylor Visitor"
    assert subject["group"] == "Visitor Pass"
    assert subject["pronouns"] is None
    assert subject["vehicles"][0]["registration_number"] == "VIS123"
    assert subject["vehicles"][0]["title"] == "Ford"
    assert subject["vehicles"][0]["color"] == "White"


def app_for_reports(user: User) -> FastAPI:
    app = FastAPI()
    app.include_router(reports_api.router, prefix="/api/v1/reports")

    async def override_current_user() -> User:
        return user

    async def override_db_session():
        yield SimpleNamespace()

    app.dependency_overrides[current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_db_session
    return app


async def test_report_export_endpoint_returns_download_payload(monkeypatch) -> None:
    user = make_user()
    row = ReportExport(
        id=uuid.uuid4(),
        report_number="123456",
        report_type=reports_service.REPORT_TYPE_PERSON_MOVEMENTS,
        person_id=uuid.uuid4(),
        period_start=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        period_end=datetime(2026, 5, 5, 8, 0, tzinfo=UTC),
        options={"include_snapshots": True},
        snapshot={"report_id": "123456", "events": [], "person": {"display_name": "Ash Smith"}},
        pdf_path="reports/person-movements-123456.pdf",
        pdf_bytes=321,
        created_by_user_id=user.id,
        created_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )

    async def fake_create(*_args, **_kwargs):
        return row

    monkeypatch.setattr(reports_api, "create_person_movement_report_export", fake_create)
    app = app_for_reports(user)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/reports/person-movements/export",
            json={
                "person_id": str(uuid.uuid4()),
                "period_start": "2026-05-01T08:00:00Z",
                "period_end": "2026-05-05T08:00:00Z",
                "include_snapshots": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_id"] == "123456"
    assert payload["download_url"] == "/api/v1/reports/123456/pdf"
    assert payload["report"]["report_id"] == "123456"


async def test_report_export_endpoint_accepts_visitor_pass_subject(monkeypatch) -> None:
    user = make_user()
    visitor_pass_id = uuid.uuid4()
    captured_kwargs = {}
    row = ReportExport(
        id=uuid.uuid4(),
        report_number="456789",
        report_type=reports_service.REPORT_TYPE_PERSON_MOVEMENTS,
        person_id=None,
        period_start=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        period_end=datetime(2026, 5, 5, 8, 0, tzinfo=UTC),
        options={"include_snapshots": False},
        snapshot={
            "report_id": "456789",
            "subject_type": "visitor_pass",
            "events": [],
            "person": {"id": str(visitor_pass_id), "display_name": "Taylor Visitor"},
        },
        pdf_path="reports/person-movements-456789.pdf",
        pdf_bytes=456,
        created_by_user_id=user.id,
        created_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )

    async def fake_create(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return row

    monkeypatch.setattr(reports_api, "create_person_movement_report_export", fake_create)
    app = app_for_reports(user)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/reports/person-movements/export",
            json={
                "visitor_pass_id": str(visitor_pass_id),
                "period_start": "2026-05-01T08:00:00Z",
                "period_end": "2026-05-05T08:00:00Z",
                "include_snapshots": False,
            },
        )

    assert response.status_code == 200
    assert captured_kwargs["person_id"] is None
    assert captured_kwargs["visitor_pass_id"] == visitor_pass_id
    assert response.json()["report"]["subject_type"] == "visitor_pass"


async def test_report_lookup_and_pdf_download(monkeypatch, tmp_path) -> None:
    user = make_user()
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
    row = ReportExport(
        id=uuid.uuid4(),
        report_number="654321",
        report_type=reports_service.REPORT_TYPE_PERSON_MOVEMENTS,
        person_id=uuid.uuid4(),
        period_start=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        period_end=datetime(2026, 5, 5, 8, 0, tzinfo=UTC),
        options={},
        snapshot={"report_id": "654321", "events": [{"id": "event", "_snapshot_path": "hidden"}]},
        pdf_path="reports/person-movements-654321.pdf",
        pdf_bytes=pdf_path.stat().st_size,
        created_by_user_id=user.id,
        created_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )

    async def fake_load(_session, report_id, *, actor):
        assert actor.id == user.id
        return row if report_id == "654321" else None

    monkeypatch.setattr(reports_api, "load_report_export", fake_load)
    monkeypatch.setattr(reports_api, "report_pdf_path", lambda _row: pdf_path)
    app = app_for_reports(user)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        lookup = await client.get("/api/v1/reports/654321")
        download = await client.get("/api/v1/reports/654321/pdf")

    assert lookup.status_code == 200
    assert lookup.json()["report"]["events"][0].get("_snapshot_path") is None
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert "Crest-House-Access-Report-654321.pdf" in download.headers["content-disposition"]
    assert download.content.startswith(b"%PDF")


async def test_report_lookup_denies_non_owner_standard_user(monkeypatch) -> None:
    user = make_user()
    user.role = UserRole.STANDARD
    other_user_id = uuid.uuid4()
    row = ReportExport(
        id=uuid.uuid4(),
        report_number="654322",
        report_type=reports_service.REPORT_TYPE_PERSON_MOVEMENTS,
        person_id=uuid.uuid4(),
        period_start=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        period_end=datetime(2026, 5, 5, 8, 0, tzinfo=UTC),
        options={},
        snapshot={"report_id": "654322", "events": []},
        pdf_path="reports/person-movements-654322.pdf",
        pdf_bytes=100,
        created_by_user_id=other_user_id,
        created_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
    )

    class FakeSession:
        async def scalar(self, _statement):
            return row

    loaded = await reports_service.load_report_export(FakeSession(), "654322", actor=user)

    assert loaded is None


PREVIEW_CONTRACT = json.loads(
    (Path(__file__).parent / "contracts/fixtures/reports/preview_contract.json").read_text()
)


@pytest.mark.parametrize(("local", "expected"), [
    ("2026-07-14T09:00:00", "2026-07-14T08:00:00+00:00"),
    ("2026-01-14T09:00:00", "2026-01-14T09:00:00+00:00"),
    ("2026-10-25T01:30:00+01:00", "2026-10-25T00:30:00+00:00"),
    ("2026-10-25T01:30:00+00:00", "2026-10-25T01:30:00+00:00"),
])
def test_report_boundary_uses_site_zone_and_preserves_aware_instants(local, expected):
    resolved, choice = reports_service.resolve_report_boundary(
        datetime.fromisoformat(local), timezone=reports_service._timezone("Europe/London"), field="period_start",
    )
    assert resolved.isoformat() == expected
    assert choice is None


@pytest.mark.parametrize("case", PREVIEW_CONTRACT["fold_requests"])
def test_report_boundary_requires_explicit_occurrence(case):
    local = datetime.fromisoformat(case["period_start"])
    zone = reports_service._timezone("Europe/London")
    resolved, choice = reports_service.resolve_report_boundary(local, timezone=zone, field="period_start")
    assert resolved is None
    assert choice == PREVIEW_CONTRACT["ambiguous"]["time_choices"][0]
    resolved, choice = reports_service.resolve_report_boundary(
        local, timezone=zone, field="period_start", fold=case["period_start_fold"],
    )
    assert resolved.isoformat() == case["resolved_start"]
    assert choice is None


def test_report_boundary_rejects_nonexistent_time_instead_of_normalizing_it():
    case = PREVIEW_CONTRACT["gap"]
    with pytest.raises(reports_service.ReportExportError, match="Start time does not exist") as exc:
        reports_service.resolve_report_boundary(
            datetime.fromisoformat(case["period_start"]),
            timezone=reports_service._timezone("Europe/London"), field="period_start",
        )
    assert str(exc.value) == case["detail"]


def report_person():
    return SimpleNamespace(
        id=uuid.UUID(PREVIEW_CONTRACT["ready"]["report"]["person"]["id"]),
        first_name="Synthetic", last_name="Resident", display_name="Synthetic Resident",
        pronouns=None, profile_photo_data_url=None, group=None, vehicles=[], presence=None, updated_at=None,
    )


class ReportReadSession:
    """Only reads exist; accidental persistence calls fail the test."""
    def __init__(self, event_batches=()):
        self.event_batches = iter(event_batches)
        self.queries = []

    async def scalar(self, statement):
        self.queries.append(statement)
        return report_person()

    async def scalars(self, statement):
        self.queries.append(statement)
        return SimpleNamespace(all=lambda: next(self.event_batches))

    async def get(self, _model, _identity):
        return None


def forbid_report_effects(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Preview attempted a write, media restoration or export effect")

    for name in ("generate_report_number", "render_person_movement_report_pdf", "write_audit_log", "_resolve_report_path"):
        monkeypatch.setattr(reports_service, name, forbidden)
    monkeypatch.setattr(reports_service, "get_snapshot_manager", forbidden)
    monkeypatch.setattr(snapshots_service, "get_snapshot_manager", forbidden)


async def london_report_config():
    return SimpleNamespace(site_timezone="Europe/London")


async def test_preview_contract_is_built_without_export_or_persistence(monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 14, 9, tzinfo=UTC)

    monkeypatch.setattr(reports_service, "datetime", FixedClock)
    monkeypatch.setattr(reports_service, "get_runtime_config", london_report_config)
    forbid_report_effects(monkeypatch)
    session = ReportReadSession([[], [], []])
    preview = await reports_service.preview_person_movement_report(
        session, person_id=report_person().id,
        period_start=datetime(2026, 7, 14, 9), period_end=datetime(2026, 7, 14, 10),
        include_denied=False, include_snapshots=True, include_confidence=True,
    )
    # Brand is an existing site presentation constant, outside this synthetic DTO fixture.
    preview["report"].pop("brand")
    assert preview == PREVIEW_CONTRACT["ready"]
    assert len(session.queries) == 4


async def test_preview_complete_history_duration_and_media_are_shared_with_export_builder(monkeypatch):
    monkeypatch.setattr(reports_service, "get_runtime_config", london_report_config)
    forbid_report_effects(monkeypatch)
    start = datetime(2026, 7, 14, 8, tzinfo=UTC)
    prior = make_event(occurred_at=start - timedelta(hours=6), direction=AccessDirection.ENTRY)
    departures = [make_event(occurred_at=start + timedelta(minutes=index), direction=AccessDirection.EXIT) for index in range(251)]
    departures[0].snapshot_path = "snapshots/access-events/synthetic.jpg"
    departures[0].snapshot_content_type = "image/jpeg"
    session = ReportReadSession([departures, [prior, *departures], departures])
    preview = await reports_service.preview_person_movement_report(
        session, person_id=report_person().id, period_start=start, period_end=start + timedelta(hours=5),
        include_denied=False, include_snapshots=True, include_confidence=True,
    )
    report = preview["report"]
    assert preview["complete"] is True
    assert len(report["events"]) == 251
    assert report["summary"]["total"] == 251
    first_departure = report["events"][-1]
    assert first_departure["duration"] == {"label": "6hrs 0m"}
    assert first_departure["snapshot_url"] == f"/api/v1/events/{departures[0].id}/snapshot"
    assert "_snapshot_path" not in first_departure
    assert all("LIMIT" not in str(query).upper() for query in session.queries)
    # Export invokes this same snapshot builder; media availability is its only read-policy difference.
    exported = reports_service.public_report_snapshot(await reports_service.build_movement_report_snapshot(
        ReportReadSession([departures, [prior, *departures], departures]),
        person_id=report_person().id, visitor_pass_id=None, report_number="123456",
        period_start=start, period_end=start + timedelta(hours=5),
        options=report["options"], timezone=reports_service._timezone("Europe/London"),
        verify_snapshot_availability=False,
    ))
    for key in ("events", "period", "summary", "timeline", "person", "presence", "options"):
        assert exported[key] == report[key]
    assert exported["report_id"] == "123456"


async def test_preview_visitor_keeps_existing_attribution_filter_and_options(monkeypatch):
    monkeypatch.setattr(reports_service, "get_runtime_config", london_report_config)
    forbid_report_effects(monkeypatch)
    arrival = make_event(occurred_at=datetime(2026, 7, 14, 8, tzinfo=UTC), direction=AccessDirection.ENTRY)
    denied = make_event(occurred_at=datetime(2026, 7, 14, 9, tzinfo=UTC), direction=AccessDirection.ENTRY, decision=AccessDecision.DENIED)
    visitor = VisitorPass(
        id=uuid.uuid4(), visitor_name="Synthetic Visitor", pass_type=VisitorPassType.DURATION,
        number_plate="SYNTH01", arrival_event_id=arrival.id, departure_event_id=None,
    )
    class VisitorReadSession(ReportReadSession):
        async def get(self, model, identity):
            assert model is VisitorPass and identity == visitor.id
            return visitor

    session = VisitorReadSession([[arrival, denied], [arrival], [arrival]])
    preview = await reports_service.preview_person_movement_report(
        session, visitor_pass_id=visitor.id,
        period_start=datetime(2026, 7, 14, 8, tzinfo=UTC), period_end=datetime(2026, 7, 14, 10, tzinfo=UTC),
        include_denied=False, include_snapshots=False, include_confidence=False,
    )
    assert preview["report"]["subject_type"] == "visitor_pass"
    assert preview["report"]["person"]["display_name"] == "Synthetic Visitor"
    assert len(preview["report"]["events"]) == 1
    assert preview["report"]["events"][0]["snapshot_url"] is None
    params = session.queries[0].compile().params.values()
    assert {"visitor_pass": {"id": str(visitor.id)}} in params
    assert "SYNTH01" in params
    assert [arrival.id] in params


async def test_preview_api_matches_choice_and_gap_contracts_and_validates_fold(monkeypatch):
    monkeypatch.setattr(reports_service, "get_runtime_config", london_report_config)
    forbid_report_effects(monkeypatch)
    app = app_for_reports(make_user())
    body = {"person_id": str(report_person().id), "period_start": "2026-10-25T01:30:00", "period_end": "2026-10-25T03:00:00"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ambiguous = await client.post("/api/v1/reports/person-movements/preview", json=body)
        invalid = await client.post("/api/v1/reports/person-movements/preview", json={**body, "period_start_fold": 2})
        gap = await client.post("/api/v1/reports/person-movements/preview", json={**body, "period_start": PREVIEW_CONTRACT["gap"]["period_start"]})
        backwards = await client.post("/api/v1/reports/person-movements/preview", json={**body, "period_start": "2026-10-25T04:00:00"})
    assert ambiguous.status_code == 200
    assert ambiguous.json() == PREVIEW_CONTRACT["ambiguous"]
    assert invalid.status_code == 422
    assert gap.status_code == PREVIEW_CONTRACT["gap"]["status_code"]
    assert gap.json() == {"detail": PREVIEW_CONTRACT["gap"]["detail"]}
    assert backwards.status_code == 400
    assert backwards.json() == {"detail": "Report end time must be after the start time."}


async def test_preview_api_retains_authentication_and_passes_aware_bounds(monkeypatch):
    app = app_for_reports(make_user())
    async def synthetic_auth_config():
        return SimpleNamespace(auth_cookie_name="synthetic_iacs_session")
    # Exercise real credential absence/denial without loading runtime settings from a database.
    monkeypatch.setattr(auth_service, "get_runtime_config", synthetic_auth_config)
    captured = {}
    async def preview(_session, **kwargs):
        captured.update(kwargs)
        return PREVIEW_CONTRACT["ready"]
    monkeypatch.setattr(reports_api, "preview_person_movement_report", preview)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/reports/person-movements/preview", json={
            "person_id": str(report_person().id), "period_start": "2026-10-25T01:30:00+01:00",
            "period_end": "2026-10-25T01:30:00+00:00",
        })
        app.dependency_overrides.pop(current_user)
        anonymous = await client.post("/api/v1/reports/person-movements/preview", json={})
    assert response.json() == PREVIEW_CONTRACT["ready"]
    assert captured["period_start"].utcoffset() == timedelta(hours=1)
    assert captured["period_end"].utcoffset() == timedelta(0)
    assert anonymous.status_code == 401


def test_frontend_report_fixture_is_generated_from_canonical_contract():
    frontend_fixture = Path(__file__).parents[2] / "frontend/src/api/fixtures/reportPreview.generated.json"
    assert json.loads(frontend_fixture.read_text()) == PREVIEW_CONTRACT


def test_report_timeline_emits_the_public_fixture_fields_in_site_time():
    expected = PREVIEW_CONTRACT["timeline_event"]
    event = make_event(occurred_at=datetime.fromisoformat(expected["occurred_at"]), direction=AccessDirection.ENTRY,
                       registration_number=expected["registration_number"])
    event.id = uuid.UUID(expected["id"])
    assert reports_service.serialize_timeline_event(event, timezone=reports_service._timezone("Europe/London")) == expected


async def test_export_orchestration_preserves_actor_subject_utc_bounds_and_saved_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(reports_service, "get_runtime_config", london_report_config)
    captured = {}
    class ExportSession(ReportReadSession):
        def add(self, row):
            captured["row"] = row

        async def commit(self):
            captured["committed"] = True

        async def refresh(self, row):
            assert captured["committed"] is True and row is captured["row"]
            captured["refreshed"] = True

    async def number(_session):
        return "123456"

    async def render(snapshot, path, *, timezone):
        captured["rendered"] = snapshot
        assert path == tmp_path / "synthetic.pdf"
        assert timezone.key == "Europe/London"
        return 321

    async def audit(_session, **kwargs):
        captured["audit"] = kwargs

    monkeypatch.setattr(reports_service, "generate_report_number", number)
    monkeypatch.setattr(reports_service, "render_person_movement_report_pdf", render)
    monkeypatch.setattr(reports_service, "_resolve_report_path", lambda _relative, **_kwargs: tmp_path / "synthetic.pdf")
    monkeypatch.setattr(reports_service, "write_audit_log", audit)
    actor = make_user()
    row = await reports_service.create_person_movement_report_export(
        ExportSession([[], [], []]), person_id=report_person().id,
        period_start=datetime.fromisoformat("2026-07-14T09:00:00+01:00"),
        period_end=datetime.fromisoformat("2026-07-14T10:00:00+01:00"),
        include_denied=False, include_snapshots=True, include_confidence=True, actor=actor,
    )
    assert row is captured["row"]
    assert row.report_number == "123456"
    assert row.person_id == report_person().id
    assert row.created_by_user_id == actor.id
    assert row.period_start.isoformat() == PREVIEW_CONTRACT["ready"]["report"]["period"]["start"]
    assert row.period_end.isoformat() == PREVIEW_CONTRACT["ready"]["report"]["period"]["end"]
    assert row.pdf_bytes == 321
    assert row.snapshot is captured["rendered"]
    assert row.snapshot["report_id"] == "123456"
    assert row.snapshot["subject"] == PREVIEW_CONTRACT["ready"]["report"]["subject"]
    assert captured["audit"]["action"] == "report.export"
    assert captured["audit"]["actor_user_id"] == actor.id
    assert captured["audit"]["metadata"]["subject_type"] == "person"
    assert captured["refreshed"] is True
