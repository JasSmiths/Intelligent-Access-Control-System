from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    Anomaly,
    AuditLog,
    Person,
    Presence,
    Schedule,
    TelemetrySpan,
    TelemetryTrace,
    Vehicle,
    VehiclePersonAssignment,
)
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalyType,
    PresenceState,
)
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services import access_events as access_events_module
from app.services.access_events import (
    GATE_OBSERVATION_PAYLOAD_KEY,
    PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY,
    AccessEventService,
)
from app.services.event_bus import event_bus
from app.services.telemetry import telemetry


SIMULATION_SOURCE = "simulation_e2e"
SCENARIO_IDS = (
    "known_arrival_closed_gate",
    "known_exit_open_gate",
    "rapid_same_vehicle_reads",
    "multiple_arrivals_under_two_minutes",
    "open_gate_convoy_arrivals",
    "multiple_exits_same_gate_open",
    "unknown_plate_denied",
)

_RUN_LOCK = asyncio.Lock()


class FullAccessFlowRequest(BaseModel):
    cleanup: bool = True
    start_at: datetime | None = None
    scenario_ids: list[str] | None = Field(default=None, min_length=1)
    include_debug: bool = False


class SimulationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "critical"]
    scenario_id: str
    step_id: str
    expected: Any = None
    observed: Any = None
    recommended_fix: str


class SimulationScenarioResult(BaseModel):
    scenario_id: str
    status: Literal["passed", "failed"]
    steps: int
    access_event_ids: list[str]
    suppressed_reads: int
    anomalies: int
    gate_actions: int
    notification_intents: int
    evidence: dict[str, Any]
    issue_codes: list[str]
    debug: dict[str, Any] | None = None


class SimulationSummary(BaseModel):
    scenarios: int
    steps: int
    access_events: int
    suppressed_reads: int
    anomalies: int
    simulated_gate_actions: int
    notification_intents: int
    issues: int


class FullAccessFlowReport(BaseModel):
    run_id: str
    status: Literal["passed", "failed"]
    started_at: datetime
    finished_at: datetime
    summary: SimulationSummary
    issues: list[SimulationIssue]
    scenarios: list[SimulationScenarioResult]


@dataclass(frozen=True)
class SyntheticVehiclePerson:
    key: str
    person_id: uuid.UUID
    vehicle_id: uuid.UUID
    registration_number: str
    display_name: str


@dataclass(frozen=True)
class SyntheticDataSet:
    marker: str
    schedule_id: uuid.UUID
    people: dict[str, SyntheticVehiclePerson]


@dataclass
class SimulationRecorder:
    realtime_events: list[dict[str, Any]] = field(default_factory=list)
    gate_actions: list[dict[str, Any]] = field(default_factory=list)
    garage_actions: list[dict[str, Any]] = field(default_factory=list)
    notification_intents: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "realtime_events": len(self.realtime_events),
            "gate_actions": len(self.gate_actions),
            "garage_actions": len(self.garage_actions),
            "notification_intents": len(self.notification_intents),
            "suppressed_reads": sum(1 for event in self.realtime_events if event["type"] == "plate_read.suppressed"),
        }


