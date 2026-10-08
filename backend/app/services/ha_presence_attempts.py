"""Dormant one-attempt HA repository; no provider capability or production startup.

Fresh current authority gates NEW attempts. Owned receipts record what already
happened even if a newer desired generation commits during the provider request.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import HaPresenceDesired, HaPresenceEffect, HaPresenceTarget, Presence
from app.models.access_pipeline import HaPresenceCoordinator
from app.models.enums import PresenceState
from app.modules.home_assistant.input_boolean_contracts import (
    HaDelivery,
    home_assistant_connection_fingerprint,
)
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.ha_presence_contracts import (
    HaAttemptClaim,
    HaConfigurationUnavailable,
    HaCoordinationBusy,
    HaLeaseLost,
    PreparedHaAttempt,
)
from app.services.ha_presence_database import (
    HA_LEASE_SECONDS,
    HA_OPERATION_SECONDS,
    HA_SCAN_SIZE,
    database_now,
    ha_session,
    lock_current_people,
    lock_ha_coordinator,
)
from app.services.ha_presence_policy import current_binding, physical_target_key
from app.services.home_assistant_configuration import lock_home_assistant_connection
from app.services.maintenance_state import assert_hardware_enabled
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, write_audit_log


class HaPresenceAttemptStore:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self._session_factory = session_factory

    async def prepare_attempt(
        self, operation_deadline: float, *,
        on_committed: Callable[[PreparedHaAttempt], None] | None = None,
    ) -> PreparedHaAttempt | None:
        """Metadata page32, one actual free slot. Terminal pages do not exhaust backlog.

        Each candidate owns a separate short transaction, so no later Person/setting
        lock is acquired while retaining a preceding candidate's HA coordination.
        The worker's original operation deadline bounds active SQL across pages.
        """
        while asyncio.get_running_loop().time() < operation_deadline:
            async with ha_session(self._session_factory) as session:
                candidates = (await session.execute(select(
                    HaPresenceTarget.target_key, HaPresenceTarget.generation,
                    HaPresenceTarget.person_id,
                ).where(HaPresenceTarget.state == "ready",
                    HaPresenceTarget.barrier_attempt_id.is_(None))
                    .order_by(HaPresenceTarget.generation, HaPresenceTarget.target_key)
                    .limit(HA_SCAN_SIZE))).all()
            if not candidates:
                return None
            for key, generation, person_id in candidates:
                if asyncio.get_running_loop().time() >= operation_deadline:
                    return None
                try:
                    prepared, again = await self._prepare_candidate(
                        key, generation, person_id, operation_deadline,
                        on_committed=on_committed,
                    )
                except HaCoordinationBusy:
                    return None
                except DBAPIError as exc:
                    if getattr(exc.orig, "sqlstate", None) == "55P03":
                        return None
                    raise
                if prepared is not None:
                    return prepared
                if not again:
                    return None
        return None

    async def _prepare_candidate(
        self, key: str, generation: int, person_id: uuid.UUID | None,
        operation_deadline: float, *,
        on_committed: Callable[[PreparedHaAttempt], None] | None = None,
    ) -> tuple[PreparedHaAttempt | None, bool]:
        prepared: PreparedHaAttempt | None = None
        async with ha_session(self._session_factory) as session:
            await reload_auth_root_for_transaction(session)
            people = await lock_current_people(session, [person_id] if person_id else [])
            person = people.get(person_id) if person_id else None
            reason: str | None = None
            try:
                connection = await lock_home_assistant_connection(session)
            except HaConfigurationUnavailable:
                connection, reason = None, "configuration_unavailable"
            try:
                await assert_hardware_enabled(session)
            except ValueError:
                reason = "maintenance_active"
            coordinator = await lock_ha_coordinator(session)
            target = await session.get(HaPresenceTarget, key, with_for_update=True,
                populate_existing=True)
            if target is None or target.state != "ready" or target.generation != generation:
                return None, True
            if target.barrier_attempt_id is not None:
                return None, True
            desired = await session.get(HaPresenceDesired, person_id,
                with_for_update=True, populate_existing=True) if person_id is not None else None
            # Scalar Presence proof is valid only after all applied/reset/delete
            # writers adopt last-flush proposals at the reviewed activation gate.
            presence = (await session.execute(select(Presence.state, Presence.last_event_id)
                .where(Presence.person_id == person_id))).one_or_none()
            sampled = asyncio.get_running_loop().time()  # BEFORE awaited DB response.
            now = await database_now(session)
            if coordinator.lease_token is not None and coordinator.lease_until is not None and coordinator.lease_until > now:
                return None, False  # Actual global capacity is occupied, not leadership.
            binding = None
            if person is None or not person.is_active:
                reason = reason or "person_unavailable"
            elif presence is None or presence.state not in {PresenceState.PRESENT, PresenceState.EXITED}:
                reason = reason or "presence_unavailable"
            else:
                try:
                    binding = current_binding(person, presence.state)
                except (ValueError, TypeError, AttributeError):
                    reason = reason or "binding_unavailable"
            if (
                desired is None or not desired.eligible or desired.generation != generation
                or desired.event_id != target.event_id
                or desired.binding_fingerprint != target.binding_fingerprint
                or desired.config_fingerprint != target.config_fingerprint
                or desired.presence_event_id != (presence.last_event_id if presence else None)
                or desired.presence_state != (presence.state.value if presence else None)
            ):
                reason = reason or "desired_superseded"
            if (
                target.origin_at is None or target.origin_at.tzinfo is None
                or target.deadline is None or target.deadline.tzinfo is None
                or target.origin_at > now or target.deadline <= now
                or target.deadline != target.origin_at + timedelta(seconds=300)
                or desired is None or desired.origin_at != target.origin_at
                or desired.deadline != target.deadline
            ):
                reason = reason or "origin_unavailable_or_expired"
            if binding is None or target.entity_id not in binding.entity_ids or target.action != binding.action or target.binding_fingerprint != binding.fingerprint:
                reason = reason or "binding_changed"
            if connection is None or not (connection.home_assistant_url and connection.home_assistant_token):
                reason = reason or "configuration_unavailable"
            elif (
                target.url != connection.home_assistant_url.rstrip("/")
                or physical_target_key(connection, target.entity_id) != target.target_key
                or target.config_fingerprint != home_assistant_connection_fingerprint(connection)
            ):
                reason = reason or "configuration_changed"
            existing = await session.scalar(select(HaPresenceEffect.id).where(
                HaPresenceEffect.target_key == key, HaPresenceEffect.generation == generation,
            ))
            if existing is not None:
                reason = reason or "generation_already_attempted"
            if reason is not None:
                target.state, target.updated_at = "held", now
                if desired is not None and desired.generation == generation:
                    desired.eligible, desired.outcome = False, reason
                await session.flush()
                await session.commit()
                return None, True
            assert connection is not None and binding is not None
            assert target.origin_at is not None and target.deadline is not None
            if sampled >= operation_deadline:
                return None, False
            token, attempt_id = uuid.uuid4(), uuid.uuid4()
            coordinator.epoch += 1
            coordinator.lease_token, coordinator.lease_until = token, now + timedelta(seconds=HA_LEASE_SECONDS)
            target.barrier_version += 1
            target.barrier_attempt_id, target.updated_at = attempt_id, now
            effect = HaPresenceEffect(
                id=attempt_id, target_key=key, generation=generation,
                person_id=target.person_id, event_id=target.event_id,
                url=target.url, entity_id=target.entity_id, action=binding.action,
                binding_fingerprint=target.binding_fingerprint,
                config_fingerprint=target.config_fingerprint, state="attempting",
                owner_token=token, owner_epoch=coordinator.epoch,
                lease_until=coordinator.lease_until, attempted_at=now,
            )
            session.add(effect)
            claim = HaAttemptClaim(attempt_id, key, generation, target.barrier_version,
                token, coordinator.epoch)
            await _audit_attempt(session, effect, "attempting")
            await session.flush()
            checked = await database_now(session)
            start_deadline = min(operation_deadline, sampled + HA_OPERATION_SECONDS,
                sampled + (target.deadline - now).total_seconds(),
                sampled + (effect.lease_until - now).total_seconds())
            if checked >= effect.lease_until or checked >= target.deadline or asyncio.get_running_loop().time() >= start_deadline:
                raise HaLeaseLost("ha_attempt_expired_before_commit")
            prepared = PreparedHaAttempt(
                claim, connection, target.entity_id, binding.action,
                target.origin_at, target.deadline,
                start_deadline,
            )
            await session.commit()
            if on_committed is not None:
                # Synchronous ownership notification only: confirmed commit, once,
                # before actual close. Failure still retains this durable attempt.
                on_committed(prepared)
        # The same absolute start deadline includes actual session close latency.
        return prepared, True

    async def renew_attempt(self, claim: HaAttemptClaim) -> bool:
        """Retain actual capacity through receipt/session/client cleanup.

        After a terminal receipt only the singleton capacity lease is extended;
        the immutable outcome never authorizes another provider call or receipt.
        """
        try:
            async with ha_session(self._session_factory) as session:
                coordinator = await lock_ha_coordinator(session)
                effect = await session.get(HaPresenceEffect, claim.attempt_id,
                    with_for_update=True, populate_existing=True)
                now = await database_now(session)
                _assert_capacity(coordinator, effect, claim, now)
                assert effect is not None and coordinator.lease_until is not None
                previous_expiry = coordinator.lease_until
                coordinator.lease_until = now + timedelta(seconds=HA_LEASE_SECONDS)
                if effect.state == "attempting":
                    if effect.lease_until <= now:
                        raise HaLeaseLost("ha_attempt_lease_lost")
                    previous_expiry = min(previous_expiry, effect.lease_until)
                    effect.lease_until = coordinator.lease_until
                # Terminal receipts are immutable: renewal now owns capacity only.
                await session.flush()
                if await database_now(session) >= previous_expiry:
                    raise HaLeaseLost("ha_renewal_expired_during_write")
                await session.commit()
                return True
        except HaLeaseLost:
            return False

    async def record_receipt(self, claim: HaAttemptClaim, delivery: HaDelivery) -> None:
        """Separate owned receipt phase after I/O timer exit; no optional expiry test.

        A newer target generation cannot discard old known POST truth. An exact
        live attempt lease is still required; replacing its barrier is not allowed
        to let this old receipt clear the replacement.
        """
        async with ha_session(self._session_factory) as session:
            coordinator = await lock_ha_coordinator(session)
            target = await session.get(HaPresenceTarget, claim.target_key,
                with_for_update=True, populate_existing=True)
            effect = await session.get(HaPresenceEffect, claim.attempt_id,
                with_for_update=True, populate_existing=True)
            now = await database_now(session)
            _assert_owned(coordinator, effect, claim, now)
            assert effect is not None and coordinator.lease_until is not None
            previous_expiry = min(coordinator.lease_until, effect.lease_until)
            effect.state, effect.outcome = delivery.effect_state, delivery.kind
            effect.detail, effect.completed_at = delivery.reason, now
            if target is not None and target.barrier_attempt_id == claim.attempt_id and target.barrier_version == claim.barrier_version:
                if delivery.kind != "unknown":
                    target.barrier_attempt_id = None
                    if target.generation == claim.generation:
                        target.state = "succeeded" if delivery.kind == "accepted" else "failed"
                target.updated_at = now
            # Keep exact capacity until the worker settles actual operation,
            # receipt-session and dedicated client cleanup, then release separately.
            await _audit_attempt(session, effect, delivery.kind, reason=delivery.reason,
                observed_state=delivery.state)
            await session.flush()
            if await database_now(session) >= previous_expiry:
                raise HaLeaseLost("ha_receipt_expired_during_write")
            await session.commit()

    async def release_capacity(self, claim: HaAttemptClaim) -> bool:
        """Call only AFTER actual operation/session/client cleanup has settled.

        This final short transaction clears only the exact terminal owner's token.
        Its own DB close is retained, though commit necessarily makes capacity
        available before that final connection cleanup returns. No provider work
        or preceding receipt cleanup may remain at this boundary.
        """
        async with ha_session(self._session_factory) as session:
            coordinator = await lock_ha_coordinator(session)
            effect = await session.get(HaPresenceEffect, claim.attempt_id,
                with_for_update=True, populate_existing=True)
            if not _matches_capacity_identity(coordinator, effect, claim):
                return False
            assert effect is not None
            if effect.state == "attempting":
                return False  # Unsettled certainty stays recoverable/UNKNOWN-fenced.
            coordinator.lease_token, coordinator.lease_until = None, None
            await session.flush()
            await session.commit()
            return True

    async def recover_expired_attempts(self) -> int:
        """Bounded attempted→UNKNOWN accounting; never replays an old generation."""
        async with ha_session(self._session_factory) as session:
            coordinator = await lock_ha_coordinator(session)
            now = await database_now(session)
            rows = (await session.scalars(select(HaPresenceEffect).where(
                HaPresenceEffect.state == "attempting", HaPresenceEffect.lease_until <= now,
            ).order_by(HaPresenceEffect.lease_until, HaPresenceEffect.id)
                .limit(HA_SCAN_SIZE).with_for_update())).all()
            # Locks may block. Resample instead of using transaction-start now().
            now = await database_now(session)
            for effect in rows:
                if effect.lease_until > now:
                    continue
                effect.state, effect.outcome = "unknown", "worker_lost"
                effect.detail, effect.completed_at = "worker_lost", now
                await _audit_attempt(session, effect, "unknown", reason="worker_lost")
            if coordinator.lease_until is not None and coordinator.lease_until <= now:
                coordinator.lease_token, coordinator.lease_until = None, None
            await session.flush()
            await session.commit()
            return len(rows)


def _matches_capacity_identity(
    coordinator: HaPresenceCoordinator, effect: HaPresenceEffect | None,
    claim: HaAttemptClaim,
) -> bool:
    return bool(
        coordinator.epoch == claim.epoch and coordinator.lease_token == claim.token
        and effect is not None and effect.owner_token == claim.token
        and effect.owner_epoch == claim.epoch and effect.target_key == claim.target_key
        and effect.generation == claim.generation
    )


def _assert_capacity(
    coordinator: HaPresenceCoordinator, effect: HaPresenceEffect | None,
    claim: HaAttemptClaim, now: datetime,
) -> None:
    if (
        not _matches_capacity_identity(coordinator, effect, claim)
        or coordinator.lease_until is None or coordinator.lease_until <= now
    ):
        raise HaLeaseLost("ha_attempt_capacity_lost")


def _assert_owned(
    coordinator: HaPresenceCoordinator, effect: HaPresenceEffect | None,
    claim: HaAttemptClaim, now: datetime,
) -> None:
    _assert_capacity(coordinator, effect, claim, now)
    if effect is None or effect.state != "attempting" or effect.lease_until <= now:
        raise HaLeaseLost("ha_attempt_lease_lost")


async def _audit_attempt(
    session: AsyncSession, effect: HaPresenceEffect, outcome: str, *,
    reason: str | None = None, observed_state: str | None = None,
) -> None:
    await write_audit_log(
        session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="ha_presence.attempting" if outcome == "attempting" else f"person_presence_input_boolean.{effect.action}",
        actor="Access Event Automation", target_entity="HomeAssistantInputBoolean",
        target_id=effect.target_key, target_label="Home Assistant presence target",
        outcome=outcome, level="info" if outcome in {"attempting", "accepted"} else "warning",
        metadata={"attempt_id": str(effect.id), "generation": effect.generation,
            "person_id": str(effect.person_id) if effect.person_id else None,
            "access_event_id": str(effect.event_id) if effect.event_id else None,
            "physical_target_fingerprint": effect.target_key,
            "binding_fingerprint": effect.binding_fingerprint,
            "config_fingerprint": effect.config_fingerprint,
            "delivery": outcome, "reason": reason, "state": observed_state},
    )
