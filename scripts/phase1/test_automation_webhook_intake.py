"""Webhook source acceptance and automation handoff share one isolated transaction."""
from test_recovery_boundaries import _bounded, isolated_resources as isolated_resources

import asyncio
from datetime import UTC, datetime
import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import func, select, text

from app.db.session import AsyncSessionLocal
from app.models import AutomationRule, AutomationRun, AutomationWebhookNonce, AutomationWebhookSender, NotificationRun
from app.services import automation_integration_actions, automations

pytestmark = pytest.mark.asyncio
KEY = "whk_synthetic_webhook_key_with_sufficient_length_for_tests"
SOURCE = "192.0.2.10"
BODY = b'{"message":"Synthetic webhook","eligible_rule_ids":["body-is-not-authority"]}'


async def setup(monkeypatch, *, require_hmac=True, trigger="webhook.received", sources=None):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE automation_webhook_senders, automation_webhook_nonces, notification_runs CASCADE"))
        row = AutomationRule(name="Synthetic webhook rule", is_active=True,
            triggers=[{"id": "receive", "type": trigger, "config": {
                "webhook_key": KEY, "require_hmac": require_hmac, "allowed_source_ips": sources or [],
            }}], trigger_keys=[trigger], conditions=[], actions=[{
                "id": "notify", "type": "integration.whatsapp.send_message", "config": {"target_mode": "all"}}])
        session.add(row)
        await session.commit()
        identity = row.id
    service = automations.AutomationService()
    monkeypatch.setattr(service.dispatcher, "run_once", AsyncMock(return_value=False))
    monkeypatch.setattr(automation_integration_actions, "integration_action_status",
        AsyncMock(return_value=SimpleNamespace(enabled=True, disabled_reason=None)))
    return service, identity


def request(*, nonce=None, signed=True):
    nonce = nonce or str(uuid.uuid4())
    timestamp = str(int(datetime.now(tz=UTC).timestamp()))
    signature = hmac.new(KEY.encode(), f"{timestamp}.{nonce}.".encode() + BODY, hashlib.sha256).hexdigest()
    return {"webhook_key": KEY, "payload": {"message": "Synthetic webhook", "eligible_rule_ids": ["forged"]},
            "source_ip": SOURCE, "raw_body": BODY, "nonce": nonce,
            "signature_timestamp": timestamp, "signature": signature if signed else None}


async def stored():
    async with AsyncSessionLocal() as session:
        return list((await session.scalars(select(AutomationRun).order_by(AutomationRun.id))).all()), \
            list((await session.scalars(select(AutomationWebhookSender))).all()), \
            list((await session.scalars(select(AutomationWebhookNonce))).all())


async def test_nonce_sender_and_occurrence_commit_once_on_duplicate(monkeypatch):
    service, rule_id = await setup(monkeypatch)
    incoming = request()
    result = await service.handle_webhook(**incoming)
    assert result["accepted"] and result["hmac_verified"] and result["new_sender"]
    with pytest.raises(automations.AutomationError, match="already used"):
        await service.handle_webhook(**incoming)
    runs, senders, nonces = await stored()
    assert len(runs) == len(senders) == len(nonces) == 1
    assert senders[0].event_count == 1 and runs[0].rule_id == rule_id
    assert runs[0].status == "queued" and runs[0].claim_token is None


async def test_concurrent_same_signed_request_has_one_accepted_origin(monkeypatch):
    first, _ = await setup(monkeypatch)
    second = automations.AutomationService()
    monkeypatch.setattr(second.dispatcher, "run_once", AsyncMock(return_value=False))
    incoming = request()
    outcomes = await _bounded(asyncio.gather(first.handle_webhook(**incoming), second.handle_webhook(**incoming), return_exceptions=True))
    assert sum(isinstance(item, dict) and item["accepted"] for item in outcomes) == 1
    assert sum(isinstance(item, automations.AutomationError) for item in outcomes) == 1
    runs, senders, nonces = await stored()
    assert len(runs) == len(senders) == len(nonces) == 1 and senders[0].event_count == 1


async def test_distinct_concurrent_unsigned_requests_get_stable_sender_sequence(monkeypatch):
    first, _ = await setup(monkeypatch, require_hmac=False, sources=[SOURCE])
    second = automations.AutomationService()
    monkeypatch.setattr(second.dispatcher, "run_once", AsyncMock(return_value=False))
    results = await _bounded(asyncio.gather(first.handle_webhook(**request(signed=False)), second.handle_webhook(**request(signed=False))))
    assert sum(result["new_sender"] for result in results) == 1
    runs, senders, nonces = await stored()
    assert len(runs) == 2 and len(senders) == 1 and not nonces
    assert senders[0].event_count == 2 and len({run.occurrence_key for run in runs}) == 2


