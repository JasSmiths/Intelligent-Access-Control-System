from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    Anomaly,
    MovementSagaRecord,
    MovementSessionRecord,
    Person,
    Presence,
    Vehicle,
    VisitorPass,
)
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    MovementSagaState,
    PresenceState,
    TimingClassification,
)
from app.modules.lpr.base import PlateRead
from app.services.access.authorization import (
    RecognitionAuthorizationDenied,
    recognition_deadline_for_event,
)
from app.services.access.delivery import reserve_observation_outputs
from app.services.access.evidence import AccessEvidenceResolver
from app.services.access.hardware import open_gate_for_access_event, publish_gate_open_skipped
from app.services.access.payloads import (
    _access_event_raw_payload,
    _movement_intent_payload,
    _raw_payload_with_movement_saga,
    access_event_realtime_payload,
)
from app.services.access.reads import (
    EXTERNAL_ADMISSION_DEPARTURE_REASON,
    EXTERNAL_ADMISSION_ORIGINAL_SUPERSEDED_REASON,
    EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
    DebounceWindow,
    _external_admission_direction_resolution,
    _external_admission_from_read,
    _external_admission_payload,
    _external_gate_open_read,
    _gate_observation_from_gate_event,
    _movement_saga_idempotency_key,
    _webhook_trace_for_window,
    lpr_ingest_id_from_read,
)
from app.services.gate_commands import GateCommandOutcome
from app.services.lpr_ingest import LprIngestRepository
from app.services.movement.admission import AdmissionSessionInput, finalize_in_session
from app.services.movement.sessions import (
    VEHICLE_SESSION_PAYLOAD_KEY,
    ExternalVehicleSessionMatch,
    MovementSessionService,
)
from app.services.movement_ledger import MovementLedgerRepository
from app.services.schedules import ScheduleEvaluation
from app.services.settings import RuntimeConfig, get_runtime_config_for_session
from app.services.snapshots import alert_snapshot_metadata_from_event
from app.services.visitor_passes import VisitorPassReservationConflict, get_visitor_pass_service

logger = get_logger(__name__)


@dataclass
class AccessExecutionResult:
    window: DebounceWindow
    read: PlateRead
    runtime: RuntimeConfig
    event: AccessEvent
    person: Person | None
    vehicle: Vehicle | None
    visitor_pass: VisitorPass | None
    visitor_pass_mode: str | None
    external_admission: dict[str, Any] | None
    direction_resolution: dict[str, Any]
    timing: TimingClassification
    anomalies: list[Anomaly]
    gate_command_required: bool
    gate_open_skipped: bool
    gate_outcome: GateCommandOutcome | None
    presence_updated: bool
    movement_saga: MovementSagaRecord


