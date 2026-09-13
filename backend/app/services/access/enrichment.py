from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Anomaly, Person, Vehicle, VisitorPass
from app.models.enums import AccessDecision, AccessDirection, AnomalySeverity
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.modules.notifications.base import NotificationContext
from app.services.access.execution import AccessExecutionResult
from app.services.access.payloads import (
    access_event_realtime_payload,
    notification_facts,
)
from app.services.access.reads import VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY
from app.services.access.snapshots import capture_access_event_snapshot
from app.services.dvla import NormalizedDvlaVehicle, lookup_normalized_vehicle_registration
from app.services.event_bus import event_bus
from app.services.leaderboard import get_leaderboard_service
from app.services.lpr_zone_shadow import get_lpr_zone_shadow_service
from app.services.movement.sessions import ARRIVAL_GATE_STATES, coerce_gate_state
from app.services.notifications import get_notification_service
from app.services.person_presence_input_booleans import apply_person_presence_input_boolean_actions
from app.services.settings import RuntimeConfig
from app.services.snapshots import alert_snapshot_metadata_from_event
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS
from app.services.vehicle_visual_detections import get_vehicle_visual_detection_recorder
from app.services.visitor_passes import get_visitor_pass_service, serialize_visitor_pass

logger = get_logger(__name__)


def dvla_mot_alert_required(mot_status: str | None) -> bool:
    normalized = (mot_status or "").strip().casefold().replace("_", " ")
    return bool(normalized and normalized not in {"valid", "not required"})


def dvla_tax_alert_required(tax_status: str | None) -> bool:
    return bool(tax_status and tax_status.strip().casefold() not in {"taxed", "sorn"})


