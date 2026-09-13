"""Per-target command receipts, fencing and evidence; participant of AccessDeviceService.

No provider I/O lives here. The committed ``attempting`` transition is the last
step before transmission. A missing/late response can never make a target free.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import AccessDeviceCommandRecord, GateCommandRecord, GateStateObservation
from app.models.enums import GateCommandState
from app.modules.access_devices.base import gate_receipt_projection
from app.modules.gate.base import CommandDelivery, GateState

UNRESOLVED_DEVICE_COMMAND_STATES = ("prepared", "attempting", "accepted", "unknown")
DEVICE_COMMAND_LEASE_SECONDS = 120
DEVICE_COMMAND_EVIDENCE_WINDOW_SECONDS = 120
MAX_PROVIDER_RECEIPTS = 8


@dataclass(frozen=True)
class DeviceCommandClaim:
    record: AccessDeviceCommandRecord
    token: str
    acquired: bool


def target_idempotency_key(operation_key: str, target_id: str, action: str) -> str:
    if not operation_key.strip():
        raise ValueError("A stable command idempotency key is required.")
    digest = hashlib.sha256(operation_key.encode()).hexdigest()
    return f"device-command:{action}:{target_id}:{digest}"


def automatic_garage_origin_context(
    value: dict[str, Any] | None, *, target_id: str, action: str,
    intent_id: str, operation_key: str, gate_command_id: str | None,
) -> dict[str, str] | None:
    """Validate the one trusted automatic-access provenance contract we retain."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"kind", "access_event_id", "target_label"}
            or value.get("kind") != "automatic_access_garage"):
        raise ValueError("Unsupported automatic garage origin context.")
    if action != "open" or gate_command_id is not None:
        raise ValueError("Automatic garage provenance belongs to a direct garage open only.")
    try:
        event_id = uuid.UUID(str(value["access_event_id"]))
        device_id = uuid.UUID(str(target_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Automatic garage origin identities must be UUIDs.") from exc
    label = value["target_label"]
    if not isinstance(label, str) or not label.strip() or len(label) > 160:
        raise ValueError("Automatic garage target label must contain 1 to 160 characters.")
    if (intent_id != str(uuid.uuid5(event_id, f"automatic-garage-open:{device_id}"))
            or operation_key != f"garage-command:open:{device_id}:event:{event_id}"):
        raise ValueError("Automatic garage origin differs from the stable command identity.")
    return {"kind": "automatic_access_garage", "access_event_id": str(event_id), "target_label": label}


def device_command_receipt(row: AccessDeviceCommandRecord) -> dict[str, Any]:
    delivery = (CommandDelivery.ACCEPTED if row.accepted is True
                else CommandDelivery.REJECTED if row.state == "rejected"
                else CommandDelivery.NOT_SENT if row.state == "not_sent" or row.accepted is False
                else CommandDelivery.UNKNOWN)
    return {
        "command_id": str(row.id), "target_device_id": str(row.target_device_id),
        "device_key": row.device_key, "action": row.action, "status": row.state,
        "accepted": bool(row.accepted), "delivery": delivery.value,
        "state": row.gate_state or "unknown", "verified": row.state == "verified",
        "requires_reconciliation": row.state in UNRESOLVED_DEVICE_COMMAND_STATES,
        "verification_observation_id": (str(row.verification_observation_id)
                                        if row.verification_observation_id else None),
        "verification_evidence": row.verification_evidence,
        "attempted_at": row.attempted_at.isoformat() if row.attempted_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "provider_receipts": list(row.provider_receipts or []), "detail": row.detail,
    }


class AccessDeviceCommandJournal:
    async def reconcile_parent_in_session(
        self, session: AsyncSession, parent: GateCommandRecord,
    ) -> dict[str, Any] | None:
        """Complete retained parent truth while the caller holds its row lock.

        Movement callers acquire saga/event/parent first; this owner then takes
        ordered target/child locks. An active parent still owns its fanout and is
        only read. Recovery never renews a lease or changes unchanged held rows.
        """
        now = await session.scalar(select(func.clock_timestamp()))
        if (parent.state == GateCommandState.LEASED and parent.lease_expires_at
                and parent.lease_expires_at > now):
            return await self.gate_command_projection(session, parent)
        projection = await self.gate_command_projection(session, parent, reconcile=True)
        if projection is None:
            return None
        values = {
            "command_metadata": {**(parent.command_metadata or {}), **projection},
            "accepted": projection["accepted"], "gate_state": projection["state"],
            "mechanically_confirmed": projection["mechanically_confirmed"],
            "requires_reconciliation": projection["requires_reconciliation"],
            "lease_token": None, "lease_expires_at": None,
            "state": (GateCommandState.RECONCILIATION_REQUIRED if projection["requires_reconciliation"]
                      else GateCommandState.RECONCILED if projection["mechanically_confirmed"]
                      else GateCommandState.REJECTED),
        }
        if parent.completed_at is None:
            # Timestamp the completed checkpoint after every blocking target lock.
            values["completed_at"] = await session.scalar(select(func.clock_timestamp()))
        for name, value in values.items():
            if getattr(parent, name) != value:
                setattr(parent, name, value)
        return projection

    async def gate_command_projection(self, session: AsyncSession, parent: GateCommandRecord, *,
                                      reconcile: bool = False) -> dict[str, Any] | None:
        metadata = parent.command_metadata or {}
        plan = metadata.get("target_plan")
        if metadata.get("recovery_version") != 2 or not plan:
            return None
        if reconcile:
            receipts = await self.parent_receipts(session, parent)
        else:
            rows = list((await session.scalars(select(AccessDeviceCommandRecord).where(
                AccessDeviceCommandRecord.gate_command_id == parent.id))).all())
            by_id = {str(row.target_device_id): row for row in rows}
            receipts = [device_command_receipt(by_id[target["target_device_id"]]) for target in plan["targets"]
                        if target["target_device_id"] in by_id]
        return gate_receipt_projection(receipts, admission_target_device_id=plan.get("admission_target_device_id"),
                                       expected_target_count=len(plan["targets"]))

    async def list_command_receipts(self, *, limit: int = 25,
                                    before_id: uuid.UUID | None = None) -> dict[str, Any]:
        from sqlalchemy import tuple_

        if not 1 <= limit <= 100:
            raise ValueError("Receipt page size must be between 1 and 100.")
        async with AsyncSessionLocal() as session:
            statement = select(AccessDeviceCommandRecord).where(AccessDeviceCommandRecord.gate_command_id.is_(None))
            if before_id is not None:
                cursor = await session.get(AccessDeviceCommandRecord, before_id)
                if cursor is None or cursor.gate_command_id is not None:
                    raise ValueError("The direct-command receipt cursor is no longer available.")
                statement = statement.where(tuple_(AccessDeviceCommandRecord.created_at, AccessDeviceCommandRecord.id)
                                            < tuple_(cursor.created_at, cursor.id))
            rows = list((await session.scalars(statement.order_by(
                AccessDeviceCommandRecord.created_at.desc(), AccessDeviceCommandRecord.id.desc()).limit(limit + 1))).all())
            return {"items": [device_command_receipt(row) for row in rows[:limit]],
                    "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None}

    @staticmethod
    async def lock_target(session: AsyncSession, target_id: uuid.UUID) -> None:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:target))"),
                              {"target": f"iacs:access-device:{target_id}"})

    @classmethod
    async def assert_configurable(cls, session: AsyncSession, target_id: uuid.UUID) -> None:
        await cls.lock_target(session, target_id)
        active = await session.scalar(select(AccessDeviceCommandRecord.id).where(
            AccessDeviceCommandRecord.target_device_id == target_id,
            AccessDeviceCommandRecord.state.in_(UNRESOLVED_DEVICE_COMMAND_STATES),
        ).limit(1))
        if active:
            raise ValueError("This access device has an unresolved command; reconcile it before changing its configuration.")

    @staticmethod
    async def assert_parent_active(session: AsyncSession, command_id: str, lease_token: str) -> None:
        row = await session.get(GateCommandRecord, uuid.UUID(command_id), with_for_update=True)
        now = await session.scalar(select(func.clock_timestamp()))
        if (not row or row.state != GateCommandState.LEASED or not lease_token
                or row.lease_token != lease_token or not row.lease_expires_at or row.lease_expires_at <= now):
            raise ValueError("Gate command lease is no longer active; no target may be sent.")

    async def replay(self, *, operation_key: str, target_id: str, action: str,
                     intent_id: str, binding_fingerprint: str,
                     origin_context: dict[str, Any] | None = None,
                     gate_command_id: str | None = None) -> AccessDeviceCommandRecord | None:
        origin = automatic_garage_origin_context(origin_context, target_id=target_id, action=action,
            intent_id=intent_id, operation_key=operation_key, gate_command_id=gate_command_id)
        async with AsyncSessionLocal() as session:
            row = await session.scalar(select(AccessDeviceCommandRecord).where(
                AccessDeviceCommandRecord.idempotency_key == target_idempotency_key(operation_key, target_id, action)
            ).with_for_update())
            if row:
                if (row.intent_id != intent_id or row.binding_fingerprint != binding_fingerprint
                        or row.origin_context != origin):
                    raise ValueError("An existing command identity cannot be rebound to different confirmed inputs.")
                self.expire(row, await session.scalar(select(func.clock_timestamp())))
                await session.commit()
            return row

    async def freeze_parent_plan(self, command_id: str, lease_token: str, plan: dict[str, Any]) -> None:
        async with AsyncSessionLocal() as session:
            row = await session.get(GateCommandRecord, uuid.UUID(command_id), with_for_update=True)
            now = await session.scalar(select(func.clock_timestamp()))
            if (not row or row.state != GateCommandState.LEASED or not lease_token
                    or row.lease_token != lease_token or not row.lease_expires_at or row.lease_expires_at <= now):
                raise ValueError("Gate command lease is no longer active; no target may be sent.")
            metadata = dict(row.command_metadata or {})
            frozen = metadata.get("target_plan")
            if frozen is not None and frozen != plan:
                raise ValueError("Gate command targets differ from the frozen command plan.")
            row.command_metadata = {**metadata, "target_plan": plan, "recovery_version": 2}
            await session.commit()

    async def freeze_entry_precondition(self, command_id: str, lease_token: str,
                                        precondition: dict[str, Any]) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            await self.assert_parent_active(session, command_id, lease_token)
            row = await session.get(GateCommandRecord, uuid.UUID(command_id))
            metadata = dict(row.command_metadata or {})
            frozen = metadata.get("automatic_entry_precondition")
            if frozen is None:
                frozen = precondition
                row.command_metadata = {**metadata, "automatic_entry_precondition": frozen}
            await session.commit()
            return frozen

    async def claim(
        self, session: AsyncSession, *, target: dict[str, Any], action: str,
        intent_id: str, operation_key: str, gate_command_id: str | None,
        expires_at: datetime | None, origin_context: dict[str, Any] | None = None,
    ) -> DeviceCommandClaim:
        """Caller validates current device/policy in this same locked transaction."""
        target_id = uuid.UUID(target["target_device_id"])
        origin = automatic_garage_origin_context(origin_context, target_id=str(target_id), action=action,
            intent_id=intent_id, operation_key=operation_key, gate_command_id=gate_command_id)
        if origin is not None and target.get("kind") != "garage_door":
            raise ValueError("Automatic garage provenance requires a garage-door target.")
        if gate_command_id is not None:
            # The child FK can acquire a parent key-share lock on INSERT. Take
            # the parent first, matching dispatch and receipt finalization order.
            parent = await session.get(GateCommandRecord, uuid.UUID(gate_command_id),
                                       with_for_update=True, populate_existing=True)
            if parent is None:
                raise ValueError("The parent gate command no longer exists.")
        if gate_command_id is None:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:operation))"),
                                  {"operation": f"iacs:direct-device-operation:{intent_id}"})
            prior = await session.scalar(select(AccessDeviceCommandRecord).where(
                AccessDeviceCommandRecord.intent_id == intent_id,
                AccessDeviceCommandRecord.gate_command_id.is_(None)).limit(1))
            if prior and (prior.target_device_id != target_id or prior.action != action
                          or prior.binding_fingerprint != target["binding_fingerprint"]):
                raise ValueError("A direct-device intent cannot be rebound to another physical target or action.")
        await self.lock_target(session, target_id)
        key = target_idempotency_key(operation_key, str(target_id), action)
        row = await session.scalar(select(AccessDeviceCommandRecord).where(
            AccessDeviceCommandRecord.idempotency_key == key).with_for_update())
        now = await session.scalar(select(func.clock_timestamp()))
        if row:
            if (row.binding_fingerprint != target["binding_fingerprint"] or row.intent_id != intent_id
                    or row.origin_context != origin):
                raise ValueError("An existing command identity cannot be rebound to different inputs.")
            self.expire(row, now)
            return DeviceCommandClaim(row, "", False)
        active = await session.scalar(select(AccessDeviceCommandRecord).where(
            AccessDeviceCommandRecord.target_device_id == target_id,
            AccessDeviceCommandRecord.state.in_(UNRESOLVED_DEVICE_COMMAND_STATES),
        ).with_for_update())
        if active:
            self.expire(active, now)
            await session.flush()
        blocked = bool(active and active.state in UNRESOLVED_DEVICE_COMMAND_STATES)
        expired = bool(expires_at and expires_at < now)
        token = uuid.uuid4().hex
        row = AccessDeviceCommandRecord(
            target_device_id=target_id, device_key=target["device_key"], action=action,
            intent_id=intent_id, idempotency_key=key,
            origin_context=origin,
            gate_command_id=uuid.UUID(gate_command_id) if gate_command_id else None,
            recovery_version=1, state="not_sent" if blocked or expired else "prepared",
            binding_snapshot={**target["binding_snapshot"],
                              "expires_at": expires_at.isoformat() if expires_at else None},
            binding_fingerprint=target["binding_fingerprint"],
            lease_token=None if blocked or expired else token,
            lease_expires_at=None if blocked or expired else now + timedelta(seconds=DEVICE_COMMAND_LEASE_SECONDS),
            accepted=False if blocked or expired else None, gate_state="unknown",
            completed_at=now if blocked or expired else None,
            detail=("A previous command for this physical target is unresolved." if blocked
                    else "Command authority expired before dispatch." if expired else None),
            provider_receipts=[],
        )
        session.add(row)
        await session.flush()
        return DeviceCommandClaim(row, token, not blocked and not expired)

    async def lock_claim(self, session: AsyncSession, claim: DeviceCommandClaim) -> AccessDeviceCommandRecord:
        row = await session.get(AccessDeviceCommandRecord, claim.record.id, with_for_update=True, populate_existing=True)
        if row is None:
            raise RuntimeError("Device command receipt is missing.")
        return row

    async def begin_attempt(self, session: AsyncSession, claim: DeviceCommandClaim, *,
                            provider: str, external_id: str) -> bool:
        row = await session.get(AccessDeviceCommandRecord, claim.record.id, with_for_update=True,
                                populate_existing=True)
        now = await session.scalar(select(func.clock_timestamp()))
        if not row or not self._owns(row, claim.token, "prepared", now):
            if row:
                self.expire(row, now)
            return False
        expires = row.binding_snapshot.get("expires_at")
        if expires and datetime.fromisoformat(expires) < now:
            self._terminal(row, "not_sent", now, "Command authority expired before dispatch.", accepted=False)
            return False
        if not any(item["provider"] == provider and item["external_id"] == external_id
                   for item in row.binding_snapshot["providers"]):
            raise ValueError("Provider target differs from the frozen binding.")
        if any(item.get("provider") == provider for item in row.provider_receipts or []):
            raise ValueError("A provider may be attempted only once for a target operation.")
        row.state, row.attempted_at = "attempting", now
        row.provider_receipts = [*(row.provider_receipts or []), {
            "provider": provider, "external_id": external_id, "delivery": "unknown",
            "status": "attempting", "attempted_at": now.isoformat(),
        }][-MAX_PROVIDER_RECEIPTS:]
        await session.flush()
        return True

    async def finish_attempt(self, claim: DeviceCommandClaim, *, delivery: CommandDelivery,
                             state: GateState, detail: str | None,
                             observation: dict[str, Any] | None = None,
                             has_fallback: bool = False,
                             acceptance_basis: str | None = None) -> AccessDeviceCommandRecord:
        async with AsyncSessionLocal() as session:
            row = await session.get(AccessDeviceCommandRecord, claim.record.id, with_for_update=True)
            if not row:
                raise RuntimeError("Device command receipt is missing.")
            now = await session.scalar(select(func.clock_timestamp()))
            if not self._owns(row, claim.token, "attempting", now):
                self.expire(row, now)
                await session.commit()
                return row
            receipts = list(row.provider_receipts or [])
            receipts[-1] = {**receipts[-1], "delivery": delivery.value, "status": delivery.value,
                            "completed_at": now.isoformat(), "state": state.value,
                            "acceptance_basis": acceptance_basis if delivery == CommandDelivery.ACCEPTED else None}
            row.provider_receipts, row.gate_state, row.detail = receipts, state.value, detail
            row.accepted = (True if delivery == CommandDelivery.ACCEPTED
                            else None if delivery == CommandDelivery.UNKNOWN else False)
            if delivery == CommandDelivery.NOT_SENT and has_fallback:
                row.state = "prepared"
            elif delivery in {CommandDelivery.ACCEPTED, CommandDelivery.UNKNOWN}:
                row.state = delivery.value
                row.lease_token = row.lease_expires_at = None
                row.completed_at = now
                if observation is not None and delivery == CommandDelivery.ACCEPTED:
                    try:
                        await self._verify(session, row, observation, now)
                    except ValueError:
                        # Receipt acceptance is already known. An observation may
                        # become stale while this row is locked by another worker;
                        # losing that evidence must not erase the accepted receipt.
                        row.gate_state = "unknown"
                        row.detail = "Provider accepted the command; fresh physical verification is still required."
            else:
                self._terminal(row, delivery.value, now, detail, accepted=False)
            await session.commit()
            return row

    async def finish_without_send(self, claim: DeviceCommandClaim, *, detail: str,
                                  observation: dict[str, Any] | None = None) -> AccessDeviceCommandRecord:
        async with AsyncSessionLocal() as session:
            row = await self.complete_prepared(session, claim, detail=detail, observation=observation)
            await session.commit()
            return row

    async def complete_prepared(self, session: AsyncSession, claim: DeviceCommandClaim, *, detail: str,
                                observation: dict[str, Any] | None = None) -> AccessDeviceCommandRecord:
        row = await session.get(AccessDeviceCommandRecord, claim.record.id, with_for_update=True,
                                populate_existing=True)
        if not row:
            raise RuntimeError("Device command receipt is missing.")
        now = await session.scalar(select(func.clock_timestamp()))
        if self._owns(row, claim.token, "prepared", now):
            expires = row.binding_snapshot.get("expires_at")
            if expires and datetime.fromisoformat(expires) < now:
                self._terminal(row, "not_sent", now, "Command authority expired before verification.", accepted=False)
            else:
                row.accepted, row.detail = False, detail
                if observation:
                    await self._verify(session, row, observation, now)
                else:
                    self._terminal(row, "not_sent", now, detail, accepted=False)
        else:
            self.expire(row, now)
        return row

    async def reconcile_observation(self, command_id: uuid.UUID, *, binding_fingerprint: str,
                                    observation: dict[str, Any]) -> AccessDeviceCommandRecord | None:
        async with AsyncSessionLocal() as session:
            row = await session.get(AccessDeviceCommandRecord, command_id, with_for_update=True)
            if not row:
                return None
            now = await session.scalar(select(func.clock_timestamp()))
            self.expire(row, now)
            # A still-running worker owns completion. Unknown/accepted receipts are
            # resolved by fresh evidence from their exact frozen binding only.
            if row.state in {"accepted", "unknown"} and row.binding_fingerprint == binding_fingerprint:
                await self._verify(session, row, observation, now)
            await session.commit()
            return row

    async def reconcile_recorded_observation(self, command_id: uuid.UUID) -> AccessDeviceCommandRecord | None:
        async with AsyncSessionLocal() as session:
            row = await session.get(AccessDeviceCommandRecord, command_id, with_for_update=True)
            if not row:
                return None
            now = await session.scalar(select(func.clock_timestamp()))
            self.expire(row, now)
            if row.state in {"accepted", "unknown"} and row.attempted_at:
                expected = {"open", "opening"} if row.action == "open" else {"closed"}
                observation = await session.scalar(select(GateStateObservation).where(
                    GateStateObservation.gate_entity_id == row.device_key,
                    GateStateObservation.access_device_id == row.target_device_id,
                    GateStateObservation.binding_fingerprint == row.binding_fingerprint,
                    GateStateObservation.source.in_([item["provider"] for item in row.binding_snapshot["providers"]]),
                    GateStateObservation.state.in_(expected),
                    GateStateObservation.observed_at >= row.attempted_at,
                    GateStateObservation.observed_at <= row.attempted_at + timedelta(seconds=DEVICE_COMMAND_EVIDENCE_WINDOW_SECONDS),
                    GateStateObservation.observed_at <= now,
                ).order_by(GateStateObservation.observed_at.asc()).limit(1))
                if observation:
                    row.verification_observation_id = observation.id
                    row.verification_evidence = self._verification_snapshot(row, observation)
                    row.gate_state = observation.state
                    row.state, row.completed_at = "verified", now
                    row.lease_token = row.lease_expires_at = None
            await session.commit()
            return row

    async def parent_receipts(self, session: AsyncSession, parent: GateCommandRecord) -> list[dict[str, Any]]:
        plan = (parent.command_metadata or {}).get("target_plan") or {}
        now = await session.scalar(select(func.clock_timestamp()))
        for identity in sorted(target["target_device_id"] for target in plan.get("targets", [])):
            await self.lock_target(session, uuid.UUID(identity))
        rows = list((await session.scalars(select(AccessDeviceCommandRecord).where(
            AccessDeviceCommandRecord.gate_command_id == parent.id).with_for_update()
            .execution_options(populate_existing=True))).all())
        by_target = {str(row.target_device_id): row for row in rows}
        parent_expired = parent.state != GateCommandState.LEASED or not parent.lease_expires_at or parent.lease_expires_at <= now
        for target in plan.get("targets", []):
            row = by_target.get(target["target_device_id"])
            if row:
                if parent_expired and row.state == "prepared":
                    self._terminal(row, "not_sent", now, "Parent operation ended before this target was attempted.", accepted=False)
                else:
                    self.expire(row, now)
            elif parent_expired:
                # The whole target plan was committed before its first attempt.
                # Absence of a child after parent expiry proves this target was not
                # attempted; materialize that receipt without acquiring send authority.
                row = AccessDeviceCommandRecord(
                    target_device_id=uuid.UUID(target["target_device_id"]), device_key=target["device_key"],
                    action="open", intent_id=(parent.command_metadata or {}).get("intent_id") or str(parent.id),
                    idempotency_key=target_idempotency_key(parent.idempotency_key, target["target_device_id"], "open"),
                    gate_command_id=parent.id, state="not_sent", recovery_version=1,
                    binding_snapshot=target["binding_snapshot"], binding_fingerprint=target["binding_fingerprint"],
                    accepted=False, gate_state="unknown", completed_at=now, provider_receipts=[],
                    detail="Parent operation ended before this target was attempted.")
                session.add(row)
                await session.flush()
                by_target[target["target_device_id"]] = row
        return [device_command_receipt(by_target[target["target_device_id"]])
                for target in plan.get("targets", []) if target["target_device_id"] in by_target]

    @staticmethod
    async def _verify(session: AsyncSession, row: AccessDeviceCommandRecord,
                      observation: dict[str, Any], now: datetime) -> None:
        observed_at = observation["observed_at"]
        if observation["provider"] not in {item["provider"] for item in row.binding_snapshot["providers"]}:
            raise ValueError("Verification provider is absent from the frozen binding.")
        expected = {"open", "opening"} if row.action == "open" else {"closed"}
        if (observation["state"] not in expected or observed_at > now
                or observed_at < (row.attempted_at or row.created_at)
                or observed_at > (row.attempted_at or row.created_at) + timedelta(seconds=DEVICE_COMMAND_EVIDENCE_WINDOW_SECONDS)
                or (now - observed_at).total_seconds() > 10):
            raise ValueError("Physical verification requires fresh, target-specific evidence after command reservation.")
        evidence = GateStateObservation(
            gate_entity_id=row.device_key, gate_name=row.device_key,
            access_device_id=row.target_device_id, binding_fingerprint=row.binding_fingerprint,
            state=observation["state"], raw_state=observation["state"],
            observed_at=observed_at, state_changed_at=None,
            source=observation["provider"],
        )
        session.add(evidence)
        await session.flush()
        row.verification_observation_id, row.gate_state = evidence.id, observation["state"]
        row.verification_evidence = AccessDeviceCommandJournal._verification_snapshot(row, evidence)
        row.state, row.completed_at = "verified", now
        row.lease_token = row.lease_expires_at = None

    @staticmethod
    def _verification_snapshot(row: AccessDeviceCommandRecord, evidence: GateStateObservation) -> dict[str, Any]:
        return {"observation_id": str(evidence.id), "observed_at": evidence.observed_at.isoformat(),
                "state": evidence.state, "target_device_id": str(row.target_device_id), "provider": evidence.source}

    @staticmethod
    def _owns(row: AccessDeviceCommandRecord, token: str, state: str, now: datetime) -> bool:
        return bool(token and row.lease_token == token and row.state == state
                    and row.lease_expires_at and row.lease_expires_at > now)

    @staticmethod
    def _terminal(row: AccessDeviceCommandRecord, state: str, now: datetime,
                  detail: str | None, *, accepted: bool | None) -> None:
        row.state, row.completed_at, row.accepted, row.detail = state, now, accepted, detail
        row.lease_token = row.lease_expires_at = None

    @classmethod
    def expire(cls, row: AccessDeviceCommandRecord, now: datetime) -> None:
        if row.state not in {"prepared", "attempting"} or (row.lease_expires_at and row.lease_expires_at > now):
            return
        if row.state == "prepared":
            cls._terminal(row, "not_sent", now, "Reservation expired before transmission.", accepted=False)
        else:
            cls._terminal(row, "unknown", now, "Attempt expired without a conclusive receipt; no retry is permitted.", accepted=None)
            row.gate_state = "unknown"
