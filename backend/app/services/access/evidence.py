from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.providers import analyze_image_with_provider
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Person, Presence, Vehicle, VisitorPass
from app.models.enums import AccessDecision, AccessDirection, PresenceState, TimingClassification
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services.access.decision import AccessPlan, access_is_allowed, plan_access
from app.services.access.payloads import vehicle_display_name
from app.services.access.reads import (
    CAMERA_TIEBREAKER_MIN_CONFIDENCE,
    GATE_CAMERA_IDENTIFIER,
    _external_admission_direction_resolution,
    _gate_malfunction_from_read,
    _visitor_pass_candidate_kind,
    _visitor_pass_plate_match_from_read,
)
from app.services.event_bus import event_bus
from app.services.movement.sessions import (
    coerce_access_direction,
    coerce_gate_state,
    explicit_direction_from_read,
    gate_observation_from_read,
)
from app.services.movement.sessions import (
    known_vehicle_plate_match_from_read as _known_vehicle_plate_match_from_read,
)
from app.services.movement_fsm import CameraTieBreakerEvidence, MovementDirectionFSM, MovementIntent
from app.services.schedules import (
    ScheduleEvaluation,
    evaluate_vehicle_schedule,
    schedule_evaluation_payload,
)
from app.services.settings import RuntimeConfig, get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_LPR, telemetry
from app.services.unifi_protect import get_unifi_protect_service
from app.services.visitor_passes import get_visitor_pass_service

logger = get_logger(__name__)


@dataclass(frozen=True)
class PreparedCameraEvidence:
    person_id: uuid.UUID
    read_fingerprint: str
    decision: dict[str, Any]

    @classmethod
    def for_read(cls, person_id: uuid.UUID, read: PlateRead, decision: dict[str, Any]) -> PreparedCameraEvidence:
        return cls(person_id, _read_fingerprint(read), dict(decision))


def _read_fingerprint(read: PlateRead) -> str:
    value = {"source": read.source, "captured_at": read.captured_at.isoformat(),
             "registration_number": read.registration_number, "raw_payload": read.raw_payload,
             "candidates": read.candidate_registration_numbers, "confidence": read.confidence}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


class CameraEvidenceRequired(Exception):
    """A read-only probe must end its transaction before optional vendor I/O."""
    def __init__(self, person: Person):
        self.person_id = person.id
        self.display_name = person.display_name


@dataclass(frozen=True)
class AccessEvidence:
    vehicle: Vehicle | None
    person: Person | None
    visitor_pass: VisitorPass | None
    visitor_pass_mode: str | None
    schedule_evaluation: ScheduleEvaluation | None
    direction_resolution: dict[str, Any]
    timing: TimingClassification
    plan: AccessPlan


