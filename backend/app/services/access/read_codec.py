"""Canonical durable observation decoding and code-owned classification snapshots."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models import LprIngestEvent
from app.modules.lpr.base import PlateRead
from app.services.access.reads import (
    EXTERNAL_ADMISSION_PAYLOAD_KEY,
    GATE_MALFUNCTION_PAYLOAD_KEY,
    GATE_OBSERVATION_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
    LPR_INGEST_EVENT_PAYLOAD_KEY,
    PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY,
    PROCESSING_ATTEMPT_PAYLOAD_KEY,
    VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY,
    _plate_read_with_payload,
)

CLASSIFICATION_KEYS = (
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
    GATE_MALFUNCTION_PAYLOAD_KEY,
    VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY,
    EXTERNAL_ADMISSION_PAYLOAD_KEY,
)
RESERVED_AUTHORITY_KEYS = frozenset(
    (
        *CLASSIFICATION_KEYS,
        GATE_OBSERVATION_PAYLOAD_KEY,
        LPR_INGEST_EVENT_PAYLOAD_KEY,
        PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY,
        PROCESSING_ATTEMPT_PAYLOAD_KEY,
    )
)


def read_with_ingest_identity(read: PlateRead, row: LprIngestEvent) -> PlateRead:
    return _plate_read_with_payload(
        read,
        {
            **read.raw_payload,
            LPR_INGEST_EVENT_PAYLOAD_KEY: {
                "id": str(row.id),
                "idempotency_key": row.idempotency_key,
                "status": row.status,
            },
        },
    )


def read_from_ingest(row: LprIngestEvent) -> PlateRead:
    """Original raw input stays inspectable; server owners rebuild authority."""
    payload = dict(row.normalized_payload or {})
    original = payload.get("raw_payload")
    raw_payload = (
        {key: value for key, value in original.items() if key not in RESERVED_AUTHORITY_KEYS}
        if isinstance(original, dict)
        else {}
    )
    read = PlateRead(
        registration_number=str(payload.get("registration_number") or row.registration_number),
        confidence=float(payload.get("confidence") or 0.0),
        source=str(payload.get("source") or row.source),
        captured_at=row.captured_at,
        raw_payload={
            **raw_payload,
            GATE_OBSERVATION_PAYLOAD_KEY: dict(row.original_observation or {}),
        },
        candidate_registration_numbers=tuple(
            str(candidate)
            for candidate in payload.get("candidate_registration_numbers", [])
            if str(candidate or "").strip()
        ),
    )
    return read_with_ingest_identity(read, row)


def classification_context(read: PlateRead) -> dict[str, Any]:
    """Only the fenced classification owner writes this immutable member value."""
    return {
        "registration_number": read.registration_number,
        "annotations": {
            key: deepcopy(read.raw_payload[key])
            for key in CLASSIFICATION_KEYS
            if key in read.raw_payload
        },
    }


def read_from_member(row: LprIngestEvent, context: dict[str, Any]) -> PlateRead:
    read = read_from_ingest(row)
    registration = context.get("registration_number")
    annotations = context.get("annotations")
    if not isinstance(registration, str) or not registration:
        raise ValueError("Stored member classification has no registration")
    if not isinstance(annotations, dict) or any(
        key not in CLASSIFICATION_KEYS or not isinstance(value, dict)
        for key, value in annotations.items()
    ):
        raise ValueError("Stored member classification has unsupported annotations")
    return _plate_read_with_payload(
        read, {**read.raw_payload, **annotations}, registration_number=registration
    )
