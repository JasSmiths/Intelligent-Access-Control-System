"""Postgres JSON-backed memory for Alfred v3."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select

from app.ai.providers import ChatMessageInput
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AlfredMemory
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, emit_audit_log

logger = get_logger(__name__)

MEMORY_EXTRACTION_PROMPT = """Extract durable Alfred memory from the completed IACS chat turn.
Only keep stable preferences, useful context, or operator instructions that will help future IACS conversations.
Do not store secrets, passwords, API keys, tokens, plates from one-off visitor messages, raw tool JSON, transient diagnostics, or anything about visitors.
Admins may create site memory when the user explicitly says it should apply to the whole site. Otherwise use user scope.
Return compact JSON: {"memories":[{"scope":"user","kind":"preference","title":"short","content":{"note":"..."}, "tags":["iacs"],"confidence":0.0}]}.
Return {"memories":[]} when there is nothing worth remembering."""


class AlfredMemoryService:
    async def recall(
        self,
        *,
        user_id: str | None,
        user_role: str | None,
        session_id: str | None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        user_uuid = _coerce_uuid(user_id)
        session_uuid = _coerce_uuid(session_id)
        async with AsyncSessionLocal() as session:
            clauses = [AlfredMemory.deleted_at.is_(None)]
            scope_clauses = [AlfredMemory.scope == "site"]
            if user_uuid:
                scope_clauses.append((AlfredMemory.scope == "user") & (AlfredMemory.owner_user_id == user_uuid))
            if session_uuid:
                scope_clauses.append((AlfredMemory.scope == "session_summary") & (AlfredMemory.source_session_id == session_uuid))
            scope_filter = or_(*scope_clauses)
            rows = (
                await session.scalars(
                    select(AlfredMemory)
                    .where(*clauses)
                    .where(scope_filter)
                    .order_by(AlfredMemory.last_used_at.desc().nullslast(), AlfredMemory.updated_at.desc())
                    .limit(limit)
                )
            ).all()
            now = datetime.now(tz=UTC)
            for row in rows:
                row.last_used_at = now
            await session.commit()
            for row in rows:
                await session.refresh(row)
            role = str(user_role or "standard").lower()
            return [self._public_memory(row, include_owner=role == "admin") for row in rows]

    async def remember_from_turn(
        self,
        provider: Any,
        *,
        user_message: str,
        assistant_text: str,
        tool_results: list[dict[str, Any]],
        actor_context: dict[str, Any],
        session_id: str,
    ) -> int:
        user = actor_context.get("user") if isinstance(actor_context, dict) else {}
        user_id = str((user or {}).get("id") or "")
        user_role = str((user or {}).get("role") or "standard").lower()
        if not user_id:
            return 0
        try:
            result = await provider.complete(
                [
                    ChatMessageInput("system", MEMORY_EXTRACTION_PROMPT),
                    ChatMessageInput(
                        "user",
                        json.dumps(
                            {
                                "user_message": user_message,
                                "assistant_text": assistant_text,
                                "tool_names": [str(item.get("name") or "") for item in tool_results[:12]],
                                "actor_context": actor_context,
                                "user_role": user_role,
                            },
                            separators=(",", ":"),
                            default=str,
                        ),
                    ),
                ]
            )
        except Exception as exc:
            logger.info("alfred_memory_extract_failed", extra={"error": str(exc)[:180]})
            return 0
        payload = _first_json_object(result.text)
        if not isinstance(payload, dict):
            return 0
        memories = payload.get("memories") if isinstance(payload.get("memories"), list) else []
        created = 0
        for item in memories[:5]:
            if not isinstance(item, dict):
                continue
            if await self.create_memory(
                user_id=user_id,
                user_role=user_role,
                scope=str(item.get("scope") or "user"),
                kind=str(item.get("kind") or "preference"),
                title=str(item.get("title") or ""),
                content=item.get("content") if isinstance(item.get("content"), dict) else {"note": str(item.get("content") or "")},
                tags=item.get("tags") if isinstance(item.get("tags"), list) else [],
                source_session_id=session_id,
                confidence=item.get("confidence"),
            ):
                created += 1
        return created

    async def create_memory(
        self,
        *,
        user_id: str,
        user_role: str,
        scope: str,
        kind: str,
        title: str,
        content: dict[str, Any],
        tags: list[Any],
        source_session_id: str | None = None,
        confidence: Any = None,
    ) -> dict[str, Any] | None:
        scope = scope.strip().lower()
        if scope not in {"user", "site", "session_summary"}:
            scope = "user"
        if scope == "site" and user_role != "admin":
            scope = "user"
        owner_uuid = _coerce_uuid(user_id)
        if not owner_uuid:
            return None
        title = title.strip()[:180]
        if not title:
            note = str(content.get("note") or content.get("text") or "").strip()
            title = note[:80] or "Alfred memory"
        if _contains_secretish(content):
            return None
        try:
            confidence_value = max(0.0, min(1.0, float(confidence if confidence is not None else 0.6)))
        except (TypeError, ValueError):
            confidence_value = 0.6
        row = AlfredMemory(
            scope=scope,
            owner_user_id=None if scope == "site" else owner_uuid,
            kind=kind.strip()[:80] or "preference",
            title=title,
            content=_compact_content(content),
            tags=[str(tag).strip()[:40] for tag in tags[:12] if str(tag).strip()],
            source_session_id=_coerce_uuid(source_session_id),
            confidence=confidence_value,
        )
        async with AsyncSessionLocal() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        emit_audit_log(
            category=TELEMETRY_CATEGORY_ALFRED,
            action="alfred.memory.create",
            actor="Alfred_AI",
            actor_user_id=owner_uuid,
            target_entity="AlfredMemory",
            target_id=str(row.id),
            target_label=row.title,
            metadata={"scope": row.scope, "kind": row.kind, "tags": row.tags},
        )
        return self._public_memory(row, include_owner=True)

    async def list_user_memories(self, *, user_id: str, user_role: str) -> list[dict[str, Any]]:
        user_uuid = _coerce_uuid(user_id)
        if not user_uuid:
            return []
        role = str(user_role or "standard").lower()
        async with AsyncSessionLocal() as session:
            query = select(AlfredMemory).where(AlfredMemory.deleted_at.is_(None))
            if role == "admin":
                query = query.where((AlfredMemory.owner_user_id == user_uuid) | (AlfredMemory.scope == "site"))
            else:
                query = query.where(AlfredMemory.owner_user_id == user_uuid)
            rows = (await session.scalars(query.order_by(AlfredMemory.updated_at.desc()).limit(100))).all()
        return [self._public_memory(row, include_owner=role == "admin") for row in rows]

    async def delete_memory(self, *, memory_id: str, user_id: str, user_role: str) -> bool:
        memory_uuid = _coerce_uuid(memory_id)
        user_uuid = _coerce_uuid(user_id)
        if not memory_uuid or not user_uuid:
            return False
        role = str(user_role or "standard").lower()
        async with AsyncSessionLocal() as session:
            row = await session.get(AlfredMemory, memory_uuid)
            if not row or row.deleted_at:
                return False
            if role != "admin" and row.owner_user_id != user_uuid:
                return False
            row.deleted_at = datetime.now(tz=UTC)
            await session.commit()
        emit_audit_log(
            category=TELEMETRY_CATEGORY_ALFRED,
            action="alfred.memory.delete",
            actor="Alfred_AI",
            actor_user_id=user_uuid,
            target_entity="AlfredMemory",
            target_id=str(memory_uuid),
            target_label=row.title,
            metadata={"scope": row.scope},
        )
        return True

    async def status(self) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(AlfredMemory.scope, func.count(AlfredMemory.id))
                    .where(AlfredMemory.deleted_at.is_(None))
                    .group_by(AlfredMemory.scope)
                )
            ).all()
        counts = {str(scope): int(count) for scope, count in rows}
        return {"enabled": True, "backend": "postgres_json", "counts": counts}

    def _public_memory(self, row: AlfredMemory, *, include_owner: bool = False) -> dict[str, Any]:
        payload = {
            "id": str(row.id),
            "scope": row.scope,
            "kind": row.kind,
            "title": row.title,
            "content": row.content,
            "tags": row.tags,
            "confidence": row.confidence,
            "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        if include_owner:
            payload["owner_user_id"] = str(row.owner_user_id) if row.owner_user_id else None
        return payload


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _contains_secretish(value: Any) -> bool:
    text = json.dumps(value, default=str).lower()
    return any(marker in text for marker in ("password", "api_key", "secret", "token", "bearer "))


def _compact_content(value: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, item in list(value.items())[:20]:
        if item in (None, "", [], {}):
            continue
        key_text = str(key)[:80]
        if any(secret in key_text.lower() for secret in ("password", "api_key", "secret", "token")):
            continue
        clean[key_text] = str(item)[:1200] if not isinstance(item, (dict, list, bool, int, float)) else item
    return clean or {"note": "Memory recorded."}


def _first_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
    return None


alfred_memory_service = AlfredMemoryService()
