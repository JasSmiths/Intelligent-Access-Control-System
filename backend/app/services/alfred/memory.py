"""Postgres JSON-backed memory for Alfred v3."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, text

from app.ai.providers import ChatMessageInput
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AlfredLesson, AlfredMemory
from app.services.alfred.embeddings import embedding_text, generate_embedding, vector_literal
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, emit_audit_log

logger = get_logger(__name__)

MEMORY_EXTRACTION_PROMPT = """Extract durable Alfred memory from the completed IACS chat turn.
Only keep stable preferences, useful context, or operator instructions that will help future IACS conversations.
Do not store secrets, passwords, API keys, tokens, plates from one-off visitor messages, raw tool JSON, transient diagnostics, or anything about visitors.
Admins may create site memory when the user explicitly says it should apply to the whole site. Otherwise use user scope.
Return compact JSON: {"memories":[{"scope":"user","kind":"preference","title":"short","content":{"note":"..."}, "tags":["iacs"],"confidence":0.0}]}.
Return {"memories":[]} when there is nothing worth remembering."""


class AlfredMemoryService:
    async def semantic_search(
        self,
        query: str,
        limit: int = 5,
        actor_id: str | uuid.UUID | int | None = None,
    ) -> list[dict[str, Any]]:
        """Return semantically relevant visible Alfred memories and lessons.

        Exact memory recall remains the primary durable-memory path. This
        search is an additive hint for the planner and falls back to lexical
        scoring when embeddings or pgvector are unavailable.
        """

        bounded_limit = max(1, min(int(limit or 5), 12))
        actor_uuid = _coerce_uuid(actor_id)
        query_text = str(query or "").strip()
        if not query_text:
            return []
        query_embedding = await generate_embedding(query_text, purpose="alfred_semantic_query")
        if query_embedding:
            try:
                rows = await self._semantic_vector_search(
                    query_embedding=query_embedding,
                    limit=bounded_limit,
                    actor_uuid=actor_uuid,
                )
                if rows:
                    return rows
            except Exception as exc:
                logger.info("alfred_semantic_search_failed", extra={"error": str(exc)[:180]})
        return await self._lexical_semantic_fallback(query_text, limit=bounded_limit, actor_uuid=actor_uuid)

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
        compact_content = _compact_content(content)
        embedding = await generate_embedding(
            _memory_embedding_text(title=title, kind=kind, content=compact_content, tags=tags),
            purpose="alfred_memory_create",
        )
        row = AlfredMemory(
            scope=scope,
            owner_user_id=None if scope == "site" else owner_uuid,
            kind=kind.strip()[:80] or "preference",
            title=title,
            content=compact_content,
            tags=[str(tag).strip()[:40] for tag in tags[:12] if str(tag).strip()],
            source_session_id=_coerce_uuid(source_session_id),
            confidence=confidence_value,
            embedding=embedding,
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
        return {"enabled": True, "backend": "postgres_json_pgvector", "counts": counts}

    async def _semantic_vector_search(
        self,
        *,
        query_embedding: list[float],
        limit: int,
        actor_uuid: uuid.UUID | None,
    ) -> list[dict[str, Any]]:
        embedding = vector_literal(query_embedding)
        memory_scope_sql = "scope = 'site'"
        lesson_scope_sql = "scope = 'site'"
        params: dict[str, Any] = {"embedding": embedding, "limit": limit}
        if actor_uuid:
            memory_scope_sql = "(scope = 'site' OR (scope = 'user' AND owner_user_id = :actor_uuid))"
            lesson_scope_sql = "(scope = 'site' OR (scope = 'user' AND owner_user_id = :actor_uuid))"
            params["actor_uuid"] = actor_uuid

        async with AsyncSessionLocal() as session:
            memory_rows = (
                await session.execute(
                    text(
                        f"""
                        SELECT
                            id::text AS id,
                            scope,
                            kind,
                            title,
                            content,
                            tags,
                            confidence,
                            1 - (embedding <=> CAST(:embedding AS vector)) AS score,
                            updated_at
                        FROM alfred_memories
                        WHERE deleted_at IS NULL
                          AND embedding IS NOT NULL
                          AND {memory_scope_sql}
                        ORDER BY embedding <=> CAST(:embedding AS vector)
                        LIMIT :limit
                        """
                    ),
                    params,
                )
            ).mappings().all()
            lesson_rows = (
                await session.execute(
                    text(
                        f"""
                        SELECT
                            id::text AS id,
                            scope,
                            title,
                            lesson,
                            tags,
                            confidence,
                            1 - (embedding <=> CAST(:embedding AS vector)) AS score,
                            updated_at
                        FROM alfred_lessons
                        WHERE status = 'active'
                          AND deleted_at IS NULL
                          AND embedding IS NOT NULL
                          AND {lesson_scope_sql}
                        ORDER BY embedding <=> CAST(:embedding AS vector)
                        LIMIT :limit
                        """
                    ),
                    params,
                )
            ).mappings().all()

        results = [
            _memory_search_result(row, source="semantic") for row in memory_rows
        ] + [
            _lesson_search_result(row, source="semantic") for row in lesson_rows
        ]
        results.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return results[:limit]

    async def _lexical_semantic_fallback(
        self,
        query: str,
        *,
        limit: int,
        actor_uuid: uuid.UUID | None,
    ) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            memory_filter = [AlfredMemory.scope == "site"]
            lesson_filter = [AlfredLesson.scope == "site"]
            if actor_uuid:
                memory_filter.append((AlfredMemory.scope == "user") & (AlfredMemory.owner_user_id == actor_uuid))
                lesson_filter.append((AlfredLesson.scope == "user") & (AlfredLesson.owner_user_id == actor_uuid))
            memories = (
                await session.scalars(
                    select(AlfredMemory)
                    .where(AlfredMemory.deleted_at.is_(None))
                    .where(or_(*memory_filter))
                    .order_by(AlfredMemory.updated_at.desc())
                    .limit(40)
                )
            ).all()
            lessons = (
                await session.scalars(
                    select(AlfredLesson)
                    .where(AlfredLesson.status == "active")
                    .where(AlfredLesson.deleted_at.is_(None))
                    .where(or_(*lesson_filter))
                    .order_by(AlfredLesson.confidence.desc(), AlfredLesson.updated_at.desc())
                    .limit(40)
                )
            ).all()

        query_terms = _semantic_terms(query)
        scored: list[dict[str, Any]] = []
        for row in memories:
            score = _lexical_score(query_terms, _memory_embedding_text(title=row.title, kind=row.kind, content=row.content, tags=row.tags))
            if score > 0:
                scored.append(_public_memory_search_result(row, score=score, source="lexical"))
        for row in lessons:
            score = _lexical_score(query_terms, embedding_text(row.title, row.lesson, " ".join(row.tags or [])))
            if score > 0:
                scored.append(_public_lesson_search_result(row, score=score, source="lexical"))
        scored.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return scored[:limit]

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


def _memory_embedding_text(*, title: str, kind: str, content: dict[str, Any], tags: list[Any]) -> str:
    return embedding_text(title, kind, json.dumps(content, default=str, sort_keys=True), " ".join(str(tag) for tag in tags))


def _memory_search_result(row: Any, *, source: str) -> dict[str, Any]:
    content = row.get("content") if hasattr(row, "get") else getattr(row, "content", {})
    return {
        "source_type": "memory",
        "source": source,
        "id": str(row["id"] if hasattr(row, "__getitem__") else row.id),
        "scope": str(row["scope"] if hasattr(row, "__getitem__") else row.scope),
        "kind": str(row["kind"] if hasattr(row, "__getitem__") else row.kind),
        "title": str(row["title"] if hasattr(row, "__getitem__") else row.title),
        "content": content if isinstance(content, dict) else {},
        "tags": list(row["tags"] if hasattr(row, "__getitem__") and isinstance(row["tags"], list) else getattr(row, "tags", []) or []),
        "confidence": float(row["confidence"] if hasattr(row, "__getitem__") else row.confidence or 0.0),
        "score": float(row["score"] if hasattr(row, "__getitem__") else 0.0),
    }


def _lesson_search_result(row: Any, *, source: str) -> dict[str, Any]:
    return {
        "source_type": "lesson",
        "source": source,
        "id": str(row["id"] if hasattr(row, "__getitem__") else row.id),
        "scope": str(row["scope"] if hasattr(row, "__getitem__") else row.scope),
        "title": str(row["title"] if hasattr(row, "__getitem__") else row.title),
        "lesson": str(row["lesson"] if hasattr(row, "__getitem__") else row.lesson),
        "tags": list(row["tags"] if hasattr(row, "__getitem__") and isinstance(row["tags"], list) else getattr(row, "tags", []) or []),
        "confidence": float(row["confidence"] if hasattr(row, "__getitem__") else row.confidence or 0.0),
        "score": float(row["score"] if hasattr(row, "__getitem__") else 0.0),
    }


def _public_memory_search_result(row: AlfredMemory, *, score: float, source: str) -> dict[str, Any]:
    return {
        "source_type": "memory",
        "source": source,
        "id": str(row.id),
        "scope": row.scope,
        "kind": row.kind,
        "title": row.title,
        "content": row.content,
        "tags": row.tags,
        "confidence": row.confidence,
        "score": score,
    }


def _public_lesson_search_result(row: AlfredLesson, *, score: float, source: str) -> dict[str, Any]:
    return {
        "source_type": "lesson",
        "source": source,
        "id": str(row.id),
        "scope": row.scope,
        "title": row.title,
        "lesson": row.lesson,
        "tags": row.tags,
        "confidence": row.confidence,
        "score": score,
    }


def _semantic_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", str(value or "").lower())
        if token not in {"about", "after", "before", "could", "should", "there", "their", "would"}
    }


def _lexical_score(query_terms: set[str], text_value: str) -> float:
    if not query_terms:
        return 0.0
    terms = _semantic_terms(text_value)
    overlap = query_terms & terms
    if not overlap:
        return 0.0
    return len(overlap) / max(1.0, len(query_terms))


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
