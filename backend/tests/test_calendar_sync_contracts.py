"""Persisted run corruption and cross-origin replay fail before provider work."""
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.modules.icloud_calendar.contracts import CalendarCoverage
from app.services.calendar_sync_contracts import (
    CalendarAccountCheckpoint,
    CalendarAttempt,
    CalendarLegacyRun,
    CalendarRunContext,
    checked_calendar_checkpoints,
)


def context():
    return CalendarRunContext(origin="ui", original_actor_user_id=uuid.uuid4(),
        account_ids=(uuid.uuid4(), uuid.uuid4()), site_timezone="Europe/London",
        coverage=CalendarCoverage.capture(datetime(2026, 10, 24, tzinfo=UTC),
            datetime(2026, 11, 8, tzinfo=UTC), vendor_timezone="Europe/London"))


def test_durable_context_round_trip_preserves_origin_manifest_and_inclusive_dst_coverage():
    original = context()
    attempt = CalendarAttempt(token=uuid.uuid4(), lease_until=datetime(2026, 10, 24, tzinfo=UTC))
    stored = original.model_copy(update={"attempt": attempt}).model_dump(mode="json")
    restored = CalendarRunContext.stored(stored)
    assert restored.account_ids == original.account_ids
    assert restored.coverage == original.coverage
    assert restored.attempt == attempt
    assert restored.coverage.effective_end == datetime(2026, 11, 9, tzinfo=UTC)
    assert restored.matches_origin("ui", original.original_actor_user_id, None)
    assert not restored.matches_origin("ui", uuid.uuid4(), None)
    assert not restored.matches_origin("workflow", original.original_actor_user_id, None)


def test_legacy_and_unknown_run_context_cannot_authorize_resume():
    with pytest.raises(CalendarLegacyRun, match="historical"):
        CalendarRunContext.stored({})
    stored = context().model_dump(mode="json")
    stored["unexpected_authority"] = "ignore this"
    with pytest.raises(ValidationError, match="Extra inputs"):
        CalendarRunContext.stored(stored)


def test_workflow_replay_retains_canonical_execution_and_rejects_changed_claim():
    execution = {"run_id": str(uuid.uuid4()), "claim_token": str(uuid.uuid4()),
        "index": 2, "operation_id": str(uuid.uuid4()), "idempotency_key": "owned:action:2"}
    original = context().model_copy(update={"origin": "workflow", "execution": execution})
    restored = CalendarRunContext.stored(original.model_dump(mode="json"))
    assert restored.matches_origin("workflow", original.original_actor_user_id, execution)
    assert not restored.matches_origin("workflow", original.original_actor_user_id,
                                      {**execution, "claim_token": str(uuid.uuid4())})
    stored = original.model_dump(mode="json")
    stored["execution"]["index"] = -1
    with pytest.raises(ValidationError, match="non-negative"):
        CalendarRunContext.stored(stored)


def test_receipts_reject_duplicate_accounts_unknown_fields_and_uncommitted_counters():
    checkpoint = CalendarAccountCheckpoint(account_id=uuid.uuid4(), apple_id="inert@example.invalid",
        display_name="Inert", attempt_token=uuid.uuid4(), status="ok", passes_created=1)
    assert checkpoint.terminal
    row = checkpoint.model_dump(mode="json")
    with pytest.raises(ValueError, match="duplicate"):
        checked_calendar_checkpoints([row, row])
    with pytest.raises(ValidationError, match="accepted counters"):
        checked_calendar_checkpoints([{**row, "status": "interrupted"}])
    with pytest.raises(ValidationError, match="Extra inputs"):
        checked_calendar_checkpoints([{**row, "raw_vendor_blob": {}}])
    assert not checkpoint.model_copy(update={"status": "requires_reauth", "passes_created": 0}).terminal
