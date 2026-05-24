from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.modules.lpr.base import PlateRead
from app.services.access_events import AccessEventService
from app.services.lpr_zone_shadow import (
    LPR_ZONE_FILTER_SUPPRESSION_REASON,
    SMART_ZONE_EVIDENCE_PAYLOAD_KEY,
    evaluate_lpr_zone_filter_decision,
    evaluate_lpr_zone_filter_for_read,
)


def test_zone_filter_skips_missing_zone_status_data() -> None:
    for mode in ("shadow", "live"):
        for payload in (None, [], "malformed", [{"zone_id": "2"}]):
            decision = evaluate_lpr_zone_filter_decision(payload, mode=mode)

            assert decision.shadow_decision == "skipped_missing_zone_status"
            assert decision.actual_outcome == f"{mode}_zone_filter_skipped_missing_zone_status"
            assert not decision.would_suppress
            assert not decision.should_suppress_live


def test_zone_filter_allows_zone_two_enter() -> None:
    decision = evaluate_lpr_zone_filter_decision(
        [{"zone_id": "2", "status": "enter"}],
        mode="live",
    )

    assert decision.shadow_decision == "allowed"
    assert decision.actual_outcome == "live_zone_filter_allowed"
    assert not decision.would_suppress


def test_zone_filter_allows_zone_two_moving() -> None:
    decision = evaluate_lpr_zone_filter_decision(
        [{"zone_id": "2", "status": "moving"}],
        mode="live",
    )

    assert decision.shadow_decision == "allowed"
    assert not decision.would_suppress


def test_zone_filter_suppresses_other_zone_with_enter_or_moving() -> None:
    decision = evaluate_lpr_zone_filter_decision(
        [{"zone_id": "1", "status": "enter"}, {"zone_id": "3", "status": "moving"}],
        mode="live",
    )

    assert decision.shadow_decision == "suppressed"
    assert decision.actual_outcome == "live_zone_filter_suppressed"
    assert decision.would_suppress
    assert decision.should_suppress_live


def test_zone_filter_suppresses_zone_two_unsupported_status() -> None:
    decision = evaluate_lpr_zone_filter_decision(
        [{"zone_id": "2", "status": "loitering"}],
        mode="live",
    )

    assert decision.shadow_decision == "suppressed"
    assert decision.should_suppress_live


def test_zone_filter_allows_if_any_zone_two_entry_is_valid() -> None:
    decision = evaluate_lpr_zone_filter_decision(
        [
            {"zone_id": "1", "status": "enter"},
            {"zone_id": "2", "status": "moving"},
            {"zone_id": "3", "status": "enter"},
        ],
        mode="live",
    )

    assert decision.shadow_decision == "allowed"
    assert not decision.would_suppress


def test_shadow_mode_records_would_suppress_without_live_suppression() -> None:
    decision = evaluate_lpr_zone_filter_decision(
        [{"zone_id": "1", "status": "enter"}],
        mode="shadow",
    )

    assert decision.shadow_decision == "shadow_only"
    assert decision.actual_outcome == "shadow_zone_filter_would_suppress"
    assert decision.would_suppress
    assert not decision.should_suppress_live


def test_evaluator_reads_zone_statuses_from_plate_payload() -> None:
    read = PlateRead(
        registration_number="LC61TXJ",
        confidence=0.91,
        source="ubiquiti",
        captured_at=datetime(2026, 5, 24, tzinfo=UTC),
        raw_payload={
            SMART_ZONE_EVIDENCE_PAYLOAD_KEY: {
                "zone_statuses": [{"zone_id": "2", "status": "moving", "level": 84}],
            },
        },
    )

    decision = evaluate_lpr_zone_filter_for_read(read, mode="live")

    assert decision.shadow_decision == "allowed"
    assert not decision.should_suppress_live
    assert LPR_ZONE_FILTER_SUPPRESSION_REASON == "lpr_zone_filter_invalid_zone_status"


@pytest.mark.asyncio
async def test_live_mode_suppresses_invalid_present_zone_status(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(lpr_zone_filter_mode="live")
    read = PlateRead(
        registration_number="ROAD123",
        confidence=0.88,
        source="ubiquiti",
        captured_at=datetime(2026, 5, 24, tzinfo=UTC),
        raw_payload={
            SMART_ZONE_EVIDENCE_PAYLOAD_KEY: {
                "zone_statuses": [{"zone_id": "1", "status": "enter", "level": 62}],
            },
        },
    )
    recorded: list[dict[str, object]] = []
    published_reasons: list[str] = []

    class FakeZoneShadowService:
        async def record_decision(self, _: PlateRead, **kwargs: object) -> list[dict[str, object]]:
            recorded.append(kwargs)
            return []

    async def fake_publish_suppressed_read(_: PlateRead, *, reason: str) -> None:
        published_reasons.append(reason)

    monkeypatch.setattr("app.services.access_events.get_lpr_zone_shadow_service", lambda: FakeZoneShadowService())
    monkeypatch.setattr(service, "_publish_suppressed_read", fake_publish_suppressed_read)

    suppressed = await service._suppress_by_live_lpr_zone_filter(read)

    assert suppressed
    assert published_reasons == [LPR_ZONE_FILTER_SUPPRESSION_REASON]
    assert recorded[0]["access_event_id"] is None
    assert recorded[0]["actual_outcome"] == "live_zone_filter_suppressed"


@pytest.mark.asyncio
async def test_shadow_mode_does_not_suppress_invalid_present_zone_status(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(lpr_zone_filter_mode="shadow")
    read = PlateRead(
        registration_number="ROAD123",
        confidence=0.88,
        source="ubiquiti",
        captured_at=datetime(2026, 5, 24, tzinfo=UTC),
        raw_payload={
            SMART_ZONE_EVIDENCE_PAYLOAD_KEY: {
                "zone_statuses": [{"zone_id": "1", "status": "moving"}],
            },
        },
    )

    async def fail_if_suppressed(_: PlateRead, *, reason: str) -> None:
        raise AssertionError(f"Shadow mode should not suppress, got {reason}")

    monkeypatch.setattr(service, "_publish_suppressed_read", fail_if_suppressed)

    assert not await service._suppress_by_live_lpr_zone_filter(read)
