"""Lossless ordered observation tokens with bounded, collision-safe lookup keys."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import LprObservationToken

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