class AccessExecution:
    """Own core access transactions and dispatch through audited hardware owners."""

    def __init__(
        self,
        movement_ledger: MovementLedgerRepository,
        movement_sessions: MovementSessionService,
        ingest: LprIngestRepository,
    ) -> None:
        self._movement_ledger = movement_ledger
        self._movement_sessions = movement_sessions
        self._ingest = ingest

    async def execute(
        self,
        window: DebounceWindow,
        *,
        read: PlateRead,
        direction_read: PlateRead,
        runtime: RuntimeConfig,
        trace: Any,
        finalize_started_at: datetime,
        webhook_trace: dict[str, Any],
    ) -> AccessExecutionResult | None:
        external_admission = _external_admission_from_read(read)
        presence_updated = False
        gate_command_required = False
        gate_open_skipped = False
        gate_outcome: GateCommandOutcome | None = None
        movement_saga = None
        async with AsyncSessionLocal() as session:
            existing = await self._movement_ledger.movement_saga_by_idempotency_key(
                session, _movement_saga_idempotency_key(read))
            if existing and existing.access_event_id:
                return None
        camera_evidence = await AccessEvidenceResolver(runtime).prepare_camera_evidence(
            read, direction_read, external_admission, trace)
        async with AsyncSessionLocal() as session:
            existing = await self._movement_ledger.movement_saga_by_idempotency_key(
                session, _movement_saga_idempotency_key(read)
            )
            if existing and existing.access_event_id:
                return None
            runtime = await get_runtime_config_for_session(session)
            evidence = await AccessEvidenceResolver(runtime).resolve(
                session, read, direction_read, external_admission, trace,
                camera_evidence=camera_evidence,
            )
            vehicle, person = evidence.vehicle, evidence.person
            visitor_pass, visitor_pass_mode = evidence.visitor_pass, evidence.visitor_pass_mode
            schedule_evaluation, direction_resolution = (
                evidence.schedule_evaluation,
                evidence.direction_resolution,
            )
            timing, plan = evidence.timing, evidence.plan
            allowed, direction, decision = plan.allowed, plan.direction, plan.decision
            # Captured gate state is direction evidence, not dispatch authority.
            # Every live authorized entry reaches the audited owner for fresh
            # designated-entry evidence; that owner may issue zero commands.
            gate_command_required = bool(allowed and direction == AccessDirection.ENTRY and not external_admission)
            movement_saga = await self._movement_ledger.create_movement_saga(
                session,
                idempotency_key=_movement_saga_idempotency_key(read),
                source=read.source,
                occurred_at=read.captured_at,
                registration_number=read.registration_number,
                person_id=person.id if person else None,
                vehicle_id=vehicle.id if vehicle else None,
                direction=direction,
                decision=decision,
                state=MovementSagaState.DIRECTION_RESOLVED,
                intent_payload=_movement_intent_payload(read, person, vehicle, allowed),
                decision_payload=direction_resolution,
            )
            # The uniqueness owner handles concurrent insertion. Lock/reload the returned
            # saga before attaching an event, including pre-existing incomplete rows.
            await session.refresh(movement_saga, with_for_update=True)
            if movement_saga.access_event_id:
                return None
            dvla_enrichment = None
            vehicle_visual_detection = None
            persistence_span = trace.start_span(
                "Persist Access Event, Presence, and Anomalies",
                attributes={"decision": decision.value, "direction": direction.value},
            )
            event = AccessEvent(
                vehicle=vehicle,
                person_id=person.id if person else None,
                registration_number=read.registration_number,
                direction=direction,
                decision=decision,
                confidence=read.confidence,
                source=read.source,
                occurred_at=read.captured_at,
                timing_classification=timing,
                raw_payload=_access_event_raw_payload(
                    window=window,
                    read=read,
                    schedule_evaluation=schedule_evaluation,
                    direction_resolution=direction_resolution,
                    vehicle_visual_detection=vehicle_visual_detection,
                    visitor_pass=visitor_pass,
                    visitor_pass_mode=visitor_pass_mode,
                    external_admission=external_admission,
                    trace_id=trace.trace_id,
                    finalize_started_at=finalize_started_at,
                    webhook_trace=webhook_trace,
                ),
            )
            session.add(event)
            await session.flush()
            await self._movement_ledger.transition_movement_saga(
                session,
                movement_saga,
                MovementSagaState.PHYSICAL_COMMAND_PENDING
                if gate_command_required
                else MovementSagaState.COMPLETED,
                detail="access_event_persisted",
                access_event_id=event.id,
                gate_command_required=gate_command_required,
                presence_committed=False,
                decision_payload=direction_resolution,
            )
            event.raw_payload = {
                **(event.raw_payload or {}),
                VEHICLE_SESSION_PAYLOAD_KEY: self._movement_sessions.initial_payload(
                    window.reads,
                    first_seen=window.first_seen,
                    updated_at=window.updated_at,
                    read=read,
                    event=event,
                ),
            }

            await self._ingest.mark_ids_succeeded_in_session(
                session,
                [
                    ingest_id
                    for item in window.reads
                    if (ingest_id := lpr_ingest_id_from_read(item))
                ],
                access_event_id=event.id,
                movement_saga_id=movement_saga.id if movement_saga else None,
            )

            if visitor_pass:
                visitor_service = get_visitor_pass_service()
                if visitor_pass_mode == "arrival":
                    try:
                        await visitor_service.reserve_arrival_in_session(session,
                            visitor_pass_id=visitor_pass.id, event=event,
                            intent_id=uuid.uuid5(event.id, "automatic-gate-open"),
                            dispatch_deadline=await recognition_deadline_for_event(session, event))
                    except (VisitorPassReservationConflict, RecognitionAuthorizationDenied) as exc:
                        # Preserve the observed event and refusal. Never consume a
                        # second pass or retry ingestion to hide this conflict.
                        allowed = False
                        decision, direction = AccessDecision.DENIED, AccessDirection.DENIED
                        event.decision, event.direction = decision, direction
                        movement_saga.decision, movement_saga.direction = decision, direction
                        gate_command_required = False
                        movement_saga.gate_command_required = False
                        event.raw_payload = {**(event.raw_payload or {}), "visitor_reservation": {
                            "status": "denied", "reason": str(exc), "pass_id": str(visitor_pass.id)}}
                elif visitor_pass_mode == "departure":
                    await visitor_service.record_departure(session, visitor_pass, event=event)
            if external_admission:
                await self._mark_external_admission_linked_session(
                    session,
                    external_admission,
                    event_id=event.id,
                    observed_at=read.captured_at,
                )

            anomalies = await self._build_anomalies(
                session,
                event,
                person,
                vehicle,
                allowed,
                visitor_pass=visitor_pass,
                external_admission=external_admission,
            )
            session.add_all(anomalies)

            gate_open_skipped = bool(external_admission and direction == AccessDirection.ENTRY)
            admission = await finalize_in_session(session, saga_id=movement_saga.id,
                mode="external" if external_admission else "live",
                historical_evidence=external_admission,
                session_input=AdmissionSessionInput(window.reads, window.first_seen, window.updated_at, read, runtime))
            event, movement_saga = admission.event, admission.saga
            presence_updated = admission.presence_changed
            await reserve_observation_outputs(session, event=event, person=person, vehicle=vehicle,
                visitor_pass=visitor_pass, visitor_pass_mode=visitor_pass_mode, anomalies=anomalies)

            await session.commit()

        if gate_command_required:
            gate_outcome = await open_gate_for_access_event(
                event,
                person,
                open_garage_doors=True,
                trace=trace,
                dvla_enrichment=dvla_enrichment,
                movement_saga_id=str(movement_saga.id) if movement_saga else None,
            )
            async with AsyncSessionLocal() as session:
                admission = await finalize_in_session(session, saga_id=movement_saga.id,
                    gate_command_id=gate_outcome.command_id,
                    session_input=AdmissionSessionInput(window.reads, window.first_seen, window.updated_at, read, runtime))
                # Return the canonical transition snapshot even if optional enrichment fails.
                if visitor_pass is not None:
                    visitor_pass = await session.get(VisitorPass, visitor_pass.id)
                await session.commit()
                event, movement_saga = admission.event, admission.saga
                presence_updated = admission.presence_changed
        elif gate_open_skipped:
            await publish_gate_open_skipped(event, direction_resolution, person)

        persistence_span.finish(
            output_payload={
                "event_id": str(event.id),
                "anomaly_count": len(anomalies),
                "presence_updated": presence_updated,
                "gate_command_required": gate_command_required,
                "gate_command_accepted": gate_outcome.accepted if gate_outcome else None,
            }
        )
        return AccessExecutionResult(
            window,
            read,
            runtime,
            event,
            person,
            vehicle,
            visitor_pass,
            visitor_pass_mode,
            external_admission,
            direction_resolution,
            timing,
            anomalies,
            gate_command_required,
            gate_open_skipped,
            gate_outcome,
            presence_updated,
            movement_saga,
        )

    async def _build_anomalies(
        self,
        session: AsyncSession,
        event: AccessEvent,
        person: Person | None,
        vehicle: Vehicle | None,
        allowed: bool,
        *,
        visitor_pass: VisitorPass | None = None,
        external_admission: dict[str, Any] | None = None,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        if not vehicle:
            if visitor_pass:
                return anomalies
            external_mode = str((external_admission or {}).get("mode") or "").strip().lower()
            if external_mode == "departure":
                return anomalies
            anomaly = Anomaly(
                event=event,
                anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
                severity=AnomalySeverity.WARNING,
                message=(
                    "Unauthorised Plate, Admitted Externally"
                    if external_mode == "arrival"
                    else "Unauthorised Plate, Access Denied"
                ),
                context={
                    key: value
                    for key, value in {
                        "registration_number": event.registration_number,
                        "snapshot": alert_snapshot_metadata_from_event(event),
                        "external_admission": external_admission
                        if external_mode == "arrival"
                        else None,
                    }.items()
                    if value is not None
                },
            )
            anomalies.append(anomaly)
            return anomalies

        if not allowed:
            subject = person.display_name if person else event.registration_number
            anomalies.append(
                Anomaly(
                    event=event,
                    anomaly_type=AnomalyType.OUTSIDE_SCHEDULE,
                    severity=AnomalySeverity.WARNING,
                    message=f"{subject} was denied by schedule or access policy.",
                    context={
                        "person_id": str(person.id) if person else None,
                        "vehicle_id": str(vehicle.id),
                    },
                )
            )
            return anomalies

        if not person:
            return anomalies

        presence = await session.get(Presence, person.id)

        if (
            presence
            and event.direction == AccessDirection.ENTRY
            and presence.state == PresenceState.PRESENT
        ):
            anomalies.append(
                Anomaly(
                    event=event,
                    anomaly_type=AnomalyType.DUPLICATE_ENTRY,
                    severity=AnomalySeverity.WARNING,
                    message=f"{person.display_name} produced an entry while already present.",
                    context={"person_id": str(person.id)},
                )
            )
        if (
            presence
            and event.direction == AccessDirection.EXIT
            and presence.state == PresenceState.EXITED
        ):
            anomalies.append(
                Anomaly(
                    event=event,
                    anomaly_type=AnomalyType.DUPLICATE_EXIT,
                    severity=AnomalySeverity.INFO,
                    message=f"{person.display_name} produced an exit while already marked exited.",
                    context={"person_id": str(person.id)},
                )
            )
        return anomalies

    async def _mark_external_admission_linked_session(
        self,
        session: AsyncSession,
        external_admission: dict[str, Any],
        *,
        event_id: uuid.UUID,
        observed_at: datetime,
    ) -> None:
        mode = str(external_admission.get("mode") or "").strip().lower()
        if mode == "departure":
            session_id = external_admission.get(
                "external_admission_movement_session_id"
            ) or external_admission.get("linked_movement_session_id")
            reason = EXTERNAL_ADMISSION_DEPARTURE_REASON
        else:
            session_id = external_admission.get(
                "original_denied_movement_session_id"
            ) or external_admission.get("linked_movement_session_id")
            reason = EXTERNAL_ADMISSION_ORIGINAL_SUPERSEDED_REASON
        if not session_id:
            return
        try:
            row_id = uuid.UUID(str(session_id))
        except (TypeError, ValueError):
            return
        row = await session.get(MovementSessionRecord, row_id)
        if not row:
            return
        await self._movement_sessions.mark_external_session_superseded(
            session,
            row,
            reason=reason,
            matched_by=str(
                external_admission.get("matched_by")
                or external_admission.get("source")
                or "external_admission"
            ),
            evidence=external_admission.get("presence_evidence")
            if isinstance(external_admission.get("presence_evidence"), dict)
            else None,
            event_id=event_id,
            observed_at=observed_at,
        )

    async def _persist_external_gate_open_admission(
        self,
        session: AsyncSession,
        match: ExternalVehicleSessionMatch,
        *,
        observed_at: datetime,
        gate_payload: dict[str, Any],
        runtime: RuntimeConfig,
    ) -> tuple[AccessEvent, list[Anomaly], dict[str, Any]] | None:
        session_id = str(getattr(match.session, "id", "") or "")
        movement_saga = await self._movement_ledger.create_movement_saga(
            session,
            idempotency_key=f"movement:external-admission:arrival:{session_id}",
            source=EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
            occurred_at=observed_at,
            registration_number=str(
                getattr(match.session, "registration_number", "")
                or match.access_event.registration_number
            ),
            direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,
            state=MovementSagaState.DIRECTION_RESOLVED,
            intent_payload={
                "source": EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
                "registration_number": str(
                    getattr(match.session, "registration_number", "")
                    or match.access_event.registration_number
                ),
                "captured_at": observed_at.isoformat(),
                "allowed": True,
                "person_id": None,
                "vehicle_id": None,
                "external_admission": {
                    "mode": "arrival",
                    "source": EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
                    "original_denied_access_event_id": str(match.access_event.id),
                    "original_denied_movement_session_id": session_id,
                },
                "gate_observation": _gate_observation_from_gate_event(gate_payload, observed_at),
            },
        )
        await session.refresh(movement_saga, with_for_update=True)
        if movement_saga.access_event_id:
            return None

        external_admission = _external_admission_payload(
            match,
            mode="arrival",
            source=EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
            observed_at=observed_at,
            gate_observation=_gate_observation_from_gate_event(gate_payload, observed_at),
            gate_payload=gate_payload,
        )
        read = _external_gate_open_read(
            match,
            observed_at=observed_at,
            gate_payload=gate_payload,
            external_admission=external_admission,
        )
        window = DebounceWindow(first_seen=observed_at, updated_at=observed_at, reads=[read])
        direction, direction_resolution = _external_admission_direction_resolution(
            read, external_admission
        )
        schedule_evaluation = ScheduleEvaluation(
            allowed=True,
            source="external_admission_arrival",
            reason="Unknown vehicle was admitted by an external gate opening while Protect still showed it present.",
        )
        webhook_trace = _webhook_trace_for_window(window)
        event = AccessEvent(
            vehicle=None,
            person_id=None,
            registration_number=read.registration_number,
            direction=direction,
            decision=AccessDecision.GRANTED,
            confidence=read.confidence,
            source=EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
            occurred_at=observed_at,
            timing_classification=TimingClassification.UNKNOWN,
            raw_payload=_access_event_raw_payload(
                window=window,
                read=read,
                schedule_evaluation=schedule_evaluation,
                direction_resolution=direction_resolution,
                vehicle_visual_detection=None,
                visitor_pass=None,
                visitor_pass_mode=None,
                external_admission=external_admission,
                trace_id=uuid.uuid4().hex,
                finalize_started_at=datetime.now(tz=UTC),
                webhook_trace=webhook_trace,
            ),
        )
        session.add(event)
        await session.flush()
        await self._movement_ledger.transition_movement_saga(
            session,
            movement_saga,
            MovementSagaState.COMPLETED,
            detail="external_gate_open_admission_recorded",
            access_event_id=event.id,
            gate_command_required=False,
            presence_committed=False,
            decision_payload=direction_resolution,
        )
        event.raw_payload = {
            **(event.raw_payload or {}),
            VEHICLE_SESSION_PAYLOAD_KEY: self._movement_sessions.initial_payload(
                window.reads,
                first_seen=window.first_seen,
                updated_at=window.updated_at,
                read=read,
                event=event,
            ),
        }
        await finalize_in_session(session, saga_id=movement_saga.id, mode="external",
            historical_evidence=external_admission,
            session_input=AdmissionSessionInput(window.reads, window.first_seen, window.updated_at, read, runtime))
        await self._movement_sessions.mark_external_session_superseded(
            session,
            match.session,
            reason=EXTERNAL_ADMISSION_ORIGINAL_SUPERSEDED_REASON,
            matched_by=match.matched_by,
            evidence=match.evidence,
            event_id=event.id,
            observed_at=observed_at,
        )
        anomalies = await self._build_anomalies(
            session,
            event,
            None,
            None,
            True,
            external_admission=external_admission,
        )
        session.add_all(anomalies)
        await reserve_observation_outputs(session, event=event, person=None, vehicle=None,
            visitor_pass=None, visitor_pass_mode=None, anomalies=anomalies)
        event.raw_payload = _raw_payload_with_movement_saga(
            event.raw_payload,
            state="completed",
            gate_command_required=False,
            presence_committed=False,
            movement_saga=movement_saga,
            detail="External gate opening recorded; no IACS hardware command was sent.",
        )
        realtime_payload = access_event_realtime_payload(
            event,
            anomaly_count=len(anomalies),
            visitor_pass=None,
            visitor_pass_mode=None,
        )
        saga_payload = (event.raw_payload or {}).get("movement_saga")
        if isinstance(saga_payload, dict):
            realtime_payload["movement_saga"] = saga_payload
        return event, anomalies, realtime_payload
