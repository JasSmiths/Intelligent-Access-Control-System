"""Automation journal contracts using fresh sessions in disposable PostgreSQL.

Collection imports the strict synthetic namespace/socket guard before IACS.
No provider, worker startup, transport or application execution is invoked here.
"""

from test_recovery_boundaries import _bounded, isolated_resources as isolated_resources

import asyncio
import copy
from datetime import UTC, datetime
import uuid

import pytest
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal
from app.models import AutomationRule, AutomationRun
from app.services.automation_execution import AutomationClaimLost, AutomationRunStore, MAX_CLAIM_SCAN, occurrence_key

pytestmark = pytest.mark.asyncio


def actions(*kinds):
    return [{"action": {"id": f"a{index}", "type": kind, "config": {}}}
            for index, kind in enumerate(kinds or ("gate.open",))]


async def rule():
    async with AsyncSessionLocal() as session:
        row = AutomationRule(name="Synthetic journal rule", is_active=True, triggers=[], trigger_keys=[], conditions=[], actions=[])
        session.add(row)
        await session.commit()
        return row.id


async def reserve(store, rule_id, *, planned=None, origin=None, trigger="time.every_x", context=None, commit=True):
    async with AsyncSessionLocal() as session:
        identity = await store.reserve(
            session, rule_id=rule_id, trigger_key=trigger,
            occurrence=origin or occurrence_key("test", str(uuid.uuid4()), rule_id, trigger),
            context=context or {"version": 1, "rule_fingerprint": "synthetic"},
            planned=planned if planned is not None else actions(), trigger_payload={},
            actor="Automation Engine", source="synthetic",
        )
        if commit:
            await session.commit()
        else:
            await session.rollback()
        return identity


async def begin(store, claimed, index=0):
    async with AsyncSessionLocal() as session:
        result = await store.begin_action(session, claimed.id, claimed.claim_token, index)
        await session.commit()
        return result


async def expire(identity):
    async with AsyncSessionLocal() as session:
        await session.execute(update(AutomationRun).where(AutomationRun.id == identity).values(
            lease_expires_at=text("clock_timestamp() - interval '1 second'")))
        await session.commit()


async def test_origin_reservation_is_invisible_until_its_transaction_commits():
    store, rule_id = AutomationRunStore(), await rule()
    async with AsyncSessionLocal() as owner:
        identity = await store.reserve(owner, rule_id=rule_id, trigger_key="time.every_x", occurrence="synthetic:transaction",
            context={"version": 1}, planned=actions(), trigger_payload={}, actor="Automation Engine", source="synthetic")
        async with AsyncSessionLocal() as observer:
            assert await observer.get(AutomationRun, identity) is None
        await owner.rollback()
    with pytest.raises(LookupError):
        await store.get(identity)
    assert await store.claim() is None


async def test_duplicate_occurrence_retains_original_plan_and_operation_identity():
    store, rule_id = AutomationRunStore(), await rule()
    original = await reserve(store, rule_id, origin="synthetic:duplicate")
    before = await store.get(original)
    repeated = await reserve(store, rule_id, origin="synthetic:duplicate", planned=actions("garage_door.close"), context={"version": 1, "changed": True})
    after = await store.get(repeated)
    assert repeated == original and after.action_plan == before.action_plan and after.context == before.context
    assert after.action_plan[0]["action"]["type"] == "gate.open"


@pytest.mark.parametrize("collision", ["rule", "trigger"])
async def test_same_occurrence_cannot_be_rebound_to_different_rule_or_trigger(collision):
    store, rule_id = AutomationRunStore(), await rule()
    identity = await reserve(store, rule_id, origin="synthetic:collision")
    other_rule = await rule() if collision == "rule" else rule_id
    with pytest.raises(ValueError, match="cannot be rebound"):
        await reserve(store, other_rule, origin="synthetic:collision", trigger="visitor_pass.used" if collision == "trigger" else "time.every_x")
    retained = await store.get(identity)
    assert retained.rule_id == rule_id and retained.trigger_key == "time.every_x"


async def test_fresh_session_workers_acquire_only_one_claim():
    first, second = AutomationRunStore(), AutomationRunStore()
    identity = await reserve(first, await rule())
    claims = await _bounded(asyncio.gather(first.claim(identity), second.claim(identity)))
    assert sum(item is not None for item in claims) == 1
    retained = await first.get(identity)
    assert retained.status == "processing" and retained.claim_count == 1


async def test_unattempted_expired_claim_changes_token_and_retains_action_identity():
    store = AutomationRunStore()
    identity = await reserve(store, await rule())
    first = await store.claim(identity)
    await expire(identity)
    second = await AutomationRunStore().claim(identity)
    assert second.claim_token != first.claim_token and second.action_plan == first.action_plan
    with pytest.raises(AutomationClaimLost):
        await begin(store, first)
    assert (await store.get(identity)).action_plan[0]["state"] == "pending"


