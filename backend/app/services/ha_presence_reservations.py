"""Last-flush desired-state participant. It never commits or performs provider I/O.

All current Presence writers must participate before activation. Earlier domain
locks remain with the caller; any Busy rolls back the complete owning transaction.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HaPresenceDesired, HaPresenceTarget, Person, Presence
from app.modules.home_assistant.input_boolean_contracts import home_assistant_connection_fingerprint
from app.services.auth_root import try_reload_auth_root_for_transaction
from app.services.ha_presence_contracts import (
    HaConfigurationUnavailable,
    HaCoordinationBusy,
    HaInvalidationReason,
    HaPresenceReservation,
    HaReservationSummary,
)
from app.services.ha_presence_database import database_now, lock_current_people, lock_ha_coordinator
from app.services.ha_presence_policy import current_binding, physical_target_key
from app.services.home_assistant_configuration import lock_home_assistant_connection

OPTIONAL_SECONDS = 300
INVALID_FINGERPRINT = hashlib.sha256(b"invalid_ha_configuration_or_binding").hexdigest()


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None


async def reserve_all_in_session(
    session: AsyncSession, proposals: Sequence[HaPresenceReservation],
) -> HaReservationSummary:
    """Flush once LAST after all domain/audit/session writes; no later domain locks.

    A stale proposal cannot manufacture a generation. Replaying the same event
    cannot revive a prior invalidation or renew its immutable original deadline.
    """
    if not proposals:
        return HaReservationSummary()
    try:
        if await try_reload_auth_root_for_transaction(session) is None:
            raise HaCoordinationBusy("ha_auth_root_busy")
        person_ids = sorted({proposal.person_id for proposal in proposals})
        people_by_id = await lock_current_people(session, person_ids)
        try:
            connection = await lock_home_assistant_connection(session)
        except HaConfigurationUnavailable:
            connection = None
        coordinator = await lock_ha_coordinator(session)
        applied = invalidated = skipped = 0
        for proposal in proposals:
            presence = await session.get(Presence, proposal.person_id, populate_existing=True)
            if (
                presence is None
                or presence.last_event_id != proposal.presence_event_id
                or proposal.event_id != proposal.presence_event_id
                or not isinstance(proposal.presence_state, str)
                or proposal.presence_state not in {"present", "exited"}
                or presence.state.value != proposal.presence_state
            ):
                skipped += 1
                continue
            desired = await session.get(HaPresenceDesired, proposal.person_id,
                with_for_update=True, populate_existing=True)
            if desired is not None and desired.event_id == proposal.event_id:
                skipped += 1
                continue
            person = people_by_id.get(proposal.person_id)
            now = await database_now(session)
            binding = None
            if person is not None:
                try:
                    binding = current_binding(person, presence.state)
                except (ValueError, TypeError, AttributeError):
                    pass  # Malformed optional bindings do not undo core presence.
            reason = "historical_invalidation"
            eligible = False
            if proposal.live_eligible:
                if not _aware(proposal.origin_at):
                    reason = "origin_unavailable"
                elif proposal.origin_at is not None and proposal.origin_at > now:
                    reason = "origin_in_future"
                elif proposal.origin_at is not None and proposal.origin_at + timedelta(seconds=OPTIONAL_SECONDS) <= now:
                    reason = "origin_expired"
                elif person is None or not person.is_active:
                    reason = "person_unavailable"
                elif binding is None or not binding.entity_ids:
                    reason = "binding_unavailable"
                elif connection is None or not (
                    connection.home_assistant_url and connection.home_assistant_token
                ):
                    reason = "configuration_unavailable"
                else:
                    reason, eligible = "queued", True
            coordinator.next_generation += 1
            generation = coordinator.next_generation
            binding_fingerprint = binding.fingerprint if binding else INVALID_FINGERPRINT
            config_fingerprint = home_assistant_connection_fingerprint(connection) if connection else INVALID_FINGERPRINT
            if desired is None:
                desired = HaPresenceDesired(person_id=proposal.person_id)
                session.add(desired)
            desired.generation = generation
            desired.event_id = proposal.event_id
            desired.presence_event_id = proposal.presence_event_id
            desired.presence_state = proposal.presence_state
            desired.binding_fingerprint = binding_fingerprint
            desired.config_fingerprint = config_fingerprint
            desired.eligible = eligible
            desired.origin_at = proposal.origin_at
            desired.deadline = (
                proposal.origin_at + timedelta(seconds=OPTIONAL_SECONDS)
                if _aware(proposal.origin_at) and proposal.origin_at is not None else None
            )
            desired.outcome = reason
            # Ownership predicate protects B's newer shared-target desired state
            # while A is retired/rebound. Attempt/barrier fields are never changed.
            await session.execute(update(HaPresenceTarget).where(
                HaPresenceTarget.person_id == proposal.person_id,
            ).values(generation=generation, state="invalidated", updated_at=now))
            if eligible and binding is not None and connection is not None:
                for entity_id in binding.entity_ids:
                    key = physical_target_key(connection, entity_id)
                    target = await session.get(HaPresenceTarget, key,
                        with_for_update=True, populate_existing=True)
                    canonical_url = connection.home_assistant_url.rstrip("/")
                    if target is not None and (
                        target.url != canonical_url or target.entity_id != entity_id
                    ):
                        # Digest narrows identity; equality is mandatory. Never link
                        # a colliding full target or truncate existing accepted IDs.
                        desired.eligible = False
                        desired.outcome = "physical_identity_collision"
                        break
                    if target is None:
                        target = HaPresenceTarget(
                            target_key=key, url=canonical_url, entity_id=entity_id,
                            barrier_version=0, barrier_attempt_id=None,
                        )
                        session.add(target)
                    target.generation = generation
                    target.person_id, target.event_id = proposal.person_id, proposal.event_id
                    target.action = binding.action
                    target.binding_fingerprint, target.config_fingerprint = binding_fingerprint, config_fingerprint
                    target.origin_at, target.deadline = desired.origin_at, desired.deadline
                    target.state, target.updated_at = "ready", now
                if desired.eligible:
                    applied += 1
                else:
                    invalidated += 1
            else:
                invalidated += 1
        await session.flush()
        return HaReservationSummary(applied, invalidated, skipped)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise HaCoordinationBusy("ha_current_authority_busy") from exc
        raise


async def invalidate_people_in_session(
    session: AsyncSession, person_ids: Sequence[uuid.UUID], *, reason: HaInvalidationReason,
) -> int:
    """Last-flush no-send invalidation for edits/reset/scoped retirement.

    Missing Person is allowed. Call after its deletion/mutations, never acquire
    new domain locks after this method. Immutable attempted records are retained.
    """
    if not isinstance(reason, str) or reason not in {
        "person_changed", "simulation_reset", "synthetic_cleanup", "binding_retired",
    }:
        raise ValueError("HA invalidation requires a safe reason code")
    if not person_ids:
        return 0
    try:
        if await try_reload_auth_root_for_transaction(session) is None:
            raise HaCoordinationBusy("ha_auth_root_busy")
        selected = sorted(set(person_ids))
        await session.execute(select(Person.id).where(Person.id.in_(selected))
            .order_by(Person.id).with_for_update(nowait=True))
        try:
            await lock_home_assistant_connection(session)
        except HaConfigurationUnavailable:
            pass
        coordinator = await lock_ha_coordinator(session)
        now = await database_now(session)
        coordinator.next_generation += 1
        generation = coordinator.next_generation
        await session.execute(update(HaPresenceDesired).where(
            HaPresenceDesired.person_id.in_(selected),
        ).values(generation=generation, eligible=False, outcome=reason))
        await session.execute(update(HaPresenceTarget).where(
            HaPresenceTarget.person_id.in_(selected),
        ).values(generation=generation, state="invalidated", updated_at=now))
        await session.flush()
        return generation
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise HaCoordinationBusy("ha_current_authority_busy") from exc
        raise


async def invalidate_configuration_in_session(session: AsyncSession) -> int:
    """No Person locks: settings owner already completed all ordinary mutations."""
    try:
        if await try_reload_auth_root_for_transaction(session) is None:
            raise HaCoordinationBusy("ha_auth_root_busy")
        # Current setting rows are already caller-owned by the writer. The same port
        # also covers a missing setting's advisory authority without a new lock graph.
        try:
            await lock_home_assistant_connection(session)
        except HaConfigurationUnavailable:
            pass
        coordinator = await lock_ha_coordinator(session)
        now = await database_now(session)
        coordinator.next_generation += 1
        coordinator.config_revision += 1
        generation = coordinator.next_generation
        await session.execute(update(HaPresenceDesired).values(
            generation=generation, eligible=False, outcome="configuration_changed",
        ))
        await session.execute(update(HaPresenceTarget).values(
            generation=generation, state="invalidated", updated_at=now,
        ))
        await session.flush()
        return generation
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise HaCoordinationBusy("ha_current_authority_busy") from exc
        raise
