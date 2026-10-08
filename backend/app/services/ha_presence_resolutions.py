"""Dormant exact-attempt preview and audited acknowledgement; never sends to HA.

Activation requires the coherent auth-root writer boundary. Read-only provider
observation cannot establish settlement: the confirmed operator assertion is the
manual evidence decision. An original UNKNOWN attempt is retained unchanged.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import HaPresenceEffect, HaPresenceResolution, HaPresenceTarget, User
from app.models.enums import PresenceState, UserRole
from app.modules.home_assistant.input_boolean_contracts import (
    FrozenHaConnection,
    home_assistant_connection_fingerprint,
)
from app.services.action_confirmations import (
    ActionConfirmationError,
    ConfirmationRejection,
    confirmation_payload_hash,
    consume_action_confirmation,
    persist_confirmation_rejection,
)
from app.services.auth import authenticated_token
from app.services.auth_root import reload_auth_root_for_transaction
from app.services.ha_presence_contracts import HaConfigurationUnavailable, HaCurrentPerson
from app.services.ha_presence_database import (
    HA_OPERATION_SECONDS,
    HA_RECEIPT_SECONDS,
    database_now,
    ha_session,
    lock_current_people,
    lock_ha_coordinator,
)
from app.services.ha_presence_policy import current_binding, physical_target_key
from app.services.ha_presence_resolution_contracts import (
    HA_ACKNOWLEDGEMENT_ACTION,
    HaAcknowledgement,
    HaAdminRequest,
    HaAttemptPreview,
    HaResolutionError,
    HaResolutionReceipt,
    acknowledgement_payload,
)
from app.services.home_assistant_configuration import lock_home_assistant_connection
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, actor_from_user, write_audit_log


class HaPresenceResolutionService:
    def __init__(
        self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal, *,
        owns_local_capacity: Callable[[], bool],
    ) -> None:
        self._session_factory = session_factory
        self._owns_local_capacity = owns_local_capacity

    async def preview_attempt(self, attempt_id: uuid.UUID, authority: HaAdminRequest) -> HaAttemptPreview:
        async with ha_session(self._session_factory) as session:
            _, expires_at = await _authorize(session, authority)
            preview, _, _ = await self._locked_preview(session, attempt_id)
            _require_unexpired(expires_at, await database_now(session))
            return preview

    async def acknowledge_attempt(
        self, request: HaAcknowledgement, authority: HaAdminRequest,
    ) -> HaResolutionReceipt:
        actual = asyncio.create_task(self._acknowledge_owned(request, authority), name="ha-acknowledgement")
        try:
            async with asyncio.timeout(HA_OPERATION_SECONDS):
                return await asyncio.shield(actual)
        except (TimeoutError, asyncio.CancelledError):
            # The caller's timer has exited. Cancel active SQL once; the actual
            # owner retains its complete rollback/close/audit suffix thereafter.
            if not actual.done() and not actual.cancelling():
                actual.cancel()
            try:
                await drain_owned(asyncio.gather(actual, return_exceptions=True))
            except asyncio.CancelledError:
                pass  # Actual cleanup has settled despite another caller cancel.
            return actual.result()  # Preserve a known commit or mandatory audit error.

    async def _acknowledge_owned(
        self, request: HaAcknowledgement, authority: HaAdminRequest,
    ) -> HaResolutionReceipt:
        session = self._session_factory()
        connection: AsyncConnection | None = None
        receipt: HaResolutionReceipt | None = None
        primary: BaseException | None = None
        try:
            # Observe the original connection, never reacquire one after a failure
            # and mistake a local ORM reset for rollback of the owning transaction.
            connection = await session.connection()
            receipt = await self._acknowledge_in_session(session, request, authority)
        except BaseException as exc:  # noqa: BLE001 - Preserve rejection/commit evidence through every cleanup outcome.
            primary = exc
        rejection = primary.rejection if isinstance(primary, ActionConfirmationError) else None
        cleanup = asyncio.create_task(
            _finish_acknowledgement(session, connection, receipt, rejection),
            name="ha-acknowledgement-cleanup",
        )
        cancelled = False
        try:
            await drain_owned(cleanup)
        except asyncio.CancelledError:
            cancelled = True  # The complete cleanup/audit child already settled.
        if receipt is not None:
            return receipt
        if primary is not None:
            raise primary
        if cancelled:
            raise asyncio.CancelledError
        raise RuntimeError("ha_acknowledgement_missing_outcome")

    async def _acknowledge_in_session(
        self, session: AsyncSession, request: HaAcknowledgement, authority: HaAdminRequest,
    ) -> HaResolutionReceipt:
        actor, expires_at = await _authorize(session, authority)
        request_fingerprint = _request_fingerprint(request)
        # Receipt retrieval precedes every new-resolution eligibility check.
        existing = await session.get(HaPresenceResolution, request.attempt_id)
        if existing is not None:
            _require_unexpired(expires_at, await database_now(session))
            return _matching_receipt(existing, authority, request, request_fingerprint)
        preview, effect, target = await self._locked_preview(session, request.attempt_id)
        # A concurrent committed resolution may have won before our graph lock.
        existing = await session.get(HaPresenceResolution, request.attempt_id, populate_existing=True)
        if existing is not None:
            _require_unexpired(expires_at, await database_now(session))
            return _matching_receipt(existing, authority, request, request_fingerprint)
        if preview['preview_fingerprint'] != request.expected_preview_fingerprint:
            raise HaResolutionError('The attempt or current target changed. Create a fresh confirmation.')
        if preview['live_owner']:
            raise HaResolutionError('An HA attempt or its cleanup still owns capacity.')
        if effect.state != 'unknown' or target.barrier_attempt_id != effect.id:
            raise HaResolutionError('Only the retained UNKNOWN target barrier can be acknowledged.')
        confirmation = await consume_action_confirmation(
            session, user=actor, action=HA_ACKNOWLEDGEMENT_ACTION,
            payload=acknowledgement_payload(request), confirmation_token=request.confirmation_token,
            commit=False,
        )
        if confirmation.id != request.confirmation_id:
            raise HaResolutionError('The explicit confirmation identity does not match this request.')
        now = await database_now(session)
        resolution = HaPresenceResolution(
            attempt_id=effect.id, confirmation_id=confirmation.id,
            target_key=target.target_key, target_generation=target.generation,
            barrier_version=target.barrier_version, request_fingerprint=request_fingerprint,
            actor_id=actor.id, actor_auth_version=actor.auth_session_version,
            reason=request.reason.strip(), resolved_at=now,
        )
        session.add(resolution)
        # Release only this exact uncertainty. The original outcome is immutable;
        # a fresh desired generation must independently pass worker authority.
        target.barrier_attempt_id = None
        target.barrier_version += 1
        await write_audit_log(
            session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action='home_assistant.presence_effect.acknowledged',
            actor=actor_from_user(actor), actor_user_id=actor.id,
            target_entity='ha_presence_effect', target_id=str(effect.id),
            target_label='Home Assistant presence target',
            metadata={'attempt_id':str(effect.id),'confirmation_id':str(confirmation.id),
                      'target_fingerprint':target.target_key,'target_generation':target.generation,
                      'barrier_version':resolution.barrier_version,
                      'previous_request_settled':True,'reason':resolution.reason},
        )
        await session.flush()
        # clock_timestamp is sampled after every blocking confirmation/audit write.
        _require_unexpired(expires_at, await database_now(session))
        receipt = _receipt(resolution)
        await session.commit()
        return receipt

    async def _locked_preview(
        self, session: AsyncSession, attempt_id: uuid.UUID,
    ) -> tuple[HaAttemptPreview, HaPresenceEffect, HaPresenceTarget]:
        identity = (await session.execute(select(
            HaPresenceEffect.target_key, HaPresenceEffect.person_id,
        ).where(HaPresenceEffect.id == attempt_id))).one_or_none()
        if identity is None:
            raise HaResolutionError('HA attempt not found.', status_code=404)
        people = await lock_current_people(session, [identity.person_id] if identity.person_id else [])
        person = people.get(identity.person_id) if identity.person_id else None
        try:
            connection = await lock_home_assistant_connection(session)
            config_fingerprint = (home_assistant_connection_fingerprint(connection)
                if connection.home_assistant_url and connection.home_assistant_token else None)
        except HaConfigurationUnavailable:
            config_fingerprint = None
        coordinator = await lock_ha_coordinator(session)
        target = await session.scalar(select(HaPresenceTarget).where(
            HaPresenceTarget.target_key == identity.target_key,
        ).with_for_update().execution_options(populate_existing=True))
        effect = await session.scalar(select(HaPresenceEffect).where(
            HaPresenceEffect.id == attempt_id,
        ).with_for_update().execution_options(populate_existing=True))
        if target is None or effect is None:
            raise HaResolutionError('HA attempt not found.', status_code=404)
        if effect.target_key != identity.target_key or effect.person_id != identity.person_id:
            raise HaResolutionError('The immutable attempt identity is inconsistent.')
        physical_fingerprint = physical_target_key(FrozenHaConnection(effect.url, ''), effect.entity_id)
        if (physical_fingerprint != target.target_key
            or effect.url != target.url or effect.entity_id != target.entity_id):
            raise HaResolutionError('The physical target identity is inconsistent.')
        existing = await session.get(HaPresenceResolution, attempt_id, populate_existing=True)
        now = await database_now(session)
        preview: HaAttemptPreview = {
            'attempt_id':str(effect.id),'attempt_generation':effect.generation,
            'attempt_state':effect.state,'action':effect.action,
            'entity_label':'Home Assistant presence target',
            'attempted_at':effect.attempted_at.isoformat(),
            'completed_at':effect.completed_at.isoformat() if effect.completed_at else None,
            'physical_target_fingerprint':physical_fingerprint,
            'attempt_binding_fingerprint':effect.binding_fingerprint,
            'attempt_config_fingerprint':effect.config_fingerprint,
            'target_generation':target.generation,'target_state':target.state,
            'barrier_version':target.barrier_version,
            'barrier_attempt_id':str(target.barrier_attempt_id) if target.barrier_attempt_id else None,
            'current_person_id':str(effect.person_id) if effect.person_id else None,
            'current_person_exists':person is not None,
            'current_person_active':person.is_active if person is not None else None,
            'current_binding_fingerprint':_binding_fingerprint(person),
            'current_config_fingerprint':config_fingerprint,
            'current_config_available':config_fingerprint is not None,
            'live_owner':self._owns_local_capacity() or (coordinator.lease_token is not None and coordinator.lease_until is not None and coordinator.lease_until > now),
            'resolution':_receipt(existing) if existing is not None else None,
            'preview_fingerprint':'',
        }
        # The projection omits sampled clock and lease expiry: unchanged capacity
        # renewals must not make every otherwise identical preview stale.
        preview['preview_fingerprint'] = confirmation_payload_hash(dict(preview))
        return preview, effect, target


async def _authorize(session: AsyncSession, authority: HaAdminRequest) -> tuple[User, datetime]:
    await reload_auth_root_for_transaction(session)
    await session.scalar(select(User.id).where(User.id == authority.actor_user_id).with_for_update(nowait=True))
    verified = await authenticated_token(session, authority.token)
    if (verified is None or verified.user.id != authority.actor_user_id
        or verified.user.role != UserRole.ADMIN):
        raise HaResolutionError('Your Admin session is no longer valid.', status_code=403)
    try:
        expires_at = datetime.fromtimestamp(verified.expires_at, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise HaResolutionError('Your session expiry cannot authorize this request.', status_code=403) from exc
    _require_unexpired(expires_at, await database_now(session))
    return verified.user, expires_at


def _require_unexpired(expires_at: datetime, now: datetime) -> None:
    if now >= expires_at:
        raise HaResolutionError('Your Admin session expired before this request completed.', status_code=403)


def _binding_fingerprint(person: HaCurrentPerson | None) -> str | None:
    if person is None:
        return None
    try:
        return current_binding(person, PresenceState.PRESENT).fingerprint
    except (ValueError, TypeError, AttributeError):
        return None


def _request_fingerprint(request: HaAcknowledgement) -> str:
    return confirmation_payload_hash({**acknowledgement_payload(request), 'confirmation_id':str(request.confirmation_id)})


def _matching_receipt(
    row: HaPresenceResolution, authority: HaAdminRequest, request: HaAcknowledgement, fingerprint: str,
) -> HaResolutionReceipt:
    if (row.actor_id != authority.actor_user_id or row.confirmation_id != request.confirmation_id
        or row.request_fingerprint != fingerprint):
        raise HaResolutionError('This attempt already has a different acknowledgement.')
    return _receipt(row)


def _receipt(row: HaPresenceResolution) -> HaResolutionReceipt:
    return {
        'attempt_id':str(row.attempt_id),'confirmation_id':str(row.confirmation_id),
        'target_fingerprint':row.target_key,'target_generation':row.target_generation,
        'barrier_version':row.barrier_version,'actor_id':str(row.actor_id),
        'reason':row.reason,'resolved_at':row.resolved_at.isoformat(),
    }


logger = get_logger(__name__)


class HaUnresolvedCleanupError(RuntimeError):
    """Retained scalar outcome when original release cannot be positively proved."""

    def __init__(
        self, *, rejection: ConfirmationRejection | None,
        committed_receipt: HaResolutionReceipt | None,
    ) -> None:
        super().__init__("HA acknowledgement cleanup could not confirm original resource release.")
        self.rejection = rejection
        self.committed_receipt = committed_receipt


async def _bounded_cleanup[T](awaitable: Awaitable[T]) -> T:
    actual = asyncio.ensure_future(awaitable)
    try:
        async with asyncio.timeout(HA_RECEIPT_SECONDS):
            return await asyncio.shield(actual)
    finally:
        if not actual.done() and not actual.cancelling():
            actual.cancel()
        await drain_owned(asyncio.gather(actual, return_exceptions=True))


async def _finish_acknowledgement(
    session: AsyncSession, connection: AsyncConnection | None,
    receipt: HaResolutionReceipt | None, rejection: ConfirmationRejection | None,
) -> None:
    release_proved = receipt is not None
    rollback_error: BaseException | None = None
    close_error: BaseException | None = None
    if receipt is None:
        try:
            # Direct rollback of the observed live original transaction provides
            # the proof BEFORE ORM reset/close can discard its connection identity.
            original = connection.get_transaction() if connection is not None else None
            if (connection is None or connection.closed or connection.invalidated
                or original is None or not original.is_active):
                release_proved = False
            else:
                await _bounded_cleanup(original.rollback())
                release_proved = not connection.invalidated and not original.is_active
            await _bounded_cleanup(session.rollback())
        except BaseException as exc:  # noqa: BLE001 - Unproved rollback must retain frozen rejection evidence.
            rollback_error = exc
            release_proved = False
    try:
        await _bounded_cleanup(session.close())
    except BaseException as exc:  # noqa: BLE001 - Actual close failure still requires owned fallback cleanup.
        close_error = exc
        release_proved = False
        try:
            # Bounded owned fallback observes actual completion. Invalidation is
            # cleanup, NEVER proof that an uncertain original rollback succeeded.
            await _bounded_cleanup(session.invalidate())
            await _bounded_cleanup(session.close())
        except BaseException as fallback:
            raise HaUnresolvedCleanupError(
                rejection=rejection, committed_receipt=receipt,
            ) from fallback
    if receipt is not None:
        if close_error is not None:
            logger.error("ha_acknowledgement_committed_cleanup_failed", extra={
                "attempt_id":receipt["attempt_id"], "exception_class":type(close_error).__name__,
            })
        return  # A late close fault cannot discard or rewrite the known receipt.
    if rejection is not None:
        if not release_proved:
            raise HaUnresolvedCleanupError(rejection=rejection, committed_receipt=None) from (rollback_error or close_error)
        # Audit failure remains the observable failure; never replace it with 403.
        await persist_confirmation_rejection(rejection)
    elif rollback_error is not None or close_error is not None:
        logger.error("ha_acknowledgement_rollback_cleanup_failed", extra={
            "exception_class":type(rollback_error or close_error).__name__,
        })