class HardwareFreeAccessEventService(AccessEventService):
    def __init__(self, recorder: SimulationRecorder) -> None:
        super().__init__()
        self._recorder = recorder

    async def _capture_event_snapshot(self, event: AccessEvent, *, trace: Any | None = None) -> None:
        return None

    async def _dvla_enrichment_for_event(self, **_kwargs: Any) -> dict[str, str | None] | None:
        return None

    async def _vehicle_visual_detection_for_read(
        self,
        read: PlateRead,
        *,
        wait_for_match: bool,
        trace: Any = None,
    ) -> dict[str, Any] | None:
        return None

    async def _resolve_duplicate_arrival_with_camera(
        self,
        read: PlateRead,
        person: Person,
        *,
        trace: Any | None = None,
    ) -> dict[str, Any]:
        return {
            "camera": "simulation",
            "provider": "simulation",
            "direction": "unknown",
            "confidence": 0.0,
            "reason": "Hardware-free simulation does not call camera vision.",
        }

    async def _open_gate_for_event(
        self,
        event: AccessEvent,
        person: Person | None,
        *,
        open_garage_doors: bool,
        trace: Any | None = None,
        dvla_enrichment: dict[str, str | None] | None = None,
    ) -> bool:
        reason = (
            f"Simulated automatic LPR grant for {event.registration_number}"
            f"{f' ({person.display_name})' if person else ''}"
        )
        action = {
            "action": "gate.open",
            "event_id": str(event.id),
            "registration_number": event.registration_number,
            "accepted": True,
            "state": GateState.OPENING.value,
            "detail": reason,
        }
        self._recorder.gate_actions.append(action)
        await self._audit_automatic_hardware_command(
            action="gate.open.automatic",
            event=event,
            person=person,
            target_entity="Gate",
            target_label="Simulated Gate",
            outcome="accepted",
            level="info",
            metadata={
                "controller": "simulation",
                "reason": reason,
                "accepted": True,
                "state": GateState.OPENING.value,
                "detail": reason,
            },
        )
        await event_bus.publish("gate.open_requested", action)
        if open_garage_doors:
            await self._open_garage_doors_for_event(
                event,
                person,
                reason,
                trace=trace,
                dvla_enrichment=dvla_enrichment,
            )
        return True

    async def _publish_gate_open_skipped(
        self,
        event: AccessEvent,
        direction_resolution: dict[str, Any],
        person: Person | None = None,
    ) -> None:
        self._recorder.gate_actions.append(
            {
                "action": "gate.open",
                "event_id": str(event.id),
                "registration_number": event.registration_number,
                "accepted": False,
                "state": (direction_resolution.get("gate_observation") or {}).get("state") or GateState.UNKNOWN.value,
                "detail": "Skipped because the simulated gate was not closed at plate-read time.",
            }
        )
        await super()._publish_gate_open_skipped(event, direction_resolution, person)

    async def _open_garage_doors_for_event(
        self,
        event: AccessEvent,
        person: Person | None,
        reason: str,
        *,
        trace: Any | None = None,
        dvla_enrichment: dict[str, str | None] | None = None,
    ) -> None:
        if person and person.garage_door_entity_ids:
            self._recorder.garage_actions.append(
                {
                    "action": "garage_door.open",
                    "event_id": str(event.id),
                    "registration_number": event.registration_number,
                    "entity_ids": list(person.garage_door_entity_ids),
                    "detail": reason,
                }
            )

    async def _publish_suppressed_read(self, read: PlateRead, *, reason: str) -> None:
        simulation = _read_simulation_payload(read)
        match = (read.raw_payload or {}).get(access_events_module.KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY) or {}
        await event_bus.publish(
            "plate_read.suppressed",
            {
                "registration_number": read.registration_number,
                "detected_registration_number": match.get("detected_registration_number") or read.registration_number,
                "source": read.source,
                "reason": reason,
                "scenario_id": simulation.get("scenario_id"),
                "step_id": simulation.get("step_id"),
            },
        )


class _FakeNotificationService:
    def __init__(self, recorder: SimulationRecorder) -> None:
        self._recorder = recorder

    async def notify(self, context: Any) -> None:
        self._recorder.notification_intents.append(
            {
                "event_type": context.event_type,
                "subject": context.subject,
                "severity": context.severity,
                "facts": dict(context.facts),
            }
        )


class _FakeLeaderboardService:
    async def evaluate_known_overtake(self, _event_id: uuid.UUID) -> None:
        return None


class HardwareFreePatchScope:
    def __init__(self, recorder: SimulationRecorder) -> None:
        self._recorder = recorder
        self._had_publish_attr = "publish" in event_bus.__dict__
        self._original_publish_attr = event_bus.__dict__.get("publish")
        self._original_notification_service = access_events_module.get_notification_service
        self._original_leaderboard_service = access_events_module.get_leaderboard_service

    async def __aenter__(self) -> "HardwareFreePatchScope":
        async def capture_publish(event_type: str, payload: dict[str, Any]) -> None:
            self._recorder.realtime_events.append(
                {
                    "type": event_type,
                    "payload": payload,
                    "created_at": datetime.now(tz=UTC).isoformat(),
                }
            )

        event_bus.publish = capture_publish  # type: ignore[method-assign]
        access_events_module.get_notification_service = lambda: _FakeNotificationService(self._recorder)  # type: ignore[assignment]
        access_events_module.get_leaderboard_service = lambda: _FakeLeaderboardService()  # type: ignore[assignment]
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        access_events_module.get_leaderboard_service = self._original_leaderboard_service  # type: ignore[assignment]
        access_events_module.get_notification_service = self._original_notification_service  # type: ignore[assignment]
        if self._had_publish_attr:
            event_bus.publish = self._original_publish_attr  # type: ignore[method-assign]
        else:
            delattr(event_bus, "publish")