DVLA_FIELDS = (
    "make",
    "color",
    "fuel_type",
    "mot_status",
    "tax_status",
    "mot_expiry",
    "tax_expiry",
    "last_dvla_lookup_date",
)
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
        dvla = await independently("dvla", event.id, lambda: self._enrich_dvla(result, trace))
        visual = await independently("visual", event.id, lambda: self._enrich_visual(result, trace))
        await independently("snapshot", event.id, lambda: self.capture_snapshot(event, trace=trace))
        visitor_pass = result.visitor_pass
        if visitor_pass and result.visitor_pass_mode == "arrival":
            enriched = await independently(
                "visitor_vehicle", event.id, lambda: self._enrich_visitor(result, dvla, visual)
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
        if dvla:
            await independently(
                "compliance",
                event.id,
                lambda: self._notify_compliance_issues(event, person, vehicle, dvla),
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

    async def _enrich_dvla(
        self, result: AccessExecutionResult, trace: Any
    ) -> dict[str, Any] | None:
        vehicle = result.vehicle
        before = {field: getattr(vehicle, field) for field in DVLA_FIELDS} if vehicle else {}
        payload = await self._dvla_enrichment_for_event(
            vehicle=vehicle,
            registration_number=result.event.registration_number,
            direction=result.event.direction,
            direction_resolution=result.direction_resolution,
            runtime=result.runtime,
            trace=trace,
        )
        changes = {
            field: getattr(vehicle, field)
            for field in before
            if before[field] != getattr(vehicle, field)
        }
        if vehicle and changes:
            async with AsyncSessionLocal() as session:
                persisted = await session.get(Vehicle, vehicle.id, with_for_update=True)
                if persisted and (
                    not persisted.last_dvla_lookup_date
                    or persisted.last_dvla_lookup_date <= vehicle.last_dvla_lookup_date
                ):
                    for field, value in changes.items():
                        setattr(persisted, field, value)
                    await session.commit()
        return payload

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
        self, result: AccessExecutionResult, dvla, visual
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
                    dvla_enrichment=dvla,
                    visual_detection=visual,
                )
                await session.commit()
            return visitor_pass

    def _apply_dvla_enrichment(
        self,
        vehicle: Vehicle,
        normalized: NormalizedDvlaVehicle,
        lookup_date: date,
    ) -> None:
        if normalized.make:
            vehicle.make = normalized.make
        if normalized.colour:
            vehicle.color = normalized.colour
        vehicle.fuel_type = normalized.fuel_type
        vehicle.mot_status = normalized.mot_status
        vehicle.tax_status = normalized.tax_status
        vehicle.mot_expiry = normalized.mot_expiry
        vehicle.tax_expiry = normalized.tax_expiry
        vehicle.last_dvla_lookup_date = lookup_date

    def _dvla_cache_date(self, timezone_name: str) -> date:
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception:  # noqa: BLE001 - preserve configured timezone fallback
            timezone = self._timezone
        return datetime.now(tz=timezone).date()

    async def _dvla_enrichment_for_event(
        self,
        *,
        vehicle: Vehicle | None,
        registration_number: str,
        direction: AccessDirection,
        direction_resolution: dict[str, Any],
        runtime: RuntimeConfig,
        trace: Any | None = None,
    ) -> dict[str, str | None] | None:
        if not self._should_run_dvla_enrichment(direction, direction_resolution):
            return None

        today = self._dvla_cache_date(runtime.site_timezone)
        span = (
            trace.start_span(
                "DVLA Vehicle Enrichment",
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                attributes={
                    "registration_number": registration_number,
                    "known_vehicle": bool(vehicle),
                    "vehicle_id": str(vehicle.id) if vehicle else None,
                    "last_lookup_date": (
                        vehicle.last_dvla_lookup_date.isoformat()
                        if vehicle and vehicle.last_dvla_lookup_date
                        else None
                    ),
                    "cache_date": today.isoformat(),
                },
            )
            if trace
            else None
        )

        if vehicle and vehicle.last_dvla_lookup_date == today:
            payload = self._vehicle_dvla_payload(vehicle)
            if span:
                span.finish(status="ok", output_payload={"status": "cached"})
            return payload

        try:
            normalized = await lookup_normalized_vehicle_registration(
                registration_number, today=today
            )
        except DvlaVehicleEnquiryError as exc:
            detail = self._sanitize_dvla_error(exc)
            if span:
                span.finish(status="error", output_payload={"status": "failed"}, error=detail)
            await self._publish_dvla_enrichment_failure(
                registration_number, exc.status_code, detail
            )
            return None
        except Exception as exc:  # noqa: BLE001 - isolate provider/reporting failure; cancellation propagates
            detail = self._sanitize_dvla_error(exc)
            if span:
                span.finish(status="error", output_payload={"status": "failed"}, error=detail)
            await self._publish_dvla_enrichment_failure(registration_number, None, detail)
            return None

        if vehicle:
            self._apply_dvla_enrichment(vehicle, normalized, today)
            payload = self._vehicle_dvla_payload(vehicle)
            status = "refreshed"
        else:
            payload = normalized.as_payload()
            status = "ephemeral"

        if span:
            span.finish(status="ok", output_payload={"status": status})
        return payload

    async def _notify_compliance_issues(
        self,
        event: AccessEvent,
        person: Person | None,
        vehicle: Vehicle | None,
        dvla_enrichment: dict[str, str | None],
    ) -> None:
        mot_status = dvla_enrichment.get("mot_status")
        tax_status = dvla_enrichment.get("tax_status")
        notifications = []
        if dvla_mot_alert_required(mot_status):
            notifications.append(
                NotificationContext(
                    event_type="expired_mot_detected",
                    subject=f"Expired MOT detected for {event.registration_number}",
                    severity=AnomalySeverity.WARNING.value,
                    facts=notification_facts(
                        event,
                        person,
                        vehicle,
                        f"DVLA reports MOT status {mot_status} for {event.registration_number}.",
                        dvla_enrichment=dvla_enrichment,
                    ),
                )
            )
        if dvla_tax_alert_required(tax_status):
            notifications.append(
                NotificationContext(
                    event_type="expired_tax_detected",
                    subject=f"Expired tax detected for {event.registration_number}",
                    severity=AnomalySeverity.WARNING.value,
                    facts=notification_facts(
                        event,
                        person,
                        vehicle,
                        f"DVLA reports tax status {tax_status} for {event.registration_number}.",
                        dvla_enrichment=dvla_enrichment,
                    ),
                )
            )

        for context in notifications:
            await get_notification_service().notify(context)

    async def _publish_dvla_enrichment_failure(
        self,
        registration_number: str,
        status_code: int | None,
        detail: str,
    ) -> None:
        logger.warning(
            "lpr_dvla_enrichment_failed",
            extra={
                "registration_number": registration_number,
                "status_code": status_code,
                "error": detail,
            },
        )
        await event_bus.publish(
            "dvla.enrichment_failed",
            {
                "registration_number": registration_number,
                "status_code": status_code,
                "error": detail,
            },
        )

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

    def _sanitize_dvla_error(self, exc: Exception) -> str:
        detail = str(exc).replace("\n", " ").strip()
        return detail or exc.__class__.__name__

    def _should_run_dvla_enrichment(
        self,
        direction: AccessDirection,
        direction_resolution: dict[str, Any],
    ) -> bool:
        if direction == AccessDirection.EXIT:
            return False
        if direction == AccessDirection.ENTRY:
            return True

        gate_observation = direction_resolution.get("gate_observation") or {}
        gate_state = (
            coerce_gate_state(gate_observation.get("state"))
            if isinstance(gate_observation, dict)
            else GateState.UNKNOWN
        )
        return gate_state in ARRIVAL_GATE_STATES

    def _vehicle_dvla_payload(self, vehicle: Vehicle) -> dict[str, str | None]:
        return {
            "registration_number": vehicle.registration_number,
            "make": vehicle.make,
            "colour": vehicle.color,
            "fuel_type": getattr(vehicle, "fuel_type", None),
            "mot_status": vehicle.mot_status,
            "tax_status": vehicle.tax_status,
            "mot_expiry": vehicle.mot_expiry.isoformat() if vehicle.mot_expiry else None,
            "tax_expiry": vehicle.tax_expiry.isoformat() if vehicle.tax_expiry else None,
        }

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
