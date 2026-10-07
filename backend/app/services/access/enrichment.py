from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Anomaly, Person, Vehicle, VisitorPass
from app.models.enums import AccessDecision, AccessDirection
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services.access.execution import AccessExecutionResult
from app.services.access.payloads import (
    access_event_realtime_payload,
)
from app.services.access.reads import VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY
from app.services.access.snapshots import capture_access_event_snapshot
from app.services.event_bus import event_bus
from app.services.leaderboard import get_leaderboard_service
from app.services.lpr_zone_shadow import get_lpr_zone_shadow_service
from app.services.movement.sessions import coerce_gate_state
from app.services.person_presence_input_booleans import apply_person_presence_input_boolean_actions
from app.services.settings import RuntimeConfig
from app.services.snapshots import alert_snapshot_metadata_from_event
from app.services.vehicle_information_jobs import eligible_arrival, enqueue_arrival
from app.services.vehicle_visual_detections import get_vehicle_visual_detection_recorder
from app.services.visitor_passes import get_visitor_pass_service, serialize_visitor_pass

logger = get_logger(__name__)


SNAPSHOT_FIELDS = (
    "snapshot_path",
    "snapshot_content_type",
    "snapshot_bytes",
    "snapshot_width",
    "snapshot_height",
    "snapshot_captured_at",
    "snapshot_camera",
)


async def independently[T](
    stage: str, event_id: Any, operation: Callable[[], Awaitable[T]]
) -> T | None:
    """Optional stages fail independently. Cancellation propagates to the worker."""
    try:
        return await operation()
    except Exception as exc:  # noqa: BLE001 - isolate provider/reporting failure; cancellation propagates
        logger.warning(
            "access_enrichment_failed",
            extra={
                "event_id": str(event_id),
                "stage": stage,
                "exception_class": type(exc).__name__,
            },
        )
        return None