@dataclass
class SimulationRunContext:
    run_id: str
    marker: str
    start_at: datetime
    include_debug: bool
    data: SyntheticDataSet
    service: HardwareFreeAccessEventService
    recorder: SimulationRecorder
    issues: list[SimulationIssue] = field(default_factory=list)
    steps_run: int = 0

    async def reset_scenario(self, presence_states: dict[str, PresenceState]) -> None:
        self.service._clear_pending_reads()
        async with AsyncSessionLocal() as session:
            for person in self.data.people.values():
                state = presence_states.get(person.key, PresenceState.EXITED)
                presence = await session.get(Presence, person.person_id)
                if not presence:
                    presence = Presence(person_id=person.person_id)
                    session.add(presence)
                presence.state = state
                presence.last_event_id = None
                presence.last_changed_at = self.start_at - timedelta(minutes=1)
            await session.commit()

    async def inject_read(
        self,
        scenario_id: str,
        step_id: str,
        registration_number: str,
        *,
        offset_seconds: int,
        gate_state: str,
        confidence: float = 0.98,
        flush: bool = True,
    ) -> None:
        self.steps_run += 1
        captured_at = self.start_at + timedelta(seconds=offset_seconds)
        raw_payload = {
            "simulation": {
                "run_id": self.run_id,
                "marker": self.marker,
                "scenario_id": scenario_id,
                "step_id": step_id,
            },
            PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY: True,
            GATE_OBSERVATION_PAYLOAD_KEY: {
                "state": gate_state,
                "observed_at": captured_at.isoformat(),
                "controller": "simulation",
                "entity_id": "cover.simulated_top_gate",
            },
            "alarm": {
                "eventPath": f"/protect/events/event/{self.run_id}-{scenario_id}-{step_id}",
                "triggers": [
                    {
                        "eventId": f"{self.run_id}-{scenario_id}-{step_id}",
                        "device": "simulation-camera",
                    }
                ],
            },
        }
        await self.service._handle_queued_read(
            PlateRead(
                registration_number=registration_number,
                confidence=confidence,
                source=SIMULATION_SOURCE,
                captured_at=captured_at,
                raw_payload=raw_payload,
            )
        )
        if flush:
            await self.service._flush_all_pending()

    async def flush(self) -> None:
        await self.service._flush_all_pending()

    async def events_for_scenario(self, scenario_id: str) -> list[AccessEvent]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(AccessEvent)
                    .options(selectinload(AccessEvent.anomalies))
                    .where(AccessEvent.source == SIMULATION_SOURCE)
                    .order_by(AccessEvent.occurred_at.asc(), AccessEvent.id.asc())
                )
            ).all()
        return [
            event
            for event in rows
            if _event_simulation_payload(event).get("run_id") == self.run_id
            and _event_simulation_payload(event).get("scenario_id") == scenario_id
        ]

    async def all_events(self) -> list[AccessEvent]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(AccessEvent)
                    .options(selectinload(AccessEvent.anomalies))
                    .where(AccessEvent.source == SIMULATION_SOURCE)
                    .order_by(AccessEvent.occurred_at.asc(), AccessEvent.id.asc())
                )
            ).all()
        return [event for event in rows if _event_simulation_payload(event).get("run_id") == self.run_id]

    async def presence_for(self, person_key: str) -> PresenceState | None:
        person = self.data.people[person_key]
        async with AsyncSessionLocal() as session:
            presence = await session.get(Presence, person.person_id)
        return presence.state if presence else None

    def add_issue(
        self,
        code: str,
        *,
        severity: Literal["info", "warning", "critical"] = "warning",
        scenario_id: str,
        step_id: str,
        expected: Any,
        observed: Any,
    ) -> None:
        self.issues.append(
            SimulationIssue(
                code=code,
                severity=severity,
                scenario_id=scenario_id,
                step_id=step_id,
                expected=expected,
                observed=observed,
                recommended_fix=recommended_fix_for_issue(code),
            )
        )


ScenarioHandler = Callable[[SimulationRunContext], Awaitable[dict[str, Any]]]


