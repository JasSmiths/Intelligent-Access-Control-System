"""Inert incoming-message store checkpoint; no webhook or handler cutover."""
from test_recovery_boundaries import _bounded, isolated_resources as isolated_resources

import asyncio
from dataclasses import replace
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal
from app.models import ProcessedMessagingMessage
from app.services.messaging.incoming_messages import IncomingMessageClaimLost, IncomingMessageStore

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def empty_inbox(isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE processed_messaging_messages"))
        await session.commit()


def envelope(body="Synthetic private visitor text"):
    return {"message": {"type": "text", "text": {"body": body}}}


def binding(kind="admin"):
    return {"kind": kind, "user_id": str(uuid.UUID(int=17)), "auth_version": 1} if kind == "admin" else {
        "kind": "visitor", "pass_id": str(uuid.UUID(int=18))}


async def accept(*, message_id=None, sender="15550000001", channel="synthetic-channel", kind="admin", body=None, debounce=0):
    async with AsyncSessionLocal() as session:
        identity = await IncomingMessageStore().accept_in_session(session, provider="whatsapp",
            provider_message_id=message_id or f"synthetic-{uuid.uuid4()}", provider_channel_id=channel,
            author_provider_id=sender, envelope=envelope(body or "Synthetic text"), routing_context=binding(kind),
            received_at=await session.scalar(select(func.clock_timestamp())), debounce_seconds=debounce)
        await session.commit()
        return identity


async def finish(claim):
    async with AsyncSessionLocal() as session:
        await IncomingMessageStore().finish_handling_in_session(session, claim, {"handled": True})
        await session.commit()


async def test_acceptance_is_participant_and_restart_can_claim_committed_input():
    store = IncomingMessageStore()
    async with AsyncSessionLocal() as session:
        identity = await store.accept_in_session(session, provider="whatsapp", provider_message_id="synthetic-rollback",
            provider_channel_id="synthetic-channel", author_provider_id="15550000001",
            envelope=envelope(), routing_context=binding(), received_at=None)
        assert await store.get(identity) is None
        await session.rollback()
    assert await store.claim() is None
    identity = await accept()
    restarted = IncomingMessageStore()
    claim = await restarted.claim()
    assert claim.message_ids == (identity,)
    row = await restarted.get(identity)
    assert row.state == "processing" and row.envelope["message"]["text"]["body"] == "Synthetic text"
    assert row.claimed_at and row.claim_token == claim.token
    await finish(claim)
    assert (await restarted.get(identity)).state == "handled"
    assert await restarted.claim(identity) is None


async def test_duplicate_acceptance_never_rebinds_or_reactivates():
    identity = await accept(message_id="synthetic-duplicate")
    assert await accept(message_id="synthetic-duplicate", body="Replacement must not overwrite") == identity
    store = IncomingMessageStore()
    assert (await store.get(identity)).envelope == envelope("Synthetic text")
    claim = await store.claim(identity)
    await finish(claim)
    assert await accept(message_id="synthetic-duplicate") == identity
    assert (await store.get(identity)).state == "handled"
    with pytest.raises(ValueError, match="rebound"):
        await accept(message_id="synthetic-duplicate", sender="15550000002")


async def test_historical_processed_row_remains_inert_after_duplicate():
    async with AsyncSessionLocal() as session:
        row = ProcessedMessagingMessage(provider="whatsapp", provider_message_id="synthetic-historical",
            provider_channel_id="synthetic-channel", author_provider_id="15550000001")
        session.add(row)
        await session.commit()
        identity = row.id
    assert await accept(message_id="synthetic-historical") == identity
    row = await IncomingMessageStore().get(identity)
    assert row.recovery_version is None and row.state is None and row.envelope is None
    assert await IncomingMessageStore().claim(identity) is None


async def test_standard_discord_reader_is_retained_without_admin_authority():
    async with AsyncSessionLocal() as session:
        identity = await IncomingMessageStore().accept_in_session(session, provider="discord",
            provider_message_id="synthetic-discord-message", provider_channel_id="synthetic-discord-channel",
            author_provider_id="synthetic-discord-reader", envelope=envelope("Synthetic status question"),
            routing_context={"kind": "standard", "user_id": None}, received_at=None)
        await session.commit()
    store = IncomingMessageStore()
    claim = await store.claim(identity)
    assert claim.provider == "discord" and claim.message_ids == (identity,)
    assert (await store.get(identity)).routing_context == {"kind": "standard", "user_id": None}
    await finish(claim)


async def test_concurrent_intake_and_fresh_claims_keep_one_owner():
    identities = await _bounded(asyncio.gather(*[accept(message_id="synthetic-concurrent") for _ in range(3)]))
    assert len(set(identities)) == 1
    ready = asyncio.Event()
    async def worker():
        await ready.wait()
        return await IncomingMessageStore().claim(identities[0])
    tasks = [asyncio.create_task(worker()) for _ in range(3)]
    ready.set()
    claims = await _bounded(asyncio.gather(*tasks))
    assert sum(claim is not None for claim in claims) == 1


async def test_visitor_debounce_survives_restart_and_preserves_all_input_rows():
    first = await accept(kind="visitor", body="First part", debounce=2.5)
    second = await accept(kind="visitor", body="Second part", debounce=2.5)
    store = IncomingMessageStore()
    assert await store.claim() is None
    a, b = await store.get(first), await store.get(second)
    assert a.available_at == b.available_at
    async with AsyncSessionLocal() as session:
        await session.execute(update(ProcessedMessagingMessage).values(available_at=text("clock_timestamp() - interval '1 second'")))
        await session.commit()
    claim = await IncomingMessageStore().claim()
    assert set(claim.message_ids) == {first, second}
    assert (await store.get(first)).envelope == envelope("First part")
    assert (await store.get(second)).envelope == envelope("Second part")
    await finish(claim)
    assert (await store.get(first)).state == (await store.get(second)).state == "handled"


async def test_more_than_eight_visitor_messages_are_not_silently_dropped():
    identities = [await accept(kind="visitor", body=f"Part {index}") for index in range(10)]
    store = IncomingMessageStore()
    first = await store.claim()
    assert len(first.message_ids) == 8
    assert await store.claim() is None  # The same visitor never has concurrent handlers.
    await finish(first)
    second = await store.claim()
    assert len(second.message_ids) == 2
    assert set(first.message_ids) | set(second.message_ids) == set(identities)
    await finish(second)


async def test_explicit_newer_hint_cannot_overtake_earlier_message():
    first, second = await accept(), await accept()
    store = IncomingMessageStore()
    assert await store.claim(second) is None
    await finish(await store.claim(first))
    assert (await store.claim(second)).message_ids == (second,)


async def test_visitor_text_batch_cannot_overtake_intervening_button():
    first = await accept(kind="visitor")
    async with AsyncSessionLocal() as session:
        button = await IncomingMessageStore().accept_in_session(session, provider="whatsapp",
            provider_message_id="synthetic-button", provider_channel_id="synthetic-channel", author_provider_id="15550000001",
            envelope={"message": {"type": "interactive", "interactive": {"id": "synthetic"}}},
            routing_context=binding("visitor"), received_at=None)
        await session.commit()
    third = await accept(kind="visitor")
    store = IncomingMessageStore()
    batch = await store.claim()
    assert batch.message_ids == (first,)
    await finish(batch)
    batch = await store.claim()
    assert batch.message_ids == (button,)
    await finish(batch)
    assert (await store.claim()).message_ids == (third,)


async def test_expired_processing_is_review_only_and_cannot_be_taken_over():
    identity = await accept()
    store = IncomingMessageStore()
    claim = await store.claim(identity)
    async with AsyncSessionLocal() as session:
        await session.execute(update(ProcessedMessagingMessage).where(ProcessedMessagingMessage.id == identity)
            .values(lease_expires_at=text("clock_timestamp() - interval '1 second'")))
        await session.commit()
    assert await IncomingMessageStore().claim(identity) is None
    row = await store.get(identity)
    assert row.state == "review_required" and row.review_reason == "processing_interrupted"
    assert row.envelope == envelope("Synthetic text") and row.batch_id == claim.batch_id
    with pytest.raises(IncomingMessageClaimLost):
        await finish(claim)
    assert await accept(message_id=row.provider_message_id) == identity
    assert await store.claim(identity) is None


async def test_reply_checkpoint_commits_before_send_and_retains_exact_stable_identity():
    identity, store = await accept(), IncomingMessageStore()
    claim = await store.claim(identity)
    async with AsyncSessionLocal() as session:
        reply = await store.begin_reply_in_session(session, claim, index=0, recipient="15550000001", payload={"type": "text"})
        assert (await store.get(identity)).reply_plan == []
        await session.commit()
    assert reply["operation_id"] == str(uuid.uuid5(claim.batch_id, "reply:0"))
    # This is the inert transmission boundary; a fresh reader sees attempting.
    assert (await store.get(identity)).reply_plan[0]["state"] == "attempting"
    assert await store.record_reply_outcome(claim, 0, delivery="accepted", result={"provider_message_id": "synthetic-reply"})
    async with AsyncSessionLocal() as session:
        with pytest.raises(IncomingMessageClaimLost, match="already used"):
            await store.begin_reply_in_session(session, claim, index=0, recipient="15550000001", payload={"type": "text"})
    await finish(claim)
    row = await store.get(identity)
    assert row.state == "handled" and row.reply_plan[0]["result"]["provider_message_id"] == "synthetic-reply"


@pytest.mark.parametrize("interruption", ["cancel", "expired"])
async def test_attempted_reply_becomes_unknown_and_is_never_resent(interruption):
    identity, store = await accept(), IncomingMessageStore()
    claim = await store.claim(identity)
    async with AsyncSessionLocal() as session:
        await store.begin_reply_in_session(session, claim, index=0, recipient="15550000001", payload={"type": "text"})
        await session.commit()
    if interruption == "cancel":
        await store.interrupt(claim, "handler_cancelled")
    else:
        async with AsyncSessionLocal() as session:
            await session.execute(update(ProcessedMessagingMessage).where(ProcessedMessagingMessage.id == identity)
                .values(lease_expires_at=text("clock_timestamp() - interval '1 second'")))
            await session.commit()
        assert await IncomingMessageStore().claim(identity) is None
    assert await store.record_reply_outcome(claim, 0, delivery="accepted") is False
    row = await store.get(identity)
    assert row.state == "review_required" and row.reply_plan[0]["state"] == "unknown"
    assert row.reply_plan[0]["operation_id"] == str(uuid.uuid5(claim.batch_id, "reply:0"))
    assert await store.claim(identity) is None


async def test_explicit_unknown_reply_is_retained_and_blocks_further_sends():
    identity, store = await accept(), IncomingMessageStore()
    claim = await store.claim(identity)
    async with AsyncSessionLocal() as session:
        await store.begin_reply_in_session(session, claim, index=0, recipient="15550000001", payload={"type": "text"})
        await session.commit()
    assert await store.record_reply_outcome(claim, 0, delivery="unknown", result={"reason": "response_lost"})
    async with AsyncSessionLocal() as session:
        with pytest.raises(IncomingMessageClaimLost, match="unresolved"):
            await store.begin_reply_in_session(session, claim, index=1, recipient="15550000001", payload={"type": "text"})
    await finish(claim)
    assert (await store.get(identity)).state == "review_required"


async def test_batch_subset_or_forged_token_cannot_complete_other_members():
    await accept(kind="visitor")
    await accept(kind="visitor")
    claim = await IncomingMessageStore().claim()
    for forged in [replace(claim, token=uuid.uuid4()), replace(claim, message_ids=(claim.batch_id,))]:
        with pytest.raises(IncomingMessageClaimLost):
            await finish(forged)
    await finish(claim)


async def test_interrupt_rejects_partial_or_wrong_peer_claim_without_mutation():
    await accept(kind="visitor")
    await accept(kind="visitor")
    store = IncomingMessageStore()
    claim = await store.claim()
    for forged in [replace(claim, message_ids=(claim.batch_id,)), replace(claim, author_id="wrong-peer"),
                   replace(claim, token=uuid.uuid4())]:
        assert await store.interrupt(forged, "must_not_mutate") is False
        for identity in claim.message_ids:
            assert (await store.get(identity)).state == "processing"
    assert await store.interrupt(claim, "handler_cancelled") is True
    for identity in claim.message_ids:
        row = await store.get(identity)
        assert row.state == "review_required" and row.review_reason == "handler_cancelled"


async def test_crossed_peer_acceptance_is_refused_within_one_transaction():
    store = IncomingMessageStore()
    async with AsyncSessionLocal() as session:
        async def receive(sender, message_id):
            return await store.accept_in_session(session, provider="whatsapp", provider_message_id=message_id,
                provider_channel_id="synthetic-channel", author_provider_id=sender,
                envelope=envelope(), routing_context=binding(), received_at=None)
        first = await receive("15550000001", "synthetic-first")
        assert await receive("15550000001", "synthetic-second")
        with pytest.raises(ValueError, match="separate transactions"):
            await receive("15550000002", "synthetic-other")
        await session.rollback()
        assert await store.get(first) is None
        # The guard follows the transaction, not the lifetime of a reused session.
        await receive("15550000002", "synthetic-other")
        await session.commit()
        await receive("15550000001", "synthetic-first")
        await session.commit()


async def test_busy_sender_queue_does_not_starve_another_sender():
    store = IncomingMessageStore()
    busy = await accept()
    assert await store.claim(busy)
    for _ in range(70):
        await accept()
    other = await accept(sender="15550000002")
    assert (await store.claim()).message_ids == (other,)


async def test_historical_review_backlog_is_not_an_execution_queue():
    async with AsyncSessionLocal() as session:
        session.add_all([ProcessedMessagingMessage(provider="whatsapp", provider_message_id=f"old-{index}")
                        for index in range(100)])
        await session.commit()
    identity = await accept()
    assert (await IncomingMessageStore().claim()).message_ids == (identity,)


@pytest.mark.parametrize("body", [{}, {"message": "invalid"}, {"message": {"body": "x" * 40000}}])
async def test_malformed_or_unbounded_input_cannot_be_accepted(body):
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError):
            await IncomingMessageStore().accept_in_session(session, provider="whatsapp", provider_message_id="synthetic-bad",
                provider_channel_id="synthetic-channel", author_provider_id="15550000001",
                envelope=body, routing_context=binding(), received_at=None)
        await session.rollback()
    assert await IncomingMessageStore().claim() is None
