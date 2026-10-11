"""Lossless ordered observation tokens with bounded, collision-safe lookup keys."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import LprObservationToken, MovementSessionObservation

TokenKind = Literal["ocr", "protect_event"]


def token_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def exact_token_match(kind: TokenKind, value: str) -> ColumnElement[bool]:
    # The digest only narrows the index scan. Full text is the evidence authority;
    # even a digest collision cannot link different observations or sessions.
    return and_(
        LprObservationToken.kind == kind,
        LprObservationToken.value_digest == token_digest(value),
        LprObservationToken.value == value,
    )


async def record_tokens_in_session(
    session: AsyncSession,
    ingest_id: uuid.UUID,
    *,
    ocr: Sequence[str],
    protect_event: Sequence[str],
) -> None:
    # Callers supply the existing ordered/deduplicated normalized token sequences.
    # Ordinal identity permits different values to coexist even under a collision.
    tokens: tuple[tuple[TokenKind, Sequence[str]], ...] = (
        ("ocr", ocr),
        ("protect_event", protect_event),
    )
    for kind, values in tokens:
        for offset in range(0, len(values), 128):
            await session.execute(
                insert(LprObservationToken),
                [
                    {
                        "ingest_id": ingest_id,
                        "kind": kind,
                        "value": value,
                        "value_digest": token_digest(value),
                        "ordinal": offset + ordinal,
                    }
                    for ordinal, value in enumerate(values[offset : offset + 128])
                ],
            )


async def matching_session_tokens_in_session(
    session: AsyncSession,
    session_ids: Sequence[uuid.UUID],
    *,
    kind: TokenKind,
    values: Sequence[str],
) -> dict[uuid.UUID, set[str]]:
    """Match bounded current evidence against complete indexed session membership.

    Values come from one read or the existing bounded presence tracker. Neither
    window previews nor legacy JSON arrays stand in for durable observations.
    """
    matches: dict[uuid.UUID, set[str]] = {}
    if not session_ids:
        return matches
    tokens = tuple(dict.fromkeys(values))
    for offset in range(0, len(tokens), 128):
        rows = await session.execute(
            select(MovementSessionObservation.session_id, LprObservationToken.value)
            .join(
                LprObservationToken,
                LprObservationToken.ingest_id == MovementSessionObservation.ingest_id,
            )
            .where(
                MovementSessionObservation.session_id.in_(session_ids),
                or_(*(exact_token_match(kind, value) for value in tokens[offset : offset + 128])),
            )
            .distinct()
        )
        for session_id, value in rows:
            matches.setdefault(session_id, set()).add(value)
    return matches