async def test_attempt_checkpoint_commits_before_sink_and_late_worker_cannot_replace_unknown():
    store = AutomationRunStore()
    identity = await reserve(store, await rule())
    claimed = await store.claim(identity)
    attempt = await begin(store, claimed)
    assert (await AutomationRunStore().get(identity)).action_plan[0]["state"] == "attempting"
    sinks = [attempt["operation_id"]]  # inert provider sink after committed attempting
    await expire(identity)
    assert await AutomationRunStore().claim(identity) is None
    held = await store.get(identity)
    assert held.status == "review_required" and held.action_plan[0]["state"] == "unknown"
    with pytest.raises(AutomationClaimLost):
        await store.finish_action(identity, claimed.claim_token, 0, {"accepted": True}, state="succeeded")
    assert len(sinks) == 1 and (await store.get(identity)).action_plan[0]["state"] == "unknown"


@pytest.mark.parametrize("notice_index", [1, 2])
async def test_recovery_skips_further_hardware_but_keeps_independent_notifications_and_review_marker(notice_index):
    store = AutomationRunStore()
    ordered = ["gate.open", "garage_door.open", "garage_door.open"]
    ordered[notice_index] = "integration.whatsapp.send_message"
    identity = await reserve(store, await rule(), planned=actions(*ordered))
    first = await store.claim(identity)
    await begin(store, first)
    await expire(identity)
    recovered = await AutomationRunStore().claim(identity)
    states = ["unknown", "skipped", "skipped"]
    states[notice_index] = "pending"
    assert [item["state"] for item in recovered.action_plan] == states
    assert recovered.action_plan[3 - notice_index]["result"]["reason"] == "earlier_action_unknown"
    assert store._valid_plan(identity, recovered.action_plan)
    await begin(store, recovered, notice_index)
    await store.finish_action(identity, recovered.claim_token, notice_index, {"status": "success", "sent": True}, state="succeeded")
    final = await store.finish(identity, recovered.claim_token)
    assert final.status == "review_required" and final.review_reason == "action_outcome_unknown"
    assert final.action_plan[notice_index]["state"] == "succeeded"
    assert await store.claim(identity) is None


async def test_accepted_first_action_is_not_repeated_after_restart_before_second():
    store = AutomationRunStore()
    identity = await reserve(store, await rule(), planned=actions("integration.whatsapp.send_message", "integration.whatsapp.send_message"))
    first = await store.claim(identity)
    first_plan = copy.deepcopy(first.action_plan)
    await begin(store, first)
    await store.finish_action(identity, first.claim_token, 0, {"status": "success", "accepted": True}, state="succeeded")
    await expire(identity)
    recovered = await AutomationRunStore().claim(identity)
    assert recovered.action_plan[0]["state"] == "succeeded" and recovered.action_plan[1]["state"] == "pending"
    assert [item["operation_id"] for item in recovered.action_plan] == [item["operation_id"] for item in first_plan]
    with pytest.raises(AutomationClaimLost):
        await begin(store, recovered, 0)
    await begin(store, recovered, 1)
    await store.finish_action(identity, recovered.claim_token, 1, {"status": "success"}, state="succeeded")
    assert (await store.finish(identity, recovered.claim_token)).status == "success"


async def test_definite_failure_stops_and_skip_continues():
    store, rule_id = AutomationRunStore(), await rule()
    failed_id = await reserve(store, rule_id, planned=actions("gate.open", "integration.whatsapp.send_message"))
    failed = await store.claim(failed_id)
    await begin(store, failed)
    await store.finish_action(failed_id, failed.claim_token, 0, {"status": "failed", "delivery": "rejected"}, state="failed")
    final = await store.finish(failed_id, failed.claim_token)
    assert final.status == "failed" and final.action_plan[1]["result"]["reason"] == "earlier_action_failed"
    skipped_id = await reserve(store, rule_id, planned=actions("gate.open", "integration.whatsapp.send_message"))
    skipped = await store.claim(skipped_id)
    async with AsyncSessionLocal() as session:
        await store.complete_local_action(session, skipped_id, skipped.claim_token, 0, {"status": "skipped", "reason": "expired"}, state="skipped")
        await session.commit()
    assert (await store.get(skipped_id)).action_plan[1]["state"] == "pending"
    await begin(store, skipped, 1)


async def test_actions_cannot_run_out_of_order_or_be_declared_locally_sent():
    store = AutomationRunStore()
    identity = await reserve(store, await rule(), planned=actions("gate.open", "garage_door.open"))
    claimed = await store.claim(identity)
    with pytest.raises(AutomationClaimLost, match="Earlier"):
        await begin(store, claimed, 1)
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError, match="transactional notification"):
            await store.complete_local_action(session, identity, claimed.claim_token, 0, {"accepted": True}, state="succeeded")
    assert all(item["state"] == "pending" for item in (await store.get(identity)).action_plan)


