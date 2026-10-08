"""Provider-free hydration of an exact canonical consumed visitor arrival.

Lock order is Saga -> Event -> Visitor -> reservation -> visual -> information ->
link checkpoint. Accepted facts survive their provider deadline; current arrival
and field ownership, rather than fresh pass eligibility, govern this projection.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AccessCompletionWork,
    AccessEvent,
    MovementSagaRecord,
    VehicleInformationJob,
    VisitorPass,
    VisitorPassReservationRecord,
)
from app.models.enums import AccessDecision, AccessDirection
from app.services.access.completion_results import compact_text
from app.services.access.completion_store import (
    assert_completion_before_commit,
    assert_completion_in_session,
    finish_completion_in_session,
)
from app.services.access.pipeline_contracts import CompletionClaim
from app.services.access.publication import mark_public_event_changed_in_session
from app.services.movement.sessions import normalize_registration_number
from app.services.visitor_passes import get_visitor_pass_service


def _uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        identity = uuid.UUID(value)
        return identity if str(identity) == value else None
    except ValueError:
        return None


def _saga_id(payload: object) -> uuid.UUID | None:
    if not isinstance(payload, dict):
        return None
    saga = payload.get("movement_saga")
    return _uuid(saga.get("id")) if isinstance(saga, dict) else None


async def _hold(session: AsyncSession, claim: CompletionClaim, reason: str) -> str:
    await finish_completion_in_session(session, claim, state="held", outcome=reason)
    await assert_completion_before_commit(session, claim, finished=True)
    return reason


def _projection(value: object, *, keys: set[str]) -> dict[str, str | None] | None:
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or any(item is not None and not isinstance(item, str) for item in value.values())
    ):
        return None
    return {key: compact_text(item) for key, item in value.items()}


def _field_value(
    state: object, current: str | None, proposed: str | None
) -> tuple[dict[str, Any], str | None] | None:
    if not isinstance(state, dict):
        return None
    status = state.get("state")
    if status == "protected" and set(state) == {"state"}:
        return dict(state), None
    key = "baseline" if status == "never_owned" else "last" if status == "owned" else None
    if key is None or set(state) != {"state", key}:
        return None
    previous = state[key]
    if previous is not None and not isinstance(previous, str):
        return None
    if current != previous:
        return {"state": "protected"}, None
    if proposed is None:
        return dict(state), None
    if len(proposed) > 80:
        return None
    return {"state": "owned", "last": proposed}, proposed


async def apply_visitor_link_in_session(session: AsyncSession, claim: CompletionClaim) -> str:
    """Caller commits only after this participant and any later required writes fence."""
    if claim.stage != "visitor_link":
        raise ValueError("Visitor applicator requires its explicit link stage")
    # Pre-read identity is not authority. Lock the exact canonical saga FIRST,
    # then re-read and prove the immutable selection under the event lock.
    encoded_identity = await session.scalar(
        select(AccessEvent.raw_payload["movement_saga"]["id"].astext).where(
            AccessEvent.id == claim.event_id
        )
    )
    identity = _uuid(encoded_identity)
    saga = (
        await session.get(
            MovementSagaRecord, identity, with_for_update=True, populate_existing=True
        )
        if identity
        else None
    )
    event = await session.get(
        AccessEvent, claim.event_id, with_for_update=True, populate_existing=True
    )
    if event is None:
        await assert_completion_in_session(session, claim)
        return await _hold(session, claim, "canonical_arrival_unavailable")
    identities = list(
        await session.scalars(
            select(MovementSagaRecord.id)
            .where(MovementSagaRecord.access_event_id == event.id)
            .limit(2)
        )
    )
    if (
        saga is None
        or _saga_id(event.raw_payload) != identity
        or identities != [identity]
        or saga.access_event_id != event.id
        or saga.registration_number != event.registration_number
    ):
        return await _hold(session, claim, "canonical_saga_unavailable")
    annotation = (event.raw_payload or {}).get("visitor_pass")
    visitor_id = (
        _uuid(annotation.get("id"))
        if isinstance(annotation, dict) and annotation.get("mode") == "arrival"
        else None
    )
    visitor = (
        await session.get(VisitorPass, visitor_id, with_for_update=True, populate_existing=True)
        if visitor_id
        else None
    )
    reservation = await session.scalar(
        select(VisitorPassReservationRecord)
        .where(VisitorPassReservationRecord.access_event_id == event.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    visual = await session.scalar(
        select(AccessCompletionWork)
        .where(AccessCompletionWork.event_id == event.id, AccessCompletionWork.stage == "visual")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    information = await session.get(
        VehicleInformationJob, event.id, with_for_update=True, populate_existing=True
    )
    work = await assert_completion_in_session(session, claim)
    if (
        visitor is None
        or str(visitor.id) != work.result.get("visitor_id")
        or visitor.arrival_event_id != event.id
        or normalize_registration_number(visitor.number_plate or "")
        != normalize_registration_number(event.registration_number)
        or event.direction != AccessDirection.ENTRY
        or event.decision != AccessDecision.GRANTED
        or saga.direction != event.direction
        or saga.decision != event.decision
        or saga.admission_status != "verified"
        or reservation is None
        or reservation.state != "consumed"
        or reservation.visitor_pass_id != visitor.id
        or reservation.intent_id != uuid.uuid5(event.id, "automatic-gate-open")
        or reservation.normalized_plate != normalize_registration_number(event.registration_number)
        or reservation.pass_type != visitor.pass_type.value
        or not isinstance(reservation.verification_evidence, dict)
        or not isinstance(reservation.verification_evidence.get("state"), str)
        or reservation.verification_evidence.get("state") not in {"open", "opening"}
    ):
        return await _hold(session, claim, "canonical_arrival_unavailable")
    result = deepcopy(work.result)
    requested = result["requested"]
    applied = result.get("applied")
    if not isinstance(applied, dict) or set(applied) - {"visual", "information"}:
        return await _hold(session, claim, "link_receipt_unavailable")
    projections: dict[str, dict[str, str | None]] = {}
    for kind, reference in requested.items():
        if kind == "visual":
            if (
                visual is None
                or visual.state != "succeeded"
                or visual.origin != work.origin
                or visual.origin_at != work.origin_at
                or visual.deadline != work.deadline
                or visual.result.get("accepted") != reference
                or reference["result_id"] != str(visual.id)
                or reference["revision"] != 1
            ):
                return await _hold(session, claim, "accepted_visual_unavailable")
            projection = _projection(visual.result.get("projection"), keys={"colour"})
        else:
            if (
                information is None
                or information.registration_number != event.registration_number
                or information.origin_at != work.origin_at
                or information.deadline != work.deadline
                or information.readiness != "committed_decision"
                or information.accepted_result_revision != reference["revision"]
                or reference["result_id"] != str(event.id)
                or information.accepted_result_at is None
                or information.accepted_result_at.isoformat() != reference["accepted_at"]
            ):
                return await _hold(session, claim, "accepted_information_unavailable")
            projection = _projection(information.accepted_result, keys={"make", "colour"})
        if projection is None:
            return await _hold(session, claim, "accepted_projection_unavailable")
        projections[kind] = projection
    if requested == applied:
        await finish_completion_in_session(
            session, claim, state="succeeded", outcome="already_linked"
        )
        await assert_completion_before_commit(session, claim, finished=True)
        return "already_linked"
    fields = result.get("fields")
    if not isinstance(fields, dict) or set(fields) != {"make", "colour"}:
        return await _hold(session, claim, "frozen_field_baseline_unavailable")
    info = projections.get("information", {})
    proposed = {
        "make": info.get("make"),
        "colour": info.get("colour") or projections.get("visual", {}).get("colour"),
    }
    changes: dict[str, str | None] = {}
    for field in ("make", "colour"):
        current = visitor.vehicle_make if field == "make" else visitor.vehicle_colour
        next_value = _field_value(fields[field], current, proposed[field])
        if next_value is None:
            return await _hold(session, claim, "field_ownership_unavailable")
        fields[field], changes[field] = next_value
    before = (visitor.vehicle_make, visitor.vehicle_colour)
    await get_visitor_pass_service().enrich_arrival(
        session, visitor, event_id=event.id, vehicle_information=changes, visual_detection=None
    )
    result["applied"] = deepcopy(requested)
    work.result = result
    if before != (visitor.vehicle_make, visitor.vehicle_colour):
        await mark_public_event_changed_in_session(session, event.id)
    await finish_completion_in_session(
        session, claim, state="succeeded", outcome="arrival_enriched"
    )
    await assert_completion_before_commit(session, claim, finished=True)
    return "arrival_enriched"