class AccessEvidenceResolver:
    """Resolve current domain facts; acquire optional camera evidence separately."""

    def __init__(self, runtime: RuntimeConfig | None = None):
        self._runtime = runtime
        self._timezone = ZoneInfo(runtime.site_timezone if runtime else settings.site_timezone)
        self._movement_direction_fsm = MovementDirectionFSM()

    async def prepare_camera_evidence(
        self, read: PlateRead, direction_read: PlateRead,
        external_admission: dict[str, Any] | None, trace: Any,
    ) -> PreparedCameraEvidence | None:
        request = None
        async with AsyncSessionLocal() as session:
            try:
                await self.resolve(session, read, direction_read, external_admission, trace,
                                   probe_camera=True)
            except CameraEvidenceRequired as needed:
                request = needed
        if request is None:
            return None
        # Only immutable identifying presentation crosses this seam; the final
        # transaction resolves identity, permissions and presence again.
        person = SimpleNamespace(id=request.person_id, display_name=request.display_name)
        decision = await self._resolve_duplicate_arrival_with_camera(direction_read, person, trace=trace)
        return PreparedCameraEvidence.for_read(request.person_id, direction_read, decision)

    async def resolve(
        self,
        session: AsyncSession,
        read: PlateRead,
        direction_read: PlateRead,
        external_admission: dict[str, Any] | None,
        trace: Any,
        *,
        camera_evidence: PreparedCameraEvidence | None = None,
        probe_camera: bool = False,
    ) -> AccessEvidence:
        vehicle = await self._lookup_active_vehicle(session, read, trace)
        person = vehicle.owner if vehicle else None
        runtime = self._runtime or await get_runtime_config()
        identity_active = bool(vehicle and (not person or person.is_active))
        if external_admission:
            visitor_pass, visitor_pass_mode = None, None
        elif not vehicle:
            visitor_pass, visitor_pass_mode = await self._match_visitor_pass(
                session, read, direction_read, trace
            )
        else:
            visitor_pass, visitor_pass_mode = None, None
        if external_admission:
            mode = str(external_admission.get("mode") or "arrival")
            schedule_evaluation = ScheduleEvaluation(
                allowed=True,
                source=f"external_admission_{mode}",
                reason=(
                    "Unknown vehicle was admitted externally and is being tracked as a vehicle session."
                    if mode == "arrival"
                    else "Externally admitted unknown vehicle departure was linked to the active vehicle session."
                ),
            )
        else:
            schedule_evaluation = await self._schedule_evaluation_for_detection(
                session,
                vehicle=vehicle,
                identity_active=identity_active,
                visitor_pass=visitor_pass,
                visitor_pass_mode=visitor_pass_mode,
                captured_at=read.captured_at,
                runtime=runtime,
                trace=trace,
            )
        allowed = access_is_allowed(
            schedule_allowed=bool(schedule_evaluation and schedule_evaluation.allowed),
            identity_active=identity_active,
            visitor_pass_matched=bool(visitor_pass),
            external_admission_matched=bool(external_admission),
        )
        direction_span = trace.start_span(
            "Direction Classification",
            attributes={
                "allowed": allowed,
                "gate_observation": gate_observation_from_read(direction_read),
                "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
                "external_admission_mode": external_admission.get("mode")
                if external_admission
                else None,
            },
        )
        if external_admission:
            direction, direction_resolution = _external_admission_direction_resolution(
                direction_read,
                external_admission,
            )
        else:
            try:
                direction, direction_resolution = await self._resolve_direction(
                    session, direction_read, person, allowed, vehicle=vehicle, trace=trace,
                    camera_evidence=camera_evidence, probe_camera=probe_camera,
                )
            except CameraEvidenceRequired:
                direction_span.finish(output_payload={"requires_external_evidence": "camera_tiebreaker"})
                raise
            except BaseException:
                direction_span.finish(status="error", output_payload={"direction": "unresolved"})
                raise
        direction_span.finish(
            output_payload={
                "direction": direction.value,
                "resolution": direction_resolution,
            }
        )
        timing_span = trace.start_span(
            "Presence Timing Classification",
            attributes={
                "allowed": allowed,
                "person_id": str(person.id) if person else None,
                "direction": direction.value,
            },
        )
        timing = (
            await self._classify_timing(session, person, direction, read.captured_at)
            if allowed and person
            else TimingClassification.UNKNOWN
        )
        timing_span.finish(output_payload={"timing_classification": timing.value})

        plan = plan_access(
            allowed=allowed,
            direction=direction,
            gate_state=coerce_gate_state(
                direction_resolution.get("gate_observation", {}).get("state")
            ),
            hardware_suppressed=bool(external_admission),
        )
        return AccessEvidence(
            vehicle,
            person,
            visitor_pass,
            visitor_pass_mode,
            schedule_evaluation,
            direction_resolution,
            timing,
            plan,
        )

    def _access_event_is_backfilled(self, event: AccessEvent) -> bool:
        if "backfill" in str(event.source or "").casefold():
            return True
        raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
        return "backfill" in raw_payload

    def _camera_tiebreaker_is_clear(self, camera_decision: dict[str, Any]) -> bool:
        confidence = self._coerce_confidence(camera_decision.get("confidence"))
        return confidence is not None and confidence >= CAMERA_TIEBREAKER_MIN_CONFIDENCE

    async def _classify_timing(
        self,
        session: AsyncSession,
        person: Person,
        direction: AccessDirection,
        occurred_at: datetime,
    ) -> TimingClassification:
        local_event = occurred_at.astimezone(self._timezone)
        event_minutes = local_event.hour * 60 + local_event.minute

        historical = (
            await session.scalars(
                select(AccessEvent)
                .where(
                    AccessEvent.person_id == person.id,
                    AccessEvent.direction == direction,
                    AccessEvent.decision == AccessDecision.GRANTED,
                    AccessEvent.occurred_at < occurred_at,
                )
                .order_by(AccessEvent.occurred_at.desc())
                .limit(20)
            )
        ).all()

        if len(historical) < 3:
            return TimingClassification.UNKNOWN

        historical_minutes = [
            item.occurred_at.astimezone(self._timezone).hour * 60
            + item.occurred_at.astimezone(self._timezone).minute
            for item in historical
        ]
        average_minutes = sum(historical_minutes) / len(historical_minutes)
        delta = event_minutes - average_minutes

        if delta < -45:
            return TimingClassification.EARLIER_THAN_USUAL
        if delta > 45:
            return TimingClassification.LATER_THAN_USUAL
        return TimingClassification.NORMAL

    def _coerce_confidence(self, value: Any) -> float | None:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        if confidence > 1:
            confidence = confidence / 100
        return max(0.0, min(confidence, 1.0))

    def _json_object_from_text(self, text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                stripped,
                flags=re.IGNORECASE | re.DOTALL,
            ).strip()
        candidates = [stripped]
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    async def _latest_live_person_or_vehicle_event(
        self,
        session: AsyncSession,
        *,
        person: Person | None,
        vehicle: Vehicle | None,
        before: datetime,
    ) -> AccessEvent | None:
        identity_filters = []
        if person and getattr(person, "id", None):
            identity_filters.append(AccessEvent.person_id == person.id)
        if vehicle and getattr(vehicle, "id", None):
            identity_filters.append(AccessEvent.vehicle_id == vehicle.id)
        if not identity_filters:
            return None
        rows = (
            await session.scalars(
                select(AccessEvent)
                .where(
                    or_(*identity_filters),
                    AccessEvent.decision == AccessDecision.GRANTED,
                    AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
                    AccessEvent.occurred_at < before,
                )
                .order_by(AccessEvent.occurred_at.desc(), AccessEvent.id.desc())
                .limit(20)
            )
        ).all()
        return next((event for event in rows if not self._access_event_is_backfilled(event)), None)

    async def _lookup_active_vehicle(
        self, session: AsyncSession, read: PlateRead, trace: Any
    ) -> Vehicle | None:
        vehicle_span = trace.start_span(
            "Plate Verification against Vehicle DB",
            attributes={"registration_number": read.registration_number},
        )
        vehicle = await session.scalar(
            select(Vehicle)
            .options(
                selectinload(Vehicle.schedule),
                selectinload(Vehicle.owner).selectinload(Person.group),
                selectinload(Vehicle.owner).selectinload(Person.schedule),
            )
            .where(
                Vehicle.registration_number == read.registration_number,
                Vehicle.is_active.is_(True),
            )
        )
        vehicle_span.finish(
            output_payload={
                "matched": bool(vehicle),
                "vehicle_id": str(vehicle.id) if vehicle else None,
                "person_id": str(vehicle.person_id) if vehicle and vehicle.person_id else None,
                "vehicle": vehicle_display_name(vehicle, read.registration_number)
                if vehicle
                else None,
                "owner": vehicle.owner.display_name if vehicle and vehicle.owner else None,
                "known_vehicle_plate_match": _known_vehicle_plate_match_from_read(read),
            }
        )
        return vehicle

    async def _match_visitor_pass(
        self,
        session: AsyncSession,
        read: PlateRead,
        direction_read: PlateRead,
        trace: Any,
    ) -> tuple[VisitorPass | None, str | None]:
        visitor_pass_mode = _visitor_pass_candidate_kind(direction_read)
        visitor_span = trace.start_span(
            "Visitor Pass Matching",
            attributes={
                "registration_number": read.registration_number,
                "candidate_kind": visitor_pass_mode,
            },
        )
        visitor_service = get_visitor_pass_service()
        visitor_pass: VisitorPass | None = None
        if visitor_pass_mode == "arrival":
            visitor_pass = await visitor_service.find_arrival_candidate(
                session,
                occurred_at=read.captured_at,
                registration_number=read.registration_number,
            )
        elif visitor_pass_mode == "departure":
            visitor_pass = await visitor_service.find_departure_pass(
                session,
                occurred_at=read.captured_at,
                registration_number=read.registration_number,
            )
        visitor_span.finish(
            output_payload={
                "matched": bool(visitor_pass),
                "mode": visitor_pass_mode,
                "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
                "visitor_name": visitor_pass.visitor_name if visitor_pass else None,
            }
        )
        return visitor_pass, visitor_pass_mode

    def _normalize_camera_direction(self, value: Any) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {
            "entry",
            "enter",
            "arrival",
            "arriving",
            "toward_camera",
            "towards_camera",
        }:
            return AccessDirection.ENTRY.value
        if normalized in {"exit", "leave", "leaving", "departure", "departing", "away_from_camera"}:
            return AccessDirection.EXIT.value
        return "unknown"

    def _parse_camera_direction_analysis(self, text: str) -> tuple[str, float | None, str | None]:
        parsed = self._json_object_from_text(text)
        if parsed:
            direction = self._normalize_camera_direction(parsed.get("direction"))
            confidence = self._coerce_confidence(parsed.get("confidence"))
            reason = str(parsed.get("reason") or "").strip() or None
            if direction != "unknown":
                return direction, confidence, reason

        normalized = text.lower()
        if any(
            phrase in normalized
            for phrase in ("away from the camera", "facing away", "leaving", "departing", "rear of")
        ):
            return AccessDirection.EXIT.value, None, text[:240].strip() or None
        if any(
            phrase in normalized
            for phrase in (
                "towards the camera",
                "toward the camera",
                "facing the camera",
                "arriving",
                "approaching",
            )
        ):
            return AccessDirection.ENTRY.value, None, text[:240].strip() or None
        return "unknown", None, text[:240].strip() or None

    async def _presence_state_for_person(
        self,
        session: AsyncSession,
        person: Person,
    ) -> PresenceState | None:
        presence = await session.get(Presence, person.id)
        return presence.state if presence else None

    def _previous_event_match_scope(
        self,
        event: AccessEvent | None,
        *,
        person: Person | None,
        vehicle: Vehicle | None,
    ) -> str | None:
        if not event:
            return None
        scopes = []
        if person and getattr(event, "person_id", None) == getattr(person, "id", None):
            scopes.append("person")
        if vehicle and getattr(event, "vehicle_id", None) == getattr(vehicle, "id", None):
            scopes.append("vehicle")
        if scopes:
            return "+".join(scopes)
        if person and vehicle:
            return "person_or_vehicle"
        if person:
            return "person"
        if vehicle:
            return "vehicle"
        return None

    async def _resolve_direction(
        self,
        session: AsyncSession,
        read: PlateRead,
        person: Person | None,
        allowed: bool,
        *,
        vehicle: Vehicle | None = None,
        trace: Any | None = None,
        camera_evidence: PreparedCameraEvidence | None = None,
        probe_camera: bool = False,
    ) -> tuple[AccessDirection, dict[str, Any]]:
        gate_observation = gate_observation_from_read(read)
        gate_state = coerce_gate_state(gate_observation.get("state")) or GateState.UNKNOWN
        visitor_pass_match = _visitor_pass_plate_match_from_read(read)
        visitor_pass_departure = (
            isinstance(visitor_pass_match, dict) and visitor_pass_match.get("kind") == "departure"
        )
        gate_malfunction = _gate_malfunction_from_read(read)
        previous_event: AccessEvent | None = None
        previous_event_payload: dict[str, Any] = {}
        if gate_malfunction and (person or vehicle):
            previous_event = await self._latest_live_person_or_vehicle_event(
                session,
                person=person,
                vehicle=vehicle,
                before=read.captured_at,
            )
            match_scope = self._previous_event_match_scope(
                previous_event, person=person, vehicle=vehicle
            )
            previous_event_payload = {
                "previous_live_match_scope": match_scope,
                "previous_live_event_id": str(previous_event.id) if previous_event else None,
                "previous_live_direction": previous_event.direction.value
                if previous_event
                else None,
                "previous_live_event_at": previous_event.occurred_at.isoformat()
                if previous_event
                else None,
            }

        presence_state = await self._presence_state_for_person(session, person) if person else None
        camera_tiebreaker: CameraTieBreakerEvidence | None = None
        while True:
            decision = self._movement_direction_fsm.resolve(
                MovementIntent(
                    source=read.source,
                    captured_at=read.captured_at,
                    registration_number=read.registration_number,
                    allowed=allowed,
                    person_known=person is not None,
                    vehicle_known=vehicle is not None,
                    gate_state=gate_state,
                    gate_observation=gate_observation,
                    presence_state=presence_state,
                    explicit_direction=explicit_direction_from_read(read),
                    visitor_pass_departure=visitor_pass_departure,
                    gate_malfunction=gate_malfunction,
                    previous_live_direction=previous_event.direction if previous_event else None,
                    previous_live_event_payload=previous_event_payload,
                    camera_tiebreaker=camera_tiebreaker,
                )
            )
            if decision.requires_external_evidence != "camera_tiebreaker" or not person:
                if visitor_pass_departure and isinstance(visitor_pass_match, dict):
                    decision.resolution["visitor_pass_id"] = visitor_pass_match.get(
                        "visitor_pass_id"
                    )
                return decision.direction, decision.resolution

            if probe_camera:
                raise CameraEvidenceRequired(person)
            if camera_tiebreaker is not None:
                raise RuntimeError("Direction evaluation requested the same external evidence twice.")
            camera_decision = (camera_evidence.decision
                if camera_evidence is not None and camera_evidence.person_id == person.id
                    and camera_evidence.read_fingerprint == _read_fingerprint(read)
                else {"direction": "unknown", "confidence": 0.0,
                      "reason": "No camera evidence prepared for the current identity."})
            camera_tiebreaker = CameraTieBreakerEvidence(
                direction=coerce_access_direction(camera_decision.get("direction")),
                confidence=self._coerce_confidence(camera_decision.get("confidence")),
                clear=self._camera_tiebreaker_is_clear(camera_decision),
                payload=camera_decision,
            )

    async def _resolve_duplicate_arrival_with_camera(
        self, read: PlateRead, person: Person, *, trace: Any | None = None
    ) -> dict[str, Any]:
        runtime = self._runtime or await get_runtime_config()
        prompt = (
            "You are resolving an access-control direction conflict. "
            f"The top gate was closed when plate {read.registration_number} was read, "
            f"but {person.display_name} is already marked present. "
            "Inspect the gate camera snapshot and decide whether the visible vehicle is facing "
            "towards the camera, which means Arriving, or away from the camera, which means Leaving. "
            'Return only JSON like {"direction":"entry|exit|unknown","confidence":0.0,"reason":"short reason"}.'
        )
        camera_span = (
            trace.start_span(
                "LLM Vision Direction Tie-breaker",
                category=TELEMETRY_CATEGORY_LPR,
                attributes={
                    "provider": "openai",
                    "model": runtime.openai_model,
                    "camera": GATE_CAMERA_IDENTIFIER,
                    "prompt_category": "access_direction_tiebreaker",
                },
            )
            if trace
            else None
        )
        artifact: dict[str, Any] | None = None
        try:
            media = await get_unifi_protect_service().snapshot(
                GATE_CAMERA_IDENTIFIER,
                width=runtime.unifi_protect_snapshot_width,
                height=runtime.unifi_protect_snapshot_height,
            )
            if trace:
                artifact = await telemetry.store_artifact(
                    media.content,
                    content_type=media.content_type,
                    kind="camera_snapshot",
                    trace_id=trace.trace_id,
                    span_id=camera_span.span_id if camera_span else None,
                    metadata={
                        "camera": GATE_CAMERA_IDENTIFIER,
                        "registration_number": read.registration_number,
                        "person_id": str(person.id),
                    },
                )
            result = await analyze_image_with_provider(
                "openai",
                prompt=prompt,
                image_bytes=media.content,
                mime_type=media.content_type,
            )
        except asyncio.CancelledError:
            if camera_span:
                camera_span.finish(status="cancelled", output_payload={"direction": "unknown"})
            raise
        except Exception as exc:  # noqa: BLE001 - isolate provider/reporting failure; cancellation propagates
            if camera_span:
                camera_span.finish(
                    status="error",
                    error=exc,
                    output_payload={
                        "artifact": artifact,
                        "direction": "unknown",
                    },
                )
            logger.warning(
                "access_direction_camera_tiebreaker_failed",
                extra={
                    "registration_number": read.registration_number,
                    "person_id": str(person.id),
                    "camera": GATE_CAMERA_IDENTIFIER,
                    "error": str(exc),
                },
            )
            return {
                "camera": GATE_CAMERA_IDENTIFIER,
                "provider": "openai",
                "direction": "unknown",
                "confidence": 0.0,
                "error": str(exc),
                "artifact": artifact,
            }

        direction, confidence, reason = self._parse_camera_direction_analysis(result.text)
        if camera_span:
            camera_span.finish(
                output_payload={
                    "artifact": artifact,
                    "provider": "openai",
                    "model": runtime.openai_model,
                    "direction": direction,
                    "confidence": confidence,
                    "reason": reason,
                    "analysis": result.text[:1000],
                }
            )
        await event_bus.publish(
            "access_event.direction_tiebreaker",
            {
                "registration_number": read.registration_number,
                "person_id": str(person.id),
                "person": person.display_name,
                "camera": GATE_CAMERA_IDENTIFIER,
                "provider": "openai",
                "direction": direction,
                "confidence": confidence,
                "reason": reason,
            },
        )
        return {
            "camera": GATE_CAMERA_IDENTIFIER,
            "provider": "openai",
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "analysis": result.text[:1000],
            "artifact": artifact,
        }

    async def _schedule_evaluation_for_detection(
        self,
        session: AsyncSession,
        *,
        vehicle: Vehicle | None,
        identity_active: bool,
        visitor_pass: VisitorPass | None,
        visitor_pass_mode: str | None,
        captured_at: datetime,
        runtime: RuntimeConfig,
        trace: Any,
    ) -> ScheduleEvaluation | None:
        person = vehicle.owner if vehicle else None
        schedule_span = trace.start_span(
            "Schedule & Access Rule Evaluation",
            attributes={
                "identity_active": identity_active,
                "vehicle_id": str(vehicle.id) if vehicle else None,
                "person_id": str(person.id) if person else None,
                "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
            },
        )
        if vehicle and identity_active:
            schedule_evaluation = await evaluate_vehicle_schedule(
                session,
                vehicle,
                captured_at,
                timezone_name=runtime.site_timezone,
                default_policy=runtime.schedule_default_policy,
            )
        elif visitor_pass:
            schedule_evaluation = ScheduleEvaluation(
                allowed=True,
                source=f"visitor_pass_{visitor_pass_mode or 'match'}",
                reason=f"Visitor pass matched for {visitor_pass.visitor_name}.",
            )
        else:
            schedule_evaluation = None
        schedule_span.finish(
            output_payload=schedule_evaluation_payload(
                schedule_evaluation, missing_reason="No active vehicle identity matched."
            )
        )
        return schedule_evaluation