class AccessEnrichment:
    """Optional enrichment and independent reporting after durable access execution."""

    def __init__(self, runtime: RuntimeConfig | None = None):
        self._runtime = runtime
        self._timezone = ZoneInfo(runtime.site_timezone if runtime else settings.site_timezone)

    async def run(self, result: AccessExecutionResult, *, trace: Any) -> None:
        event, person, vehicle = result.event, result.person, result.vehicle
        gate = result.direction_resolution.get("gate_observation") or {}
        gate_state = coerce_gate_state(gate.get("state")) if isinstance(gate, dict) else GateState.UNKNOWN
        if eligible_arrival(event.direction, gate_state):
            await independently("vehicle_information", event.id, lambda: enqueue_arrival(event))
        visual = await independently("visual", event.id, lambda: self._enrich_visual(result, trace))
        await independently("snapshot", event.id, lambda: self.capture_snapshot(event, trace=trace))
        visitor_pass = result.visitor_pass
        if visitor_pass and result.visitor_pass_mode == "arrival":
            enriched = await independently(
                "visitor_vehicle", event.id, lambda: self._enrich_visitor(result, visual)
            )
            visitor_pass = enriched or visitor_pass
        await independently(
            "zone_shadow",
            event.id,
            lambda: self._record_lpr_zone_shadow_decision(
                result.read,
                event=event,
                decision=event.decision,
                direction=event.direction,
                person=person,
                vehicle=vehicle,
                visitor_pass=visitor_pass,
            ),
        )
        if result.presence_updated and person:
            await independently(
                "presence_sync",
                event.id,
                lambda: apply_person_presence_input_boolean_actions(
                    person, event, source="access_event_presence_commit"
                ),
            )
        payload = access_event_realtime_payload(
            event,
            anomaly_count=len(result.anomalies),
            visitor_pass=visitor_pass,
            visitor_pass_mode=result.visitor_pass_mode,
        )
        payload["movement_saga"] = (event.raw_payload or {}).get("movement_saga")
        await independently(
            "access_realtime",
            event.id,
            lambda: event_bus.publish("access_event.finalized", payload),
        )
        if visitor_pass and (result.visitor_pass_mode != "arrival" or
                (result.movement_saga.admission_status == "verified" and visitor_pass.arrival_event_id == event.id)):
            await independently(
                "visitor_realtime",
                event.id,
                lambda: event_bus.publish(
                    "visitor_pass.used"
                    if result.visitor_pass_mode == "arrival"
                    else "visitor_pass.departure_recorded",
                    {
                        "visitor_pass": serialize_visitor_pass(
                            visitor_pass, timezone_name=result.runtime.site_timezone
                        )
                    },
                ),
            )
        if (event.decision == AccessDecision.GRANTED
                and event.direction == AccessDirection.ENTRY
                and event.vehicle_id):
            await independently(
                "leaderboard",
                event.id,
                lambda: get_leaderboard_service().evaluate_known_overtake(event.id),
            )
        external = result.external_admission or {}
        trace.finish(
            status="ok",
            level="warning"
            if result.anomalies or event.decision == AccessDecision.DENIED
            else "info",
            summary=f"{event.decision.value.title()} {event.direction.value} for plate {event.registration_number}",
            access_event_id=event.id,
            context={
                "event_id": str(event.id),
                "decision": event.decision.value,
                "direction": event.direction.value,
                "timing_classification": result.timing.value,
                "anomaly_count": len(result.anomalies),
                "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
                "visitor_name": visitor_pass.visitor_name if visitor_pass else None,
                "external_admission_mode": external.get("mode"),
                "external_admission_source": external.get("source"),
            },
        )

    async def _enrich_visual(
        self, result: AccessExecutionResult, trace: Any
    ) -> dict[str, Any] | None:
        visual = await self._vehicle_visual_detection_for_read(
            result.read, wait_for_match=not bool(result.vehicle), trace=trace
        )
        if visual:
            async with AsyncSessionLocal() as session:
                event = await session.get(AccessEvent, result.event.id, with_for_update=True)
                if event:
                    event.raw_payload = {
                        **(event.raw_payload or {}),
                        VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY: visual,
                    }
                    await session.commit()
                    result.event.raw_payload = event.raw_payload
        return visual

    async def capture_snapshot(self, event: AccessEvent, *, trace: Any = None) -> None:
        # Capture outside a transaction; copy only snapshot-owned columns into a fresh row.
        await capture_access_event_snapshot(event, trace=trace)
        if not event.snapshot_path:
            return
        async with AsyncSessionLocal() as session:
            persisted = await session.get(AccessEvent, event.id, with_for_update=True)
            if persisted:
                for field in SNAPSHOT_FIELDS:
                    setattr(persisted, field, getattr(event, field))
                snapshot = alert_snapshot_metadata_from_event(event)
                if snapshot:
                    for anomaly in (
                        await session.scalars(
                            select(Anomaly).where(Anomaly.event_id == event.id).with_for_update()
                        )
                    ).all():
                        if anomaly.anomaly_type.value == "unauthorized_plate":
                            anomaly.context = {**(anomaly.context or {}), "snapshot": snapshot}
                await session.commit()
                event.raw_payload = persisted.raw_payload

    async def _enrich_visitor(
        self, result: AccessExecutionResult, visual: dict[str, Any] | None
    ) -> VisitorPass | None:
        if result.visitor_pass is None:
            return None
        async with AsyncSessionLocal() as session:
            visitor_pass = await session.get(
                VisitorPass, result.visitor_pass.id, with_for_update=True
            )
            if visitor_pass:
                await get_visitor_pass_service().enrich_arrival(
                    session,
                    visitor_pass,
                    event_id=result.event.id,
                    vehicle_information=None,
                    visual_detection=visual,
                )
                await session.commit()
            return visitor_pass

    async def _record_lpr_zone_shadow_decision(
        self,
        read: PlateRead,
        *,
        event: AccessEvent,
        decision: AccessDecision,
        direction: AccessDirection,
        person: Person | None,
        vehicle: Vehicle | None,
        visitor_pass: VisitorPass | None,
    ) -> None:
        try:
            await get_lpr_zone_shadow_service().record_decision(
                read,
                access_event_id=event.id,
                actual_decision=decision.value,
                actual_direction=direction.value,
                actual_outcome=None,
                person_id=person.id if person else None,
                vehicle_id=vehicle.id if vehicle else None,
                visitor_pass_id=visitor_pass.id if visitor_pass else None,
                mode=getattr(self._runtime, "lpr_zone_filter_mode", "shadow")
                if self._runtime
                else "shadow",
            )
        except Exception as exc:  # noqa: BLE001 - isolate provider/reporting failure; cancellation propagates
            logger.warning(
                "lpr_zone_shadow_record_failed",
                extra={
                    "event_id": str(event.id),
                    "registration_number": read.registration_number,
                    "error": type(exc).__name__,
                },
            )

    async def _vehicle_visual_detection_for_read(
        self,
        read: PlateRead,
        *,
        wait_for_match: bool,
        trace=None,
    ) -> dict[str, Any] | None:
        span = (
            trace.start_span(
                "Vehicle Visual Attribute Match",
                attributes={
                    "registration_number": read.registration_number,
                    "wait_for_match": wait_for_match,
                },
            )
            if trace
            else None
        )
        attempts = 5 if wait_for_match else 1
        recorder = get_vehicle_visual_detection_recorder()
        match: dict[str, Any] | None = None
        for attempt in range(1, attempts + 1):
            match = await recorder.recent_match(
                read.registration_number,
                occurred_at=read.captured_at,
                max_age_seconds=45.0,
            )
            if match:
                break
            if attempt < attempts:
                await asyncio.sleep(0.25)
        if span:
            span.finish(
                output_payload={
                    "matched": bool(match),
                    "observed_vehicle_type": (match or {}).get("observed_vehicle_type"),
                    "observed_vehicle_color": (match or {}).get("observed_vehicle_color"),
                    "source": (match or {}).get("source"),
                    "event_id": (match or {}).get("event_id"),
                }
            )
        return match
