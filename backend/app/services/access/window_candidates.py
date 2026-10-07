"""Compact candidate display pages; full vendor observations are separate authority."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LprIngestEvent, LprWindowMember
from app.services.movement.sessions import normalize_registration_number

CANDIDATE_PAGE_SIZE = 50
TRACE_TEXT_FIELDS = {
    "request_id": 256,
    "webhook_trace_id": 256,
    "path": 1024,
    "source": 120,
    "registration_number": 64,
    "captured_at": 64,
    "received_at": 64,
    "webhook_received_at": 64,
}
TRACE_NUMBER_FIELDS = ("version", "payload_shape_version", "captured_to_webhook_ms")
KNOWN_TEXT_FIELDS = (
    "detected_registration_number",
    "registration_number",
    "normalized_registration_number",
)


@dataclass(frozen=True)
class WindowCandidate:
    ingest_id: uuid.UUID
    ordinal: int
    registration_number: str
    detected_registration_number: str
    confidence: float
    captured_at: datetime
    candidate_registration_numbers: tuple[str, ...]
    known_vehicle_plate_match: dict[str, Any] | None
    visitor_pass_plate_match: dict[str, Any] | None
    webhook_trace: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "registration_number": self.registration_number,
            "detected_registration_number": self.detected_registration_number,
            "confidence": self.confidence,
            "captured_at": self.captured_at.isoformat(),
            "candidate_registration_numbers": list(self.candidate_registration_numbers),
            "known_vehicle_plate_match": self.known_vehicle_plate_match,
            "visitor_pass_plate_match": self.visitor_pass_plate_match,
            "webhook_trace": self.webhook_trace,
        }


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _text_projection(value: Any, maximum: int) -> Any:
    # Truncate in SQL before transfer/deserialization, and reject object/list
    # values at the display boundary. The immutable raw input is untouched.
    return case(
        (func.jsonb_typeof(value) == "string", func.left(value.astext, maximum)), else_=None
    )


async def candidate_page_in_session(
    session: AsyncSession,
    window_id: uuid.UUID,
    *,
    after_ordinal: int = -1,
    limit: int = CANDIDATE_PAGE_SIZE,
) -> tuple[WindowCandidate, ...]:
    if after_ordinal < -1 or not 1 <= limit <= CANDIDATE_PAGE_SIZE:
        raise ValueError("Candidate page bounds are invalid")
    payload = LprIngestEvent.normalized_payload
    trace = payload["raw_payload"]["webhook_trace"]
    annotations = LprWindowMember.prepared_context["annotations"]
    known = annotations["_iacs_known_vehicle_plate_match"]
    visitor = annotations["_iacs_visitor_pass_plate_match"]
    trace_fields: list[Any] = []
    for key, maximum in TRACE_TEXT_FIELDS.items():
        trace_fields.extend((key, _text_projection(trace[key], maximum)))
    for key in TRACE_NUMBER_FIELDS:
        trace_fields.extend(
            (
                key,
                case(
                    (func.jsonb_typeof(trace[key]) == "number", func.left(trace[key].astext, 40)),
                    else_=None,
                ),
            )
        )
    known_fields: list[Any] = []
    for key in KNOWN_TEXT_FIELDS:
        known_fields.extend((key, _text_projection(known[key], 64)))
    for key in ("similarity", "threshold"):
        known_fields.extend(
            (
                key,
                case(
                    (func.jsonb_typeof(known[key]) == "number", func.left(known[key].astext, 40)),
                    else_=None,
                ),
            )
        )
    known_fields.extend(
        (
            "exact",
            case((func.jsonb_typeof(known["exact"]) == "boolean", known["exact"]), else_=None),
        )
    )
    rows = (
        await session.execute(
            select(
                LprIngestEvent.id,
                LprWindowMember.ordinal,
                LprWindowMember.prepared_context["registration_number"].astext,
                payload["confidence"].astext,
                LprIngestEvent.captured_at,
                LprIngestEvent.source,
                payload["candidate_registration_numbers"],
                case(
                    (func.jsonb_typeof(known) == "object", func.jsonb_build_object(*known_fields)),
                    else_=None,
                ),
                case(
                    (
                        func.jsonb_typeof(visitor) == "object",
                        func.jsonb_build_object(
                            "kind",
                            _text_projection(visitor["kind"], 32),
                            "visitor_pass_id",
                            _text_projection(visitor["visitor_pass_id"], 64),
                            "registration_number",
                            _text_projection(visitor["registration_number"], 64),
                        ),
                    ),
                    else_=None,
                ),
                func.jsonb_build_object(*trace_fields),
                func.jsonb_typeof(trace) == "object",
            )
            .join(LprWindowMember, LprWindowMember.ingest_id == LprIngestEvent.id)
            .where(LprWindowMember.window_id == window_id, LprWindowMember.ordinal > after_ordinal)
            .order_by(LprWindowMember.ordinal)
            .limit(limit)
        )
    ).all()
    results = []
    for (
        identity,
        ordinal,
        registration,
        confidence,
        captured_at,
        source,
        candidates,
        known_match,
        visitor_match,
        trace_values,
        trace_present,
    ) in rows:
        candidate_values = candidates if isinstance(candidates, list) else []
        # Same normalization/order/dedup as the established candidate serializer.
        normalized = tuple(
            dict.fromkeys(
                plate
                for value in [registration, *candidate_values]
                if (plate := normalize_registration_number(str(value or "")))
            )
        )
        known_payload = dict(known_match) if isinstance(known_match, dict) else None
        if known_payload is not None:
            for key in ("similarity", "threshold"):
                known_payload[key] = _number(known_payload.get(key))
        safe_trace = {key: value for key, value in trace_values.items() if value is not None}
        for key in TRACE_NUMBER_FIELDS:
            if key in safe_trace:
                safe_trace[key] = _number(safe_trace[key])
        if not trace_present:
            safe_trace = {
                "source": source,
                "registration_number": registration,
                "captured_at": captured_at.isoformat(),
                "received_at": None,
                "captured_to_webhook_ms": None,
            }
        results.append(
            WindowCandidate(
                identity,
                ordinal,
                registration,
                str(known_payload.get("detected_registration_number") or registration)
                if known_payload
                else registration,
                _number(confidence) or 0.0,
                captured_at,
                normalized,
                known_payload,
                dict(visitor_match) if isinstance(visitor_match, dict) else None,
                safe_trace,
            )
        )
    return tuple(results)
