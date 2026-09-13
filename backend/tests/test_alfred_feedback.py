"""Feedback ownership, preserved learning policy and optional task lifetimes."""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.models.enums import UserRole
from app.services.alfred import feedback as owner

pytestmark = pytest.mark.asyncio


async def test_missing_submitter_is_rejected_before_reading_private_turn(monkeypatch):
    service = owner.AlfredFeedbackService()
    read = AsyncMock(side_effect=AssertionError("No actor must not read private context"))
    monkeypatch.setattr(service, "_load_turn_context", read)
    with pytest.raises(owner.AlfredFeedbackError, match="active IACS user"):
        await service.submit_feedback(assistant_message_id=str(uuid.uuid4()), rating="up", reason=None,
            ideal_answer=None, source_channel="dashboard", actor_role="admin")
    read.assert_not_awaited()


@pytest.mark.parametrize(("role", "mode", "rating", "text", "scope", "status"), [
    ("admin", "auto_learn", "up", "Answer concise status questions directly.", "site", "active"),
    ("standard", "auto_learn", "up", "Answer concise status questions directly.", "user", "active"),
    ("admin", "review_then_learn", "up", "Answer concise status questions directly.", "site", "pending"),
    ("admin", "auto_learn", "reflection", "Answer concise status questions directly.", "site", "pending"),
    ("standard", "auto_learn", "up", "Always mention the timezone label.", "user", "rejected"),
])
async def test_prepared_lesson_preserves_scope_mode_reflection_and_quarantine(monkeypatch, role, mode, rating, text, scope, status):
    monkeypatch.setattr(owner, "generate_embedding", AsyncMock(return_value=None))
    actor_id = uuid.uuid4()
    lesson = await owner.AlfredFeedbackService()._prepare_lesson_from_analysis(
        feedback_id=str(uuid.uuid4()), actor_uuid=actor_id, actor_role=role, rating=rating, learning_mode=mode,
        analysis={"lesson": {"title": "Synthetic lesson", "lesson": text, "tags": ["style"], "confidence": .8}})
    assert lesson.scope == scope and lesson.status == status
    assert lesson.owner_user_id == (actor_id if scope == "user" else None)
    assert (lesson.approved_by_user_id is not None) == (status == "active" and role == "admin")
    assert ("quarantined" in lesson.tags) == (status == "rejected")


async def test_claim_fence_requires_analyzing_status_and_exact_database_timestamp():
    now = datetime.now(UTC)
    claim = SimpleNamespace(updated_at=now)
    same = owner.AlfredFeedbackService._same_claim
    assert same(SimpleNamespace(status="analyzing", updated_at=now), claim)
    assert not same(SimpleNamespace(status="review_required", updated_at=now), claim)
    assert not same(SimpleNamespace(status="analyzing", updated_at=now + timedelta(microseconds=1)), claim)
    assert not same(None, claim)


async def test_current_authority_does_not_promote_missing_inactive_or_different_role():
    claim = SimpleNamespace(actor_role="admin")
    check = owner.AlfredFeedbackService._actor_unchanged
    assert check(SimpleNamespace(is_active=True, role=UserRole.ADMIN), claim)
    assert not check(None, claim)
    assert not check(SimpleNamespace(is_active=False, role=UserRole.ADMIN), claim)
    assert not check(SimpleNamespace(is_active=True, role=UserRole.STANDARD), claim)


async def test_existing_turn_owner_and_legacy_missing_owner_privacy_rules_remain_explicit():
    service = owner.AlfredFeedbackService()
    owner_id, other_id = uuid.uuid4(), uuid.uuid4()
    context = {"turn_snapshot": {"actor_context": {"user": {"id": str(owner_id)}}}}
    service._assert_feedback_allowed(context, actor_uuid=owner_id, role="standard")
    service._assert_feedback_allowed(context, actor_uuid=other_id, role="admin")
    with pytest.raises(owner.AlfredFeedbackError):
        service._assert_feedback_allowed(context, actor_uuid=other_id, role="standard")
    # Existing legacy turn policy is characterized, not silently widened/narrowed.
    service._assert_feedback_allowed({"turn_snapshot": {}}, actor_uuid=other_id, role="standard")


async def test_lifecycle_owns_bounded_optional_reflections_and_one_dispatcher(monkeypatch):
    service = owner.AlfredFeedbackService()
    monkeypatch.setattr(service, "review_interrupted_feedback", AsyncMock(return_value=0))
    monkeypatch.setattr(service, "process_feedback", AsyncMock(return_value=None))
    entered, cancelled = [], []
    blocked = asyncio.Event()

    async def reflect(*args, **kwargs):
        entered.append(kwargs["session_id"])
        try:
            await blocked.wait()
        finally:
            cancelled.append(kwargs["session_id"])

    monkeypatch.setattr(service, "reflect_on_turn", reflect)
    values = dict(user_message="synthetic", assistant_text="synthetic", tool_results=[], actor_context={},
        provider_name="synthetic", model_name=None)
    service.schedule_reflection(None, session_id="before-start", **values)
    assert not service._reflections
    await service.start()
    first = service._dispatcher
    try:
        await service.start()
        assert service._dispatcher is first
        for index in range(owner.MAX_REFLECTION_TASKS + 1):
            service.schedule_reflection(None, session_id=str(index), **values)
        await asyncio.sleep(0)
        assert len(entered) == owner.MAX_REFLECTION_TASKS
        await service.stop()
        assert first.done() and len(cancelled) == owner.MAX_REFLECTION_TASKS
        assert service._dispatcher is None and not service._reflections
        await service.stop()
    finally:
        await service.stop()


async def test_cancelled_analysis_preserves_cancellation_when_review_checkpoint_fails(monkeypatch):
    service = owner.AlfredFeedbackService()
    claim = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(service, "_claim_feedback", AsyncMock(return_value=claim))
    monkeypatch.setattr(service, "_process_claimed_feedback", AsyncMock(side_effect=asyncio.CancelledError()))
    checkpoint = AsyncMock(side_effect=RuntimeError("synthetic checkpoint unavailable"))
    monkeypatch.setattr(service, "_review_claim", checkpoint)
    with pytest.raises(asyncio.CancelledError):
        await service.process_feedback(claim.id)
    checkpoint.assert_awaited_once_with(claim, "analysis_cancelled")