async def test_action_checkpoint_participates_in_local_mutation_rollback():
    store = AutomationRunStore()
    identity = await reserve(store, await rule(), planned=actions("notification.enable"))
    claimed = await store.claim(identity)
    async with AsyncSessionLocal() as session:
        await store.complete_local_action(session, identity, claimed.claim_token, 0, {"status": "success"}, state="succeeded")
        await session.rollback()
    assert (await store.get(identity)).action_plan[0]["state"] == "pending"
    async with AsyncSessionLocal() as session:
        await store.complete_local_action(session, identity, claimed.claim_token, 0, {"status": "success"}, state="succeeded")
        await session.commit()
    assert (await store.finish(identity, claimed.claim_token)).status == "success"


async def test_queue_discovery_passes_more_than_one_hundred_review_only_records():
    store, rule_id = AutomationRunStore(), await rule()
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        for index in range(125):
            session.add(AutomationRun(rule_id=rule_id, trigger_key="time.every_x", status="review_required",
                started_at=now, queued_at=now, recovery_version=1, occurrence_key=f"synthetic:old-review:{index}",
                context={"version": 1}, action_plan=[], review_reason="action_outcome_unknown"))
        await session.commit()
    fresh = await reserve(store, rule_id)
    claimed = await store.claim()
    assert claimed and claimed.id == fresh


async def test_poisoned_queue_candidates_make_bounded_progress_to_next_pending():
    store, rule_id = AutomationRunStore(), await rule()
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        for index in range(MAX_CLAIM_SCAN + 1):
            session.add(AutomationRun(rule_id=rule_id, trigger_key="time.every_x", status="queued", started_at=now,
                queued_at=now, recovery_version=1, occurrence_key=f"synthetic:missing-snapshot:{index}",
                context={}, action_plan=None))
        await session.commit()
    fresh = await reserve(store, rule_id)
    assert await store.claim() is None  # bounded scan terminalized exactly one batch
    second = await store.claim()
    assert second and second.id == fresh
    async with AsyncSessionLocal() as session:
        reviewed = await session.scalar(select(func.count()).select_from(AutomationRun).where(AutomationRun.status == "review_required"))
        assert reviewed == MAX_CLAIM_SCAN + 1


@pytest.mark.parametrize("status", ["claimed", "running", "queued", "processing"])
async def test_historical_unversioned_work_never_gains_execution_authority(status):
    store, rule_id = AutomationRunStore(), await rule()
    async with AsyncSessionLocal() as session:
        row = AutomationRun(rule_id=rule_id, trigger_key="time.every_x", status=status, started_at=datetime.now(tz=UTC),
                            context={}, action_results=[])
        session.add(row)
        await session.commit()
        identity = row.id
    assert await store.claim(identity) is None and await store.claim() is None
    historical = await store.get(identity)
    assert historical.status == status and historical.recovery_version is None and historical.claim_token is None


@pytest.mark.parametrize("version", [1, 2])
async def test_downgrade_refuses_any_non_null_recovery_version_and_preserves_checkpoints(version):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError

    store = AutomationRunStore()
    identity = await reserve(store, await rule())
    async with AsyncSessionLocal() as session:
        await session.execute(update(AutomationRun).where(AutomationRun.id == identity).values(recovery_version=version))
        await session.commit()
    before = await store.get(identity)
    revisions = list((Path(__file__).resolve().parents[2] / "backend/alembic/versions").glob("20260912_0005*.py"))
    assert len(revisions) == 1
    spec = importlib.util.spec_from_file_location("synthetic_automation_recovery_migration", revisions[0])
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def downgrade(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    async with AsyncSessionLocal() as session:
        connection = await session.connection()
        with pytest.raises(DBAPIError, match="Durable automation work exists"):
            await connection.run_sync(downgrade)
        await session.rollback()
    after = await store.get(identity)
    assert after.recovery_version == version and after.action_plan == before.action_plan
    assert after.occurrence_key == before.occurrence_key


@pytest.mark.parametrize("corruption", ["operation", "idempotency", "double_attempt", "overtaken_pending", "failed_then_pending", "index"])
async def test_malformed_v1_plan_is_reviewed_without_dispatch(corruption):
    store = AutomationRunStore()
    identity = await reserve(store, await rule(), planned=actions("gate.open", "integration.whatsapp.send_message"))
    row = await store.get(identity)
    plan = copy.deepcopy(row.action_plan)
    if corruption == "operation":
        plan[0]["operation_id"] = str(uuid.uuid4())
    elif corruption == "idempotency":
        plan[0]["idempotency_key"] = "new-random-retry-identity"
    elif corruption == "double_attempt":
        for item in plan:
            item.update(state="attempting", attempted_at=datetime.now(tz=UTC).isoformat())
    elif corruption == "overtaken_pending":
        plan[1].update(state="succeeded", result={"status": "success"})
    elif corruption == "failed_then_pending":
        plan[0].update(state="failed", result={"status": "failed"})
    else:
        plan[0]["index"] = 1
    async with AsyncSessionLocal() as session:
        await session.execute(update(AutomationRun).where(AutomationRun.id == identity).values(action_plan=plan))
        await session.commit()
    assert await store.claim(identity) is None
    held = await store.get(identity)
    assert held.status == "review_required" and held.review_reason == "execution_snapshot_unavailable"
    assert held.action_plan == plan and held.claim_count == 0