async def run_full_access_flow(request: FullAccessFlowRequest) -> FullAccessFlowReport:
    selected_scenarios = _selected_scenario_ids(request.scenario_ids)
    run_started_at = datetime.now(tz=UTC)
    run_id = uuid.uuid4().hex
    start_at = _normalized_start_at(request.start_at)
    marker = f"IACS-E2E-{run_id}"
    recorder = SimulationRecorder()
    service = HardwareFreeAccessEventService(recorder)
    data: SyntheticDataSet | None = None
    scenario_results: list[SimulationScenarioResult] = []

    async with _RUN_LOCK:
        async with HardwareFreePatchScope(recorder):
            try:
                data = await create_synthetic_data(run_id, marker)
                context = SimulationRunContext(
                    run_id=run_id,
                    marker=marker,
                    start_at=start_at,
                    include_debug=request.include_debug,
                    data=data,
                    service=service,
                    recorder=recorder,
                )
                for scenario_id in selected_scenarios:
                    scenario_results.append(await _run_scenario(context, scenario_id))

                await telemetry.flush()
                all_events = await context.all_events()
                if request.cleanup:
                    try:
                        await cleanup_synthetic_data(data, all_events)
                    except Exception as exc:
                        context.add_issue(
                            "cleanup_failed",
                            severity="critical",
                            scenario_id="cleanup",
                            step_id="cleanup",
                            expected="Synthetic rows are removed without touching unrelated data.",
                            observed=str(exc),
                        )
                issues = context.issues
            except Exception as exc:
                issues = [
                    SimulationIssue(
                        code="scenario_runner_failed",
                        severity="critical",
                        scenario_id="runner",
                        step_id="runner",
                        expected="Full access-flow simulation completes.",
                        observed=str(exc),
                        recommended_fix=recommended_fix_for_issue("scenario_runner_failed"),
                    )
                ]
                all_events = []
                if data and request.cleanup:
                    await cleanup_synthetic_data(data, all_events)

    finished_at = datetime.now(tz=UTC)
    summary = SimulationSummary(
        scenarios=len(scenario_results),
        steps=sum(result.steps for result in scenario_results),
        access_events=len(all_events),
        suppressed_reads=recorder.counts()["suppressed_reads"],
        anomalies=sum(len(event.anomalies) for event in all_events),
        simulated_gate_actions=len(recorder.gate_actions) + len(recorder.garage_actions),
        notification_intents=len(recorder.notification_intents),
        issues=len(issues),
    )
    return FullAccessFlowReport(
        run_id=run_id,
        status="failed" if issues else "passed",
        started_at=run_started_at,
        finished_at=finished_at,
        summary=summary,
        issues=issues,
        scenarios=scenario_results,
    )


def available_scenario_ids() -> tuple[str, ...]:
    return SCENARIO_IDS


def recommended_fix_for_issue(code: str) -> str:
    recommendations = {
        "wrong_direction": (
            "Update AccessEventService._resolve_direction so a known vehicle whose person is not present can "
            "resolve as an entry during an active open-gate convoy, instead of treating every open/opening/"
            "closing gate read as an exit."
        ),
        "wrong_decision": "Review schedule/identity evaluation for the scenario and ensure granted known vehicles stay allowed.",
        "missing_event": "Check LPR debounce/session suppression for this scenario; an expected finalized access event was not created.",
        "unexpected_event_count": "Review debounce grouping and exact-plate/session suppression boundaries for this scenario.",
        "presence_mismatch": "Review direction classification and presence update ordering for granted events.",
        "missing_gate_action": "Review automatic gate-open eligibility and simulated gate action recording for granted closed-gate entries.",
        "unexpected_gate_action": "Review automatic gate-open eligibility; exits and already-open gate reads should not command the gate.",
        "missing_anomaly": "Review anomaly creation for denied unknown plates and duplicate movement states.",
        "unexpected_anomaly": "Review direction classification and duplicate-entry/duplicate-exit anomaly conditions.",
        "suppressed_read_mismatch": "Review exact-known-plate debounce and active vehicle-session suppression behavior.",
        "cleanup_failed": "Inspect the cleanup query guardrails and manually remove rows carrying the IACS-E2E marker if needed.",
        "scenario_runner_failed": "Inspect backend logs for the simulation runner exception and add a focused regression test.",
    }
    return recommendations.get(code, "Inspect the scenario evidence and add a focused regression test before changing production logic.")


async def create_synthetic_data(run_id: str, marker: str) -> SyntheticDataSet:
    token = run_id[:4].upper()
    schedule = Schedule(
        name=f"{marker} Always Access",
        description=marker,
        time_blocks={str(day): [{"start": "00:00", "end": "24:00"}] for day in range(7)},
    )
    people_specs = {
        "arrival": ("Ada", "Arrival", f"AA{token}11"),
        "exit": ("Eli", "Exit", f"BC{token}22"),
        "rapid": ("Rae", "Rapid", f"DX{token}33"),
        "multi_a": ("Mia", "Multi", f"EL{token}44"),
        "multi_b": ("Mo", "Multi", f"FM{token}55"),
        "convoy_a": ("Cora", "Convoy", f"GP{token}66"),
        "convoy_b": ("Cian", "Convoy", f"HZ{token}77"),
        "convoy_c": ("Cleo", "Convoy", f"JQ{token}88"),
    }

    async with AsyncSessionLocal() as session:
        session.add(schedule)
        await session.flush()
        people: dict[str, SyntheticVehiclePerson] = {}
        for key, (first_name, last_name, plate) in people_specs.items():
            person = Person(
                first_name=f"{first_name} {token}",
                last_name=last_name,
                display_name=f"{first_name} {last_name} {token}",
                schedule_id=schedule.id,
                notes=marker,
                is_active=True,
                garage_door_entity_ids=[],
            )
            session.add(person)
            await session.flush()
            vehicle = Vehicle(
                person_id=person.id,
                schedule_id=schedule.id,
                registration_number=plate,
                make="Simulation",
                model=key,
                color="Blue",
                description=marker,
                is_active=True,
            )
            session.add(vehicle)
            await session.flush()
            session.add(VehiclePersonAssignment(vehicle_id=vehicle.id, person_id=person.id))
            people[key] = SyntheticVehiclePerson(
                key=key,
                person_id=person.id,
                vehicle_id=vehicle.id,
                registration_number=plate,
                display_name=person.display_name,
            )
        await session.commit()
    return SyntheticDataSet(marker=marker, schedule_id=schedule.id, people=people)


