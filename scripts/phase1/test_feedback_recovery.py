"""Durable submitted-feedback contracts; inert providers and disposable PostgreSQL only."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.db.session import AsyncSessionLocal, engine
from app.models import AlfredEvalExample, AlfredFeedback, AlfredLesson, AuditLog, ChatMessage, ChatSession, User
from app.models.enums import UserRole
from app.services.alfred import feedback as owner

pytestmark = pytest.mark.asyncio
ANALYSIS = {"summary": "Synthetic feedback analyzed", "lesson": {"title": "Concise answers",
    "lesson": "Answer concise status questions directly.", "tags": ["style"], "confidence": .8},
    "corrected_answer": "A concise synthetic answer."}


@pytest_asyncio.fixture(autouse=True)
async def feedback_resources(isolated_resources, monkeypatch):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE alfred_feedback, alfred_lessons, alfred_eval_examples CASCADE"))
        await session.commit()
    monkeypatch.setattr(owner, "get_runtime_config", AsyncMock(return_value=SimpleNamespace(
        llm_provider="synthetic", alfred_background_model="synthetic", alfred_learning_mode="auto_learn")))
    monkeypatch.setattr(owner, "generate_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(owner.AlfredFeedbackService, "_analyze_feedback", AsyncMock(side_effect=lambda **kwargs: deepcopy(ANALYSIS)))
    yield


async def turn(role=UserRole.ADMIN, *, owner_in_snapshot=True):
    async with AsyncSessionLocal() as session:
        user = User(username="synthetic-feedback-" + uuid.uuid4().hex, full_name="Synthetic Feedback User",
            password_hash="inert-unused", role=role, is_active=True)
        chat = ChatSession(title="Synthetic feedback turn", context={})
        session.add_all([user, chat])
        await session.flush()
        question = ChatMessage(session_id=chat.id, role="user", content="Synthetic status question")
        session.add(question)
        await session.flush()
        snapshot = {"user_message_id": str(question.id), "user_message": question.content,
            "assistant_response": "Synthetic original answer", "tool_results": []}
        if owner_in_snapshot:
            snapshot["actor_context"] = {"user": {"id": str(user.id), "role": str(role)}}
        answer = ChatMessage(session_id=chat.id, role="assistant", content="Synthetic original answer",
            tool_payload={"turn_snapshot": snapshot})
        session.add(answer)
        await session.commit()
    return user, answer


async def submit(service=None, *, role=UserRole.ADMIN):
    service = service or owner.AlfredFeedbackService()
    user, answer = await turn(role)
    result = await service.submit_feedback(assistant_message_id=str(answer.id), rating="down", reason="Be concise",
        ideal_answer="A concise synthetic answer.", source_channel="dashboard", user=user)
    return service, user, uuid.UUID(result["feedback"]["id"]), result


async def row(identity):
    async with AsyncSessionLocal() as session:
        return await session.get(AlfredFeedback, identity)


async def counts():
    async with AsyncSessionLocal() as session:
        return tuple([await session.scalar(select(func.count()).select_from(model))
            for model in (AlfredFeedback, AlfredLesson, AlfredEvalExample)])


async def test_submission_audit_and_queue_commit_together_without_starting_processing(monkeypatch):
    service, user, identity, result = await submit()
    assert result["processing"] is True and result["feedback"]["status"] == "queued"
    assert service._dispatcher is None and not service._reflections
    owner.AlfredFeedbackService._analyze_feedback.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "alfred.feedback.submit"))
        assert audit and audit.target_id == str(identity) and audit.actor_user_id == user.id
    assert await counts() == (1, 0, 0)

    async def reject_audit(*args, **kwargs):
        raise RuntimeError("synthetic mandatory audit failure")

    monkeypatch.setattr(owner, "write_audit_log", reject_audit)
    with pytest.raises(RuntimeError, match="mandatory audit"):
        await submit()
    assert await counts() == (1, 0, 0)


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.STANDARD])
async def test_success_commits_one_feedback_lesson_and_eval_with_preserved_scope(role):
    service, user, identity, _ = await submit(role=role)
    result = await service.process_feedback(identity)
    assert result["feedback"]["status"] == "processed" and result["processing"] is False
    assert await counts() == (1, 1, 1)
    async with AsyncSessionLocal() as session:
        lesson = await session.get(AlfredLesson, uuid.UUID(result["lesson"]["id"]))
        example = await session.scalar(select(AlfredEvalExample))
        assert lesson.scope == example.scope == ("site" if role == UserRole.ADMIN else "user")
        assert lesson.owner_user_id == (None if role == UserRole.ADMIN else user.id)
        assert lesson.source_feedback_ids == [str(identity)] and example.feedback_id == identity
    assert await service.process_feedback(identity) is None
    assert await counts() == (1, 1, 1)


async def test_concurrent_workers_claim_only_once_and_hold_no_database_connection_during_analysis(monkeypatch):
    service, _, identity, _ = await submit()
    other = owner.AlfredFeedbackService()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def analyze(**kwargs):
        nonlocal calls
        calls += 1
        assert engine.pool.checkedout() == 0
        entered.set()
        await release.wait()
        return deepcopy(ANALYSIS)

    monkeypatch.setattr(service, "_analyze_feedback", analyze)
    task = asyncio.create_task(service.process_feedback(identity))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert await other.process_feedback(identity) is None
        release.set()
        result = await asyncio.wait_for(task, 5)
        assert result["feedback"]["status"] == "processed" and calls == 1
        assert await counts() == (1, 1, 1)
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_lost_wake_and_restart_discover_committed_queue(monkeypatch):
    old, _, identity, _ = await submit()
    old._wake.clear()
    replacement = owner.AlfredFeedbackService()
    finished = asyncio.Event()
    original = replacement._process_claimed_feedback

    async def complete(claim):
        result = await original(claim)
        finished.set()
        return result

    monkeypatch.setattr(replacement, "_process_claimed_feedback", complete)
    try:
        await replacement.start()
        await asyncio.wait_for(finished.wait(), 5)
        assert (await row(identity)).status == "processed"
        assert await counts() == (1, 1, 1)
    finally:
        await replacement.stop()


@pytest.mark.parametrize("interrupt", ["deadline", "cancel"])
async def test_interrupted_analysis_becomes_review_without_resend_or_training_rows(monkeypatch, interrupt):
    service, _, identity, _ = await submit()
    entered, never = asyncio.Event(), asyncio.Event()

    async def analyze(**kwargs):
        entered.set()
        await never.wait()
        return deepcopy(ANALYSIS)

    provider = AsyncMock(side_effect=analyze)
    monkeypatch.setattr(service, "_analyze_feedback", provider)
    if interrupt == "deadline": monkeypatch.setattr(owner, "FEEDBACK_ANALYSIS_TIMEOUT_SECONDS", 1)
    task = asyncio.create_task(service.process_feedback(identity))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if interrupt == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 3)
        else:
            await asyncio.wait_for(task, 3)
        saved = await row(identity)
        assert saved.status == "review_required"
        assert saved.analysis["recovery"]["reason"] == ("analysis_cancelled" if interrupt == "cancel" else "analysis_deadline_exceeded")
        assert await counts() == (1, 0, 0)
        assert await service.process_feedback(identity) is None
        assert provider.await_count == 1
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("mutation", ["stale_claim", "inactive", "deleted", "role_changed"])
async def test_late_provider_result_cannot_publish_after_claim_or_actor_changes(monkeypatch, mutation):
    service, user, identity, _ = await submit()
    entered, release = asyncio.Event(), asyncio.Event()

    async def analyze(**kwargs):
        entered.set()
        await release.wait()
        return deepcopy(ANALYSIS)

    monkeypatch.setattr(service, "_analyze_feedback", analyze)
    task = asyncio.create_task(service.process_feedback(identity))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with AsyncSessionLocal() as session:
            if mutation == "stale_claim":
                await session.execute(text("UPDATE alfred_feedback SET updated_at=clock_timestamp()-interval '4 minutes' WHERE id=:id"), {"id": identity})
            else:
                actor = await session.get(User, user.id)
                if mutation == "inactive": actor.is_active = False
                elif mutation == "role_changed": actor.role = UserRole.STANDARD
                else: await session.delete(actor)
            await session.commit()
        if mutation == "stale_claim":
            assert await owner.AlfredFeedbackService().review_interrupted_feedback() == 1
        release.set()
        await asyncio.wait_for(task, 5)
        saved = await row(identity)
        assert saved.status == "review_required"
        assert saved.analysis["recovery"]["reason"] == ("analysis_interrupted" if mutation == "stale_claim" else "actor_authority_changed")
        assert await counts() == (1, 0, 0)
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def reject_final_commit(identity):
    suffix = uuid.uuid4().hex
    function, trigger, sequence = ("iacs_p1_feedback_" + part + "_" + suffix for part in ("fn", "trg", "seq"))
    async with AsyncSessionLocal() as session:
        await session.execute(text(f"CREATE SEQUENCE {sequence}"))
        await session.execute(text(f"""CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.id='{identity}'::uuid AND NEW.status='processed' THEN
                PERFORM nextval('{sequence}');
                RAISE EXCEPTION 'synthetic feedback final commit failure' USING ERRCODE='23514'; END IF; RETURN NEW; END $$"""))
        await session.execute(text(f"CREATE CONSTRAINT TRIGGER {trigger} AFTER UPDATE ON alfred_feedback DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {function}()"))
        await session.commit()
    try: yield sequence
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(text(f"DROP TRIGGER {trigger} ON alfred_feedback"))
            await session.execute(text(f"DROP FUNCTION {function}()"))
            await session.execute(text(f"DROP SEQUENCE {sequence}"))
            await session.commit()


async def test_final_transaction_failure_leaves_no_orphan_lesson_or_eval():
    service, _, identity, _ = await submit()
    async with reject_final_commit(identity) as sequence:
        result = await service.process_feedback(identity)
        async with AsyncSessionLocal() as session:
            reached = (await session.execute(text(f"SELECT last_value,is_called FROM {sequence}"))).one()
            assert reached.is_called and reached.last_value == 1, "The real final commit failure must have been reached"
    assert result["feedback"]["status"] == "review_required"
    assert result["feedback"]["analysis"]["recovery"]["reason"] == "analysis_failed_before_commit"
    assert await counts() == (1, 0, 0)
    assert (await row(identity)).lesson_id is None
    assert await service.process_feedback(identity) is None


@pytest.mark.parametrize("state", ["received", "analyzing", "analysis_failed"])
async def test_old_feedback_is_reviewed_or_retained_without_automatic_analysis(state):
    service, _, identity, _ = await submit()
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE alfred_feedback SET status=:state,updated_at=clock_timestamp()-interval '4 minutes' WHERE id=:id"), {"state": state, "id": identity})
        await session.commit()
    assert await service.review_interrupted_feedback() == (0 if state == "analysis_failed" else 1)
    assert await service.process_feedback(identity) is None
    assert (await row(identity)).status == ("analysis_failed" if state == "analysis_failed" else "review_required")
    owner.AlfredFeedbackService._analyze_feedback.assert_not_awaited()


async def test_current_role_overrides_caller_hint_and_existing_session_privacy_is_preserved():
    service = owner.AlfredFeedbackService()
    user, answer = await turn(UserRole.STANDARD)
    result = await service.submit_feedback(assistant_message_id=str(answer.id), rating="up", reason=None,
        ideal_answer=None, source_channel="whatsapp", actor_user_id=str(user.id), actor_role="admin")
    assert result["feedback"]["actor_role"] == "standard"
    other, _ = await turn(UserRole.STANDARD)
    with pytest.raises(owner.AlfredFeedbackError, match="own Alfred responses"):
        await service.submit_feedback(assistant_message_id=str(answer.id), rating="up", reason=None,
            ideal_answer=None, source_channel="dashboard", user=other)
    assert await counts() == (1, 0, 0)


@pytest.mark.parametrize("mutation", ["inactive", "deleted", "role_changed"])
async def test_changed_actor_is_reviewed_before_private_context_reaches_provider(mutation):
    service, user, identity, _ = await submit()
    async with AsyncSessionLocal() as session:
        actor = await session.get(User, user.id)
        if mutation == "inactive": actor.is_active = False
        elif mutation == "role_changed": actor.role = UserRole.STANDARD
        else: await session.delete(actor)
        await session.commit()
    result = await service.process_feedback(identity)
    assert result["feedback"]["status"] == "review_required"
    assert result["feedback"]["analysis"]["recovery"]["reason"] == "actor_authority_changed"
    owner.AlfredFeedbackService._analyze_feedback.assert_not_awaited()
    owner.generate_embedding.assert_not_awaited()
    assert await counts() == (1, 0, 0)