async def test_intake_failure_rolls_back_nonce_sender_and_all_occurrences(monkeypatch):
    service, _ = await setup(monkeypatch)
    original = automations.reserve_trigger
    async def fail_after_reservation(*args, **kwargs):
        identities = await original(*args, **kwargs)
        assert identities
        raise RuntimeError("Synthetic origin transaction failure")
    monkeypatch.setattr(automations, "reserve_trigger", fail_after_reservation)
    incoming = request()
    with pytest.raises(RuntimeError, match="Synthetic origin"):
        await service.handle_webhook(**incoming)
    assert await stored() == ([], [], [])
    monkeypatch.setattr(automations, "reserve_trigger", original)
    assert (await service.handle_webhook(**incoming))["accepted"]
    assert all(len(group) == 1 for group in await stored())


async def test_lost_wakeup_keeps_durable_occurrence_recoverable(monkeypatch):
    service, _ = await setup(monkeypatch)
    def failed_wakeup():
        raise RuntimeError("Synthetic lost wakeup")
    monkeypatch.setattr(service.dispatcher, "wake", failed_wakeup)
    with pytest.raises(RuntimeError, match="Synthetic lost wakeup"):
        await service.handle_webhook(**request())
    runs, senders, nonces = await stored()
    assert len(runs) == len(senders) == len(nonces) == 1 and runs[0].status == "queued"
    fresh = automations.AutomationService()
    assert await fresh.dispatcher.run_once(runs[0].id)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 1
        recovered = await session.get(AutomationRun, runs[0].id)
        assert recovered.status == "success" and recovered.action_plan[0]["state"] == "succeeded"


async def test_shared_key_never_borrows_another_rules_source_authorization(monkeypatch):
    service, allowed = await setup(monkeypatch, require_hmac=False, sources=[SOURCE])
    async with AsyncSessionLocal() as session:
        other = AutomationRule(name="Synthetic restricted source", is_active=True,
            triggers=[{"id": "receive", "type": "webhook.received", "config": {
                "webhook_key": KEY, "allowed_source_ips": ["198.51.100.20"], "require_hmac": False}}],
            trigger_keys=["webhook.received"], conditions=[], actions=[{"id": "notice", "type": "integration.whatsapp.send_message", "config": {"target_mode": "all"}}])
        session.add(other)
        await session.commit()
        denied = other.id
    incoming = request(signed=False)
    incoming["payload"]["eligible_rule_ids"] = [str(denied)]
    result = await service.handle_webhook(**incoming)
    assert len(result["runs"]) == 1
    runs, _, _ = await stored()
    assert [row.rule_id for row in runs] == [allowed]
    async with AsyncSessionLocal() as session:
        rule = await session.get(AutomationRule, allowed)
        rule.triggers = [{**rule.triggers[0], "config": {**rule.triggers[0]["config"], "allowed_source_ips": ["198.51.100.20"]}}]
        await session.commit()
    assert await automations.AutomationService().dispatcher.run_once(runs[0].id)
    async with AsyncSessionLocal() as session:
        retained = await session.get(AutomationRun, runs[0].id)
        assert retained.action_plan[0]["state"] == "skipped" and retained.review_reason
        assert retained.action_results[0]["reason"] == "rule_changed_since_occurrence"
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0


async def test_unrecognized_and_new_sender_occurrences_share_acceptance_transaction(monkeypatch):
    service, unknown_rule = await setup(monkeypatch, require_hmac=False, trigger="webhook.unrecognized")
    async with AsyncSessionLocal() as session:
        new_sender = AutomationRule(name="Synthetic new sender rule", is_active=True,
            triggers=[{"id": "sender", "type": "webhook.new_sender", "config": {}}],
            trigger_keys=["webhook.new_sender"], conditions=[], actions=[])
        session.add(new_sender)
        await session.commit()
        new_rule = new_sender.id
    first = await service.handle_webhook(**request(signed=False))
    second = await service.handle_webhook(**request(signed=False))
    assert len(first["runs"]) == 2 and len(second["runs"]) == 1
    runs, senders, nonces = await stored()
    assert not nonces and len(senders) == 1 and senders[0].event_count == 2
    assert [row.rule_id for row in runs].count(unknown_rule) == 2
    assert [row.rule_id for row in runs].count(new_rule) == 1