async def cleanup_synthetic_data(data: SyntheticDataSet, events: Sequence[AccessEvent]) -> None:
    person_ids = [person.person_id for person in data.people.values()]
    vehicle_ids = [person.vehicle_id for person in data.people.values()]

    async with AsyncSessionLocal() as session:
        selected_events = list(events)
        if not selected_events:
            rows = (
                await session.scalars(
                    select(AccessEvent)
                    .options(selectinload(AccessEvent.anomalies))
                    .where(AccessEvent.source == SIMULATION_SOURCE)
                    .order_by(AccessEvent.occurred_at.asc(), AccessEvent.id.asc())
                )
            ).all()
            selected_events = [
                event for event in rows if _event_simulation_payload(event).get("marker") == data.marker
            ]
        event_ids = [event.id for event in selected_events]
        trace_ids = [
            str(((event.raw_payload or {}).get("telemetry") or {}).get("trace_id"))
            for event in selected_events
            if isinstance(event.raw_payload, dict)
            and isinstance((event.raw_payload or {}).get("telemetry"), dict)
            and ((event.raw_payload or {}).get("telemetry") or {}).get("trace_id")
        ]
        audit_rows = (
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.action.in_(["gate.open.automatic", "garage_door.open.automatic"]),
                )
            )
        ).all()
        for row in audit_rows:
            metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
            if metadata.get("access_event_id") in {str(event_id) for event_id in event_ids}:
                await session.delete(row)

        if person_ids:
            await session.execute(delete(Presence).where(Presence.person_id.in_(person_ids)))
        if event_ids:
            await session.execute(delete(Anomaly).where(Anomaly.event_id.in_(event_ids)))
        if trace_ids:
            await session.execute(delete(TelemetrySpan).where(TelemetrySpan.trace_id.in_(trace_ids)))
            await session.execute(delete(TelemetryTrace).where(TelemetryTrace.trace_id.in_(trace_ids)))
        if event_ids:
            await session.execute(delete(AccessEvent).where(AccessEvent.id.in_(event_ids)))
        if vehicle_ids:
            await session.execute(
                delete(VehiclePersonAssignment).where(VehiclePersonAssignment.vehicle_id.in_(vehicle_ids))
            )
            await session.execute(
                delete(Vehicle).where(Vehicle.id.in_(vehicle_ids), Vehicle.description == data.marker)
            )
        if person_ids:
            await session.execute(delete(Person).where(Person.id.in_(person_ids), Person.notes == data.marker))
        await session.execute(delete(Schedule).where(Schedule.id == data.schedule_id, Schedule.description == data.marker))
        await session.commit()


async def _run_scenario(context: SimulationRunContext, scenario_id: str) -> SimulationScenarioResult:
    handler = _scenario_handlers()[scenario_id]
    before = context.recorder.counts()
    issues_before = len(context.issues)
    steps_before = context.steps_run
    evidence = await handler(context)
    await context.flush()
    events = await context.events_for_scenario(scenario_id)
    issues = [issue for issue in context.issues[issues_before:] if issue.scenario_id == scenario_id]
    after = context.recorder.counts()
    return SimulationScenarioResult(
        scenario_id=scenario_id,
        status="failed" if issues else "passed",
        steps=context.steps_run - steps_before,
        access_event_ids=[str(event.id) for event in events],
        suppressed_reads=after["suppressed_reads"] - before["suppressed_reads"],
        anomalies=sum(len(event.anomalies) for event in events),
        gate_actions=after["gate_actions"] - before["gate_actions"],
        notification_intents=after["notification_intents"] - before["notification_intents"],
        evidence=evidence,
        issue_codes=[issue.code for issue in issues],
        debug=_scenario_debug(context, events) if context.include_debug else None,
    )


def _scenario_handlers() -> dict[str, ScenarioHandler]:
    return {
        "known_arrival_closed_gate": _scenario_known_arrival_closed_gate,
        "known_exit_open_gate": _scenario_known_exit_open_gate,
        "rapid_same_vehicle_reads": _scenario_rapid_same_vehicle_reads,
        "multiple_arrivals_under_two_minutes": _scenario_multiple_arrivals_under_two_minutes,
        "open_gate_convoy_arrivals": _scenario_open_gate_convoy_arrivals,
        "multiple_exits_same_gate_open": _scenario_multiple_exits_same_gate_open,
        "unknown_plate_denied": _scenario_unknown_plate_denied,
    }


