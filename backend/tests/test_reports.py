import base64
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
import uuid

import httpx
from fastapi import FastAPI
from PIL import Image

from app.api.dependencies import current_user
from app.api.v1 import reports as reports_api
from app.db.session import get_db_session
from app.models import AccessEvent, ReportExport, User, VisitorPass
from app.models.enums import AccessDecision, AccessDirection, TimingClassification, UserRole, VisitorPassStatus, VisitorPassType
from app.services import reports as reports_service


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
        lambda item: {
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

    async def fake_load(_session, report_id):
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