async def _scenario_known_arrival_closed_gate(context: SimulationRunContext) -> dict[str, Any]:
    scenario_id = "known_arrival_closed_gate"
    person = context.data.people["arrival"]
    await context.reset_scenario({"arrival": PresenceState.EXITED})
    await context.inject_read(scenario_id, "arrival", person.registration_number, offset_seconds=0, gate_state="closed")
    events = await context.events_for_scenario(scenario_id)
    event = _single_event_or_issue(context, scenario_id, "arrival", events, person.registration_number)
    if event:
        _expect_event(context, scenario_id, "arrival", event, AccessDirection.ENTRY, AccessDecision.GRANTED)
        _expect_anomalies(context, scenario_id, "arrival", event, [])
    await _expect_presence(context, scenario_id, "arrival", "arrival", PresenceState.PRESENT)
    _expect_gate_action_count(context, scenario_id, "arrival", person.registration_number, expected=1)
    return {"expected": "Known closed-gate read creates one granted entry and opens the gate."}


async def _scenario_known_exit_open_gate(context: SimulationRunContext) -> dict[str, Any]:
    scenario_id = "known_exit_open_gate"
    person = context.data.people["exit"]
    await context.reset_scenario({"exit": PresenceState.PRESENT})
    await context.inject_read(scenario_id, "exit", person.registration_number, offset_seconds=300, gate_state="open")
    events = await context.events_for_scenario(scenario_id)
    event = _single_event_or_issue(context, scenario_id, "exit", events, person.registration_number)
    if event:
        _expect_event(context, scenario_id, "exit", event, AccessDirection.EXIT, AccessDecision.GRANTED)
        _expect_anomalies(context, scenario_id, "exit", event, [])
    await _expect_presence(context, scenario_id, "exit", "exit", PresenceState.EXITED)
    _expect_gate_action_count(context, scenario_id, "exit", person.registration_number, expected=0)
    return {"expected": "Known open-gate read for a present person creates one granted exit."}


async def _scenario_rapid_same_vehicle_reads(context: SimulationRunContext) -> dict[str, Any]:
    scenario_id = "rapid_same_vehicle_reads"
    person = context.data.people["rapid"]
    await context.reset_scenario({"rapid": PresenceState.EXITED})
    near_read = f"{person.registration_number[:-1]}8"
    await context.inject_read(
        scenario_id,
        "near-read",
        near_read,
        offset_seconds=600,
        gate_state="closed",
        confidence=0.64,
        flush=False,
    )
    await context.inject_read(
        scenario_id,
        "exact-read",
        person.registration_number,
        offset_seconds=601,
        gate_state="closed",
        confidence=0.98,
        flush=False,
    )
    await context.inject_read(
        scenario_id,
        "trailing-read",
        person.registration_number,
        offset_seconds=602,
        gate_state="closed",
        confidence=0.97,
    )
    events = await context.events_for_scenario(scenario_id)
    event = _single_event_or_issue(context, scenario_id, "exact-read", events, person.registration_number)
    if event:
        _expect_event(context, scenario_id, "exact-read", event, AccessDirection.ENTRY, AccessDecision.GRANTED)
        candidate_count = ((event.raw_payload or {}).get("debounce") or {}).get("candidate_count")
        if candidate_count != 2:
            context.add_issue(
                "unexpected_event_count",
                scenario_id=scenario_id,
                step_id="exact-read",
                expected={"candidate_count": 2},
                observed={"candidate_count": candidate_count},
            )
    suppressed = _suppressed_reads(context, scenario_id)
    if len(suppressed) < 1:
        context.add_issue(
            "suppressed_read_mismatch",
            scenario_id=scenario_id,
            step_id="trailing-read",
            expected="At least one trailing duplicate read is suppressed.",
            observed=suppressed,
        )
    return {"expected": "Rapid near/exact/trailing reads create one event and suppress trailing noise."}


async def _scenario_multiple_arrivals_under_two_minutes(context: SimulationRunContext) -> dict[str, Any]:
    scenario_id = "multiple_arrivals_under_two_minutes"
    people = [context.data.people["multi_a"], context.data.people["multi_b"]]
    await context.reset_scenario({"multi_a": PresenceState.EXITED, "multi_b": PresenceState.EXITED})
    for index, person in enumerate(people):
        await context.inject_read(
            scenario_id,
            f"arrival-{index + 1}",
            person.registration_number,
            offset_seconds=1200 + index * 75,
            gate_state="closed",
        )
    events = await context.events_for_scenario(scenario_id)
    _expect_event_count(context, scenario_id, "arrivals", events, expected=2)
    for index, person in enumerate(people):
        event = _event_for_registration(events, person.registration_number)
        if event:
            _expect_event(context, scenario_id, f"arrival-{index + 1}", event, AccessDirection.ENTRY, AccessDecision.GRANTED)
            _expect_anomalies(context, scenario_id, f"arrival-{index + 1}", event, [])
        await _expect_presence(context, scenario_id, f"arrival-{index + 1}", person.key, PresenceState.PRESENT)
    return {"expected": "Two different known vehicles under two minutes apart create separate entries."}


async def _scenario_open_gate_convoy_arrivals(context: SimulationRunContext) -> dict[str, Any]:
    scenario_id = "open_gate_convoy_arrivals"
    people = [context.data.people["convoy_a"], context.data.people["convoy_b"], context.data.people["convoy_c"]]
    await context.reset_scenario({person.key: PresenceState.EXITED for person in people})
    await context.inject_read(scenario_id, "lead-arrival", people[0].registration_number, offset_seconds=1800, gate_state="closed")
    for index, person in enumerate(people[1:], start=2):
        await context.inject_read(
            scenario_id,
            f"convoy-arrival-{index}",
            person.registration_number,
            offset_seconds=1800 + index * 20,
            gate_state="open",
        )
    events = await context.events_for_scenario(scenario_id)
    _expect_event_count(context, scenario_id, "convoy", events, expected=3)
    for index, person in enumerate(people):
        step_id = "lead-arrival" if index == 0 else f"convoy-arrival-{index + 1}"
        event = _event_for_registration(events, person.registration_number)
        if event:
            _expect_event(context, scenario_id, step_id, event, AccessDirection.ENTRY, AccessDecision.GRANTED)
            _expect_anomalies(context, scenario_id, step_id, event, [])
        await _expect_presence(context, scenario_id, step_id, person.key, PresenceState.PRESENT)
    _expect_gate_action_count(context, scenario_id, "lead-arrival", people[0].registration_number, expected=1)
    return {
        "expected": (
            "The lead vehicle opens the gate; following not-present known vehicles read while open are still entries."
        )
    }


async def _scenario_multiple_exits_same_gate_open(context: SimulationRunContext) -> dict[str, Any]:
    scenario_id = "multiple_exits_same_gate_open"
    people = [context.data.people["multi_a"], context.data.people["multi_b"]]
    await context.reset_scenario({"multi_a": PresenceState.PRESENT, "multi_b": PresenceState.PRESENT})
    for index, person in enumerate(people):
        await context.inject_read(
            scenario_id,
            f"exit-{index + 1}",
            person.registration_number,
            offset_seconds=2700 + index * 20,
            gate_state="open",
        )
    events = await context.events_for_scenario(scenario_id)
    _expect_event_count(context, scenario_id, "exits", events, expected=2)
    for index, person in enumerate(people):
        event = _event_for_registration(events, person.registration_number)
        if event:
            _expect_event(context, scenario_id, f"exit-{index + 1}", event, AccessDirection.EXIT, AccessDecision.GRANTED)
            _expect_anomalies(context, scenario_id, f"exit-{index + 1}", event, [])
        await _expect_presence(context, scenario_id, f"exit-{index + 1}", person.key, PresenceState.EXITED)
    return {"expected": "Multiple present people can exit during the same open-gate period without duplicate-exit anomalies."}


async def _scenario_unknown_plate_denied(context: SimulationRunContext) -> dict[str, Any]:
    scenario_id = "unknown_plate_denied"
    unknown_plate = f"UNK{context.run_id[:4].upper()}99"
    await context.reset_scenario({})
    await context.inject_read(scenario_id, "unknown", unknown_plate, offset_seconds=3600, gate_state="closed")
    events = await context.events_for_scenario(scenario_id)
    event = _single_event_or_issue(context, scenario_id, "unknown", events, unknown_plate)
    if event:
        _expect_event(context, scenario_id, "unknown", event, AccessDirection.DENIED, AccessDecision.DENIED)
        _expect_anomalies(context, scenario_id, "unknown", event, [AnomalyType.UNAUTHORIZED_PLATE])
    return {"expected": "Unknown closed-gate read is denied and creates an unauthorized-plate anomaly."}


def _selected_scenario_ids(requested: list[str] | None) -> list[str]:
    if not requested:
        return list(SCENARIO_IDS)
    unknown = sorted(set(requested) - set(SCENARIO_IDS))
    if unknown:
        raise ValueError(f"Unknown simulation scenario id: {', '.join(unknown)}")
    return list(dict.fromkeys(requested))


def _normalized_start_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _read_simulation_payload(read: PlateRead) -> dict[str, Any]:
    simulation = (read.raw_payload or {}).get("simulation")
    return simulation if isinstance(simulation, dict) else {}


def _event_simulation_payload(event: AccessEvent) -> dict[str, Any]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    best = raw_payload.get("best")
    best_payload = best if isinstance(best, dict) else {}
    simulation = best_payload.get("simulation")
    return simulation if isinstance(simulation, dict) else {}


def _event_for_registration(events: Sequence[AccessEvent], registration_number: str) -> AccessEvent | None:
    return next((event for event in events if event.registration_number == registration_number), None)


def _single_event_or_issue(
    context: SimulationRunContext,
    scenario_id: str,
    step_id: str,
    events: Sequence[AccessEvent],
    registration_number: str,
) -> AccessEvent | None:
    event = _event_for_registration(events, registration_number)
    if event:
        return event
    context.add_issue(
        "missing_event",
        scenario_id=scenario_id,
        step_id=step_id,
        expected=f"One access event for {registration_number}.",
        observed=[event.registration_number for event in events],
    )
    return None


def _expect_event_count(
    context: SimulationRunContext,
    scenario_id: str,
    step_id: str,
    events: Sequence[AccessEvent],
    *,
    expected: int,
) -> None:
    if len(events) != expected:
        context.add_issue(
            "unexpected_event_count",
            scenario_id=scenario_id,
            step_id=step_id,
            expected=expected,
            observed=len(events),
        )


def _expect_event(
    context: SimulationRunContext,
    scenario_id: str,
    step_id: str,
    event: AccessEvent,
    direction: AccessDirection,
    decision: AccessDecision,
) -> None:
    if event.direction != direction:
        context.add_issue(
            "wrong_direction",
            scenario_id=scenario_id,
            step_id=step_id,
            expected=direction.value,
            observed=event.direction.value,
        )
    if event.decision != decision:
        context.add_issue(
            "wrong_decision",
            scenario_id=scenario_id,
            step_id=step_id,
            expected=decision.value,
            observed=event.decision.value,
        )


async def _expect_presence(
    context: SimulationRunContext,
    scenario_id: str,
    step_id: str,
    person_key: str,
    state: PresenceState,
) -> None:
    observed = await context.presence_for(person_key)
    if observed != state:
        context.add_issue(
            "presence_mismatch",
            scenario_id=scenario_id,
            step_id=step_id,
            expected=state.value,
            observed=observed.value if observed else None,
        )


def _expect_anomalies(
    context: SimulationRunContext,
    scenario_id: str,
    step_id: str,
    event: AccessEvent,
    expected: Sequence[AnomalyType],
) -> None:
    observed = [anomaly.anomaly_type for anomaly in event.anomalies]
    if sorted(item.value for item in observed) == sorted(item.value for item in expected):
        return
    code = "missing_anomaly" if expected else "unexpected_anomaly"
    context.add_issue(
        code,
        scenario_id=scenario_id,
        step_id=step_id,
        expected=[item.value for item in expected],
        observed=[item.value for item in observed],
    )


def _expect_gate_action_count(
    context: SimulationRunContext,
    scenario_id: str,
    step_id: str,
    registration_number: str,
    *,
    expected: int,
) -> None:
    observed = [
        action
        for action in context.recorder.gate_actions
        if action.get("registration_number") == registration_number
    ]
    if len(observed) == expected:
        return
    context.add_issue(
        "missing_gate_action" if len(observed) < expected else "unexpected_gate_action",
        scenario_id=scenario_id,
        step_id=step_id,
        expected=expected,
        observed=len(observed),
    )


def _suppressed_reads(context: SimulationRunContext, scenario_id: str) -> list[dict[str, Any]]:
    return [
        event["payload"]
        for event in context.recorder.realtime_events
        if event["type"] == "plate_read.suppressed" and event["payload"].get("scenario_id") == scenario_id
    ]


def _scenario_debug(context: SimulationRunContext, events: Sequence[AccessEvent]) -> dict[str, Any]:
    return {
        "events": [_event_debug(event) for event in events],
        "gate_actions": list(context.recorder.gate_actions),
        "notification_intents": list(context.recorder.notification_intents),
        "suppressed_reads": [
            event for event in context.recorder.realtime_events if event["type"] == "plate_read.suppressed"
        ],
    }


def _event_debug(event: AccessEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "occurred_at": event.occurred_at.isoformat(),
        "anomalies": [anomaly.anomaly_type.value for anomaly in event.anomalies],
        "direction_resolution": (event.raw_payload or {}).get("direction_resolution")
        if isinstance(event.raw_payload, dict)
        else None,
    }
