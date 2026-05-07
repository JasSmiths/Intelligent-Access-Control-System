"""Feedback, lessons, and eval examples for Alfred v3 learning."""

from __future__ import annotations

import json
import re
import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from app.ai.providers import ChatMessageInput, get_llm_provider
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AlfredEvalExample, AlfredFeedback, AlfredLesson, ChatMessage, User
from app.services.settings import get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, emit_audit_log, sanitize_payload

logger = get_logger(__name__)

FEEDBACK_ANALYSIS_PROMPT = """You convert Alfred response feedback into durable IACS learning.
Do not create keyword rules. Produce concise behavioral guidance and examples that help an LLM answer similar future IACS requests better.
Only use the provided turn snapshot and user feedback. Do not invent system facts.
Never include secrets, IDs, raw tool JSON, file URLs, or private contact details in lesson text.

Return compact JSON:
{"summary":"short","lesson":{"title":"short","lesson":"one concise instruction","tags":["style"],"confidence":0.0},"corrected_answer":"optional corrected answer","eval_example":{"prompt":"user prompt","ideal_answer":"ideal answer"}}"""

DEFAULT_SEEDED_LESSONS = (
    {
        "title": "Keep simple device status answers focused",
        "lesson": (
            "For simple gate or garage state questions, answer the current device state directly. "
            "Do not mention inactive malfunctions, empty diagnostics, or unrelated health checks unless the user asked for diagnosis "
            "or a tool reports an active problem."
        ),
        "tags": ["device-status", "concise-response"],
        "confidence": 0.95,
    },
)

FEEDBACK_COMMAND_RE = re.compile(
    r"^\s*(?:(?:/feedback\s+)?(?P<word>thumbs?\s+up|thumbs?\s+down|up|down)|(?P<emoji>[👍👎]))\b[:\s-]*(?P<detail>.*)$",
    re.IGNORECASE | re.DOTALL,
)
ANSWER_PLACEHOLDER_RE = re.compile(
    r"(\[[a-z0-9 _:-]{2,60}\]|<\s*[a-z0-9 _:-]{2,60}\s*>|\{\{\s*[^{}]{2,80}\s*\}\})",
    re.IGNORECASE,
)


class AlfredFeedbackError(ValueError):
    """Raised when feedback cannot be accepted."""


class AlfredFeedbackService:
    async def seed_default_lessons(self) -> None:
        async with AsyncSessionLocal() as session:
            for item in DEFAULT_SEEDED_LESSONS:
                existing = await session.scalar(
                    select(AlfredLesson)
                    .where(AlfredLesson.scope == "site")
                    .where(AlfredLesson.title == item["title"])
                    .where(AlfredLesson.deleted_at.is_(None))
                )
                if existing:
                    continue
                session.add(
                    AlfredLesson(
                        scope="site",
                        owner_user_id=None,
                        title=item["title"],
                        lesson=item["lesson"],
                        tags=item["tags"],
                        source_feedback_ids=[],
                        confidence=item["confidence"],
                        status="active",
                        active_at=datetime.now(tz=UTC),
                    )
                )
            await session.commit()

    async def recall_active_lessons(
        self,
        *,
        user_id: str | None,
        user_role: str | None,
        message: str | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        user_uuid = _coerce_uuid(user_id)
        scope_filter = [AlfredLesson.scope == "site"]
        if user_uuid:
            scope_filter.append((AlfredLesson.scope == "user") & (AlfredLesson.owner_user_id == user_uuid))
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(AlfredLesson)
                    .where(AlfredLesson.status == "active")
                    .where(AlfredLesson.deleted_at.is_(None))
                    .where(or_(*scope_filter))
                    .order_by(AlfredLesson.confidence.desc(), AlfredLesson.updated_at.desc())
                    .limit(80)
                )
            ).all()
        rows = _rank_lessons_for_prompt(message or "", rows, limit=max(1, min(limit, 12)))
        include_owner = str(user_role or "").lower() == "admin"
        return [self._public_lesson(row, include_owner=include_owner) for row in rows]

    async def submit_feedback(
        self,
        *,
        assistant_message_id: str,
        rating: str,
        reason: str | None,
        ideal_answer: str | None,
        source_channel: str,
        user: User | None = None,
        actor_user_id: str | None = None,
        actor_role: str | None = None,
    ) -> dict[str, Any]:
        rating = _normalize_rating(rating)
        reason = (reason or "").strip()
        ideal_answer = (ideal_answer or "").strip()
        if rating == "down" and not reason:
            raise AlfredFeedbackError("Thumbs-down feedback needs a short reason.")

        actor_uuid = _coerce_uuid(str(user.id) if user else actor_user_id)
        role = (user.role.value if user else actor_role or "standard").strip().lower()
        assistant_uuid = _coerce_uuid(assistant_message_id)
        if not assistant_uuid:
            raise AlfredFeedbackError("assistant_message_id is required.")

        context = await self._load_turn_context(assistant_uuid)
        if not context:
            raise AlfredFeedbackError("Alfred response not found.")
        self._assert_feedback_allowed(context, actor_uuid=actor_uuid, role=role)

        runtime = await get_runtime_config()
        source_channel = (source_channel or "dashboard").strip().lower()[:40] or "dashboard"
        turn_snapshot = sanitize_payload(context["turn_snapshot"])
        original_prompt = str(context.get("original_user_prompt") or "")
        original_answer = str(context.get("original_assistant_response") or "")
        provider_name = str(context.get("provider") or runtime.llm_provider or "")
        model_name = str(context.get("model") or "")

        async with AsyncSessionLocal() as session:
            feedback = AlfredFeedback(
                rating=rating,
                source_channel=source_channel,
                session_id=context.get("session_id"),
                user_message_id=context.get("user_message_id"),
                assistant_message_id=assistant_uuid,
                actor_user_id=actor_uuid,
                actor_role=role,
                provider=provider_name,
                model=model_name,
                original_user_prompt=original_prompt,
                original_assistant_response=original_answer,
                reason=reason or None,
                ideal_answer=ideal_answer or None,
                turn_snapshot=turn_snapshot,
                status="queued",
            )
            session.add(feedback)
            await session.commit()
            await session.refresh(feedback)

        emit_audit_log(
            category=TELEMETRY_CATEGORY_ALFRED,
            action="alfred.feedback.submit",
            actor="Alfred_Feedback",
            actor_user_id=actor_uuid,
            target_entity="AlfredFeedback",
            target_id=str(feedback.id),
            target_label=rating,
            metadata={
                "rating": rating,
                "source_channel": source_channel,
                "status": "queued",
            },
        )
        self._schedule_processing(feedback.id)
        return {
            "feedback": self._public_feedback(feedback),
            "lesson": None,
            "eval_example": None,
            "corrected_answer": "",
            "processing": True,
        }

    def _schedule_processing(self, feedback_id: uuid.UUID) -> None:
        try:
            task = asyncio.create_task(self.process_feedback(feedback_id))
        except RuntimeError:
            logger.info("alfred_feedback_processing_not_scheduled", extra={"feedback_id": str(feedback_id)})
            return
        task.add_done_callback(self._log_processing_failure)

    def _log_processing_failure(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception as exc:
            logger.warning("alfred_feedback_processing_failed", extra={"error": str(exc)[:240]})

    async def process_feedback(self, feedback_id: uuid.UUID | str) -> dict[str, Any] | None:
        feedback_uuid = _coerce_uuid(feedback_id)
        if not feedback_uuid:
            return None
        async with AsyncSessionLocal() as session:
            feedback = await session.get(AlfredFeedback, feedback_uuid)
            if not feedback:
                return None
            if feedback.status not in {"queued", "received", "analysis_failed"}:
                return self._public_feedback(feedback)
            feedback.status = "analyzing"
            await session.commit()
            await session.refresh(feedback)

        runtime = await get_runtime_config()
        rating = feedback.rating
        reason = feedback.reason or ""
        ideal_answer = feedback.ideal_answer or ""
        original_prompt = feedback.original_user_prompt or ""
        original_answer = feedback.original_assistant_response or ""
        turn_snapshot = sanitize_payload(feedback.turn_snapshot or {})
        learning_mode = runtime.alfred_learning_mode
        source_channel = feedback.source_channel
        provider_name = feedback.provider or runtime.llm_provider
        model_name = feedback.model or ""
        role = (feedback.actor_role or "standard").strip().lower()
        actor_uuid = feedback.actor_user_id

        analysis = await self._analyze_feedback(
            runtime_provider=runtime.llm_provider,
            rating=rating,
            reason=reason,
            ideal_answer=ideal_answer,
            original_prompt=original_prompt,
            original_answer=original_answer,
            turn_snapshot=turn_snapshot,
        )
        corrected_answer = _safe_corrected_answer(
            analysis.get("corrected_answer"),
            ideal_answer=ideal_answer,
            turn_snapshot=turn_snapshot,
        )
        analysis["corrected_answer"] = corrected_answer
        lesson = await self._create_lesson_from_analysis(
            feedback_id=str(feedback.id),
            actor_uuid=actor_uuid,
            actor_role=role,
            rating=rating,
            learning_mode=learning_mode,
            analysis=analysis,
        )
        eval_example = await self._create_eval_example(
            feedback_id=str(feedback.id),
            scope=lesson.get("scope") if lesson else ("site" if role == "admin" else "user"),
            original_prompt=original_prompt,
            original_answer=original_answer,
            ideal_answer=ideal_answer,
            corrected_answer=corrected_answer,
            lesson_text=str((analysis.get("lesson") or {}).get("lesson") or ""),
            metadata={"rating": rating, "source_channel": source_channel, "provider": provider_name, "model": model_name},
        )

        async with AsyncSessionLocal() as session:
            row = await session.get(AlfredFeedback, feedback.id)
            if row:
                row.analysis = analysis
                row.corrected_answer = corrected_answer or None
                row.lesson_id = _coerce_uuid(lesson.get("id")) if lesson else None
                row.status = "processed" if lesson or eval_example else "analysis_failed"
                await session.commit()
                await session.refresh(row)
                feedback = row

        return {
            "feedback": self._public_feedback(feedback),
            "lesson": lesson,
            "eval_example": eval_example,
            "corrected_answer": corrected_answer,
            "processing": False,
        }

    async def submit_feedback_for_last_response(
        self,
        *,
        session_id: str,
        rating: str,
        reason: str | None,
        ideal_answer: str | None,
        source_channel: str,
        actor_user_id: str | None,
        actor_role: str,
    ) -> dict[str, Any]:
        session_uuid = _coerce_uuid(session_id)
        if not session_uuid:
            raise AlfredFeedbackError("session_id is required.")
        async with AsyncSessionLocal() as session:
            message = await session.scalar(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_uuid)
                .where(ChatMessage.role == "assistant")
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
        if not message:
            raise AlfredFeedbackError("No Alfred response is available for feedback in this conversation.")
        return await self.submit_feedback(
            assistant_message_id=str(message.id),
            rating=rating,
            reason=reason,
            ideal_answer=ideal_answer,
            source_channel=source_channel,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
        )

    async def list_feedback(self, *, limit: int = 100) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(AlfredFeedback).order_by(AlfredFeedback.created_at.desc()).limit(max(1, min(limit, 250)))
                )
            ).all()
        return [self._public_feedback(row) for row in rows]

    async def list_lessons(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            query = select(AlfredLesson).where(AlfredLesson.deleted_at.is_(None))
            if status:
                query = query.where(AlfredLesson.status == status)
            rows = (
                await session.scalars(
                    query.order_by(AlfredLesson.updated_at.desc()).limit(max(1, min(limit, 250)))
                )
            ).all()
        return [self._public_lesson(row, include_owner=True) for row in rows]

    async def review_lesson(
        self,
        *,
        lesson_id: str,
        decision: str,
        reviewer: User,
        lesson_text: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        lesson_uuid = _coerce_uuid(lesson_id)
        if not lesson_uuid:
            raise AlfredFeedbackError("lesson_id is required.")
        decision = decision.strip().lower()
        now = datetime.now(tz=UTC)
        async with AsyncSessionLocal() as session:
            row = await session.get(AlfredLesson, lesson_uuid)
            if not row or row.deleted_at:
                raise AlfredFeedbackError("Lesson not found.")
            if title is not None:
                row.title = title.strip()[:180] or row.title
                row.edited_by_user_id = reviewer.id
            if lesson_text is not None:
                row.lesson = lesson_text.strip() or row.lesson
                row.edited_by_user_id = reviewer.id
            if decision == "approve":
                row.status = "active"
                row.approved_by_user_id = reviewer.id
                row.approved_at = now
                row.active_at = now
            elif decision == "reject":
                row.status = "rejected"
                row.rejected_by_user_id = reviewer.id
                row.rejected_at = now
            else:
                raise AlfredFeedbackError("decision must be approve or reject.")
            await session.commit()
            await session.refresh(row)
        emit_audit_log(
            category=TELEMETRY_CATEGORY_ALFRED,
            action=f"alfred.lesson.{decision}",
            actor=f"{reviewer.full_name or reviewer.username}",
            actor_user_id=reviewer.id,
            target_entity="AlfredLesson",
            target_id=str(row.id),
            target_label=row.title,
            metadata={"scope": row.scope, "status": row.status},
        )
        return self._public_lesson(row, include_owner=True)

    async def list_eval_examples(self, *, limit: int = 100) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(AlfredEvalExample)
                    .order_by(AlfredEvalExample.created_at.desc())
                    .limit(max(1, min(limit, 500)))
                )
            ).all()
        return [self._public_eval(row) for row in rows]

    async def export_eval_jsonl(self) -> str:
        examples = await self.list_eval_examples(limit=500)
        lines = []
        for example in examples:
            lines.append(
                json.dumps(
                    {
                        "prompt": example["prompt"],
                        "ideal_answer": example.get("ideal_answer") or example.get("corrected_answer"),
                        "metadata": example.get("metadata") or {},
                    },
                    separators=(",", ":"),
                    default=str,
                )
            )
        return "\n".join(lines) + ("\n" if lines else "")

    async def _load_turn_context(self, assistant_message_id: uuid.UUID) -> dict[str, Any] | None:
        async with AsyncSessionLocal() as session:
            assistant = await session.get(ChatMessage, assistant_message_id)
            if not assistant or assistant.role != "assistant":
                return None
            payload = assistant.tool_payload if isinstance(assistant.tool_payload, dict) else {}
            snapshot = payload.get("turn_snapshot") if isinstance(payload.get("turn_snapshot"), dict) else {}
            user_message_id = _coerce_uuid(snapshot.get("user_message_id") or payload.get("user_message_id"))
            user_message = await session.get(ChatMessage, user_message_id) if user_message_id else None
            if not user_message:
                user_message = await session.scalar(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == assistant.session_id)
                    .where(ChatMessage.role == "user")
                    .where(ChatMessage.created_at <= assistant.created_at)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(1)
                )
                user_message_id = user_message.id if user_message else None
        return {
            "session_id": assistant.session_id,
            "user_message_id": user_message_id,
            "original_user_prompt": snapshot.get("user_message") or (user_message.content if user_message else ""),
            "original_assistant_response": snapshot.get("assistant_response") or assistant.content,
            "provider": snapshot.get("provider") or payload.get("provider"),
            "model": snapshot.get("model") or payload.get("model"),
            "turn_snapshot": snapshot or payload,
        }

    def _assert_feedback_allowed(
        self,
        context: dict[str, Any],
        *,
        actor_uuid: uuid.UUID | None,
        role: str,
    ) -> None:
        if role == "admin":
            return
        snapshot = context.get("turn_snapshot") if isinstance(context.get("turn_snapshot"), dict) else {}
        actor_context = snapshot.get("actor_context") if isinstance(snapshot.get("actor_context"), dict) else {}
        turn_user = actor_context.get("user") if isinstance(actor_context.get("user"), dict) else {}
        turn_user_id = _coerce_uuid(turn_user.get("id"))
        if turn_user_id and actor_uuid and turn_user_id == actor_uuid:
            return
        if not turn_user_id:
            return
        raise AlfredFeedbackError("You can only rate your own Alfred responses.")

    async def _analyze_feedback(
        self,
        *,
        runtime_provider: str,
        rating: str,
        reason: str,
        ideal_answer: str,
        original_prompt: str,
        original_answer: str,
        turn_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            provider = get_llm_provider(runtime_provider)
            result = await provider.complete(
                [
                    ChatMessageInput("system", FEEDBACK_ANALYSIS_PROMPT),
                    ChatMessageInput(
                        "user",
                        json.dumps(
                            {
                                "rating": rating,
                                "reason": reason,
                                "ideal_answer": ideal_answer,
                                "original_user_prompt": original_prompt,
                                "original_assistant_response": original_answer,
                                "turn_snapshot": sanitize_payload(turn_snapshot),
                            },
                            separators=(",", ":"),
                            default=str,
                        ),
                    ),
                ]
            )
        except Exception as exc:
            logger.info("alfred_feedback_analysis_failed", extra={"error": str(exc)[:180]})
            return {
                "summary": "Feedback stored, but LLM analysis failed.",
                "error": str(exc)[:240],
                "lesson": {},
                "corrected_answer": "",
                "eval_example": {"prompt": original_prompt, "ideal_answer": ideal_answer or original_answer},
            }
        payload = _first_json_object(result.text)
        if not isinstance(payload, dict):
            return {
                "summary": "Feedback stored, but LLM analysis returned no JSON.",
                "lesson": {},
                "corrected_answer": "",
                "eval_example": {"prompt": original_prompt, "ideal_answer": ideal_answer or original_answer},
            }
        return sanitize_payload(payload)

    async def _create_lesson_from_analysis(
        self,
        *,
        feedback_id: str,
        actor_uuid: uuid.UUID | None,
        actor_role: str,
        rating: str,
        learning_mode: str,
        analysis: dict[str, Any],
    ) -> dict[str, Any] | None:
        raw_lesson = analysis.get("lesson") if isinstance(analysis.get("lesson"), dict) else {}
        lesson_text = str(raw_lesson.get("lesson") or "").strip()
        if not lesson_text:
            return None
        scope = "site" if actor_role == "admin" else "user"
        status = "active" if learning_mode == "auto_learn" else "pending"
        if scope == "site" and actor_role != "admin":
            scope = "user"
        now = datetime.now(tz=UTC)
        try:
            confidence = max(0.0, min(1.0, float(raw_lesson.get("confidence", 0.6))))
        except (TypeError, ValueError):
            confidence = 0.6
        async with AsyncSessionLocal() as session:
            row = AlfredLesson(
                scope=scope,
                owner_user_id=None if scope == "site" else actor_uuid,
                title=str(raw_lesson.get("title") or "Alfred feedback lesson").strip()[:180],
                lesson=lesson_text[:2000],
                tags=[str(tag).strip()[:40] for tag in (raw_lesson.get("tags") if isinstance(raw_lesson.get("tags"), list) else [])[:12] if str(tag).strip()],
                source_feedback_ids=[feedback_id],
                confidence=confidence,
                status=status,
                created_by_user_id=actor_uuid,
                approved_by_user_id=actor_uuid if status == "active" and actor_role == "admin" else None,
                approved_at=now if status == "active" and actor_role == "admin" else None,
                active_at=now if status == "active" else None,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return self._public_lesson(row, include_owner=True) | {"rating": rating}

    async def _create_eval_example(
        self,
        *,
        feedback_id: str,
        scope: str,
        original_prompt: str,
        original_answer: str,
        ideal_answer: str,
        corrected_answer: str,
        lesson_text: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        prompt = original_prompt.strip()
        if not prompt:
            return None
        safe_ideal_answer = "" if _contains_placeholder(ideal_answer) else ideal_answer.strip()
        safe_corrected_answer = "" if _contains_placeholder(corrected_answer) else corrected_answer.strip()
        target_answer = safe_ideal_answer or safe_corrected_answer
        if not target_answer:
            return None
        async with AsyncSessionLocal() as session:
            row = AlfredEvalExample(
                feedback_id=_coerce_uuid(feedback_id),
                scope=scope if scope in {"user", "site"} else "user",
                prompt=prompt[:4000],
                bad_answer=original_answer[:4000] or None,
                ideal_answer=safe_ideal_answer[:4000] or None,
                corrected_answer=safe_corrected_answer[:4000] or None,
                lesson=lesson_text[:2000] or None,
                metadata_=sanitize_payload(metadata),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return self._public_eval(row)

    def _public_feedback(self, row: AlfredFeedback) -> dict[str, Any]:
        corrected_answer = "" if _contains_placeholder(row.corrected_answer or "") else row.corrected_answer
        analysis = _public_analysis(row.analysis)
        return {
            "id": str(row.id),
            "rating": row.rating,
            "source_channel": row.source_channel,
            "session_id": str(row.session_id) if row.session_id else None,
            "user_message_id": str(row.user_message_id) if row.user_message_id else None,
            "assistant_message_id": str(row.assistant_message_id) if row.assistant_message_id else None,
            "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
            "actor_role": row.actor_role,
            "provider": row.provider,
            "model": row.model,
            "original_user_prompt": row.original_user_prompt,
            "original_assistant_response": row.original_assistant_response,
            "reason": row.reason,
            "ideal_answer": row.ideal_answer,
            "corrected_answer": corrected_answer,
            "analysis": analysis,
            "status": row.status,
            "lesson_id": str(row.lesson_id) if row.lesson_id else None,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    def _public_lesson(self, row: AlfredLesson, *, include_owner: bool) -> dict[str, Any]:
        payload = {
            "id": str(row.id),
            "scope": row.scope,
            "title": row.title,
            "lesson": row.lesson,
            "tags": row.tags,
            "source_feedback_ids": row.source_feedback_ids,
            "confidence": row.confidence,
            "status": row.status,
            "active_at": row.active_at.isoformat() if row.active_at else None,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        if include_owner:
            payload["owner_user_id"] = str(row.owner_user_id) if row.owner_user_id else None
        return payload

    def _public_eval(self, row: AlfredEvalExample) -> dict[str, Any]:
        ideal_answer = "" if _contains_placeholder(row.ideal_answer or "") else row.ideal_answer
        corrected_answer = "" if _contains_placeholder(row.corrected_answer or "") else row.corrected_answer
        return {
            "id": str(row.id),
            "feedback_id": str(row.feedback_id) if row.feedback_id else None,
            "scope": row.scope,
            "prompt": row.prompt,
            "bad_answer": row.bad_answer,
            "ideal_answer": ideal_answer,
            "corrected_answer": corrected_answer,
            "lesson": row.lesson,
            "metadata": row.metadata_ or {},
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }


def parse_feedback_command(text: str) -> dict[str, str] | None:
    match = FEEDBACK_COMMAND_RE.match(text or "")
    if not match:
        return None
    word = (match.group("word") or "").lower()
    emoji = match.group("emoji") or ""
    rating = "down" if "down" in word or emoji == "👎" else "up"
    detail = (match.group("detail") or "").strip()
    reason = detail
    ideal = ""
    ideal_match = re.search(r"\bideal\s*:\s*(.+)$", detail, flags=re.IGNORECASE | re.DOTALL)
    if ideal_match:
        ideal = ideal_match.group(1).strip()
        reason = detail[: ideal_match.start()].strip(" -:\n")
    return {"rating": rating, "reason": reason, "ideal_answer": ideal}


def _normalize_rating(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"up", "thumbs_up", "positive", "good", "👍"}:
        return "up"
    if normalized in {"down", "thumbs_down", "negative", "bad", "👎"}:
        return "down"
    raise AlfredFeedbackError("rating must be up or down.")


def _safe_corrected_answer(
    value: Any,
    *,
    ideal_answer: str,
    turn_snapshot: dict[str, Any],
) -> str:
    corrected = str(value or "").strip()
    if not corrected:
        return ""
    if _contains_placeholder(corrected):
        return ""
    if ideal_answer.strip() and corrected.strip() == ideal_answer.strip() and _contains_placeholder(ideal_answer):
        return ""
    if _needs_live_facts(corrected) and not _turn_has_tool_results(turn_snapshot) and not ideal_answer.strip():
        return ""
    return corrected[:4000]


def _public_analysis(value: Any) -> dict[str, Any] | None:
    analysis = sanitize_payload(value or {}) if value else None
    if not isinstance(analysis, dict):
        return None
    analysis = dict(analysis)
    if _contains_placeholder(str(analysis.get("corrected_answer") or "")):
        analysis["corrected_answer"] = ""
    eval_example = analysis.get("eval_example")
    if isinstance(eval_example, dict):
        public_eval = dict(eval_example)
        if _contains_placeholder(str(public_eval.get("ideal_answer") or "")):
            public_eval["ideal_answer"] = ""
        if _contains_placeholder(str(public_eval.get("corrected_answer") or "")):
            public_eval["corrected_answer"] = ""
        analysis["eval_example"] = public_eval
    return analysis


def _contains_placeholder(value: str) -> bool:
    lowered = value.lower()
    if ANSWER_PLACEHOLDER_RE.search(value):
        return True
    return any(token in lowered for token in ("placeholder", "insert time", "insert date", "unknown time"))


def _needs_live_facts(value: str) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in ("left at", "arrived at", "arrived", "departed", "gate is", "currently"))


def _turn_has_tool_results(turn_snapshot: dict[str, Any]) -> bool:
    results = turn_snapshot.get("tool_results") if isinstance(turn_snapshot, dict) else None
    return bool(isinstance(results, list) and results)


TRAINING_RELEVANCE_STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "can",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "him",
    "his",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "she",
    "so",
    "than",
    "that",
    "the",
    "their",
    "them",
    "there",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "who",
    "why",
    "with",
    "you",
    "your",
}

TRAINING_TERM_ALIASES = {
    "arrive": "arrival",
    "arrived": "arrival",
    "arrives": "arrival",
    "arrival": "arrival",
    "back": "arrival",
    "came": "arrival",
    "entered": "arrival",
    "entry": "arrival",
    "return": "arrival",
    "returned": "arrival",
    "showed": "arrival",
    "depart": "departure",
    "departed": "departure",
    "departure": "departure",
    "exit": "departure",
    "exited": "departure",
    "gone": "departure",
    "leave": "departure",
    "leaves": "departure",
    "leaving": "departure",
    "left": "departure",
    "pass": "visitor_pass",
    "passes": "visitor_pass",
    "visitor": "visitor_pass",
    "visitors": "visitor_pass",
    "garage": "device_state",
    "gate": "device_state",
    "state": "device_state",
    "status": "device_state",
    "minimal": "concise",
    "concise": "concise",
    "direct": "concise",
    "focused": "concise",
    "simple": "concise",
    "exact": "time_query",
    "morning": "time_query",
    "time": "time_query",
    "today": "time_query",
    "tonight": "time_query",
}


def _rank_lessons_for_prompt(message: str, rows: list[AlfredLesson], *, limit: int) -> list[AlfredLesson]:
    bounded_limit = max(1, min(limit, 12))
    if not str(message or "").strip():
        return list(rows)[:bounded_limit]
    scored: list[tuple[float, float, int, AlfredLesson]] = []
    for index, row in enumerate(rows):
        score = _lesson_relevance_score(message, row)
        if score <= 0:
            continue
        try:
            confidence = float(getattr(row, "confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        scored.append((score, confidence, -index, row))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
    return [row for _score, _confidence, _index, row in scored[:bounded_limit]]


def _lesson_relevance_score(message: str, row: AlfredLesson) -> float:
    message_terms = _training_terms(message)
    if not message_terms:
        return 0.0
    title_terms = _training_terms(str(getattr(row, "title", "") or ""))
    lesson_terms = _training_terms(str(getattr(row, "lesson", "") or ""))
    tag_terms: set[str] = set()
    tags = getattr(row, "tags", None)
    if isinstance(tags, list):
        for tag in tags:
            tag_terms.update(_training_terms(str(tag).replace("-", " ")))
    title_overlap = message_terms & title_terms
    tag_overlap = message_terms & tag_terms
    lesson_overlap = message_terms & lesson_terms
    if not (title_overlap or tag_overlap or lesson_overlap):
        return 0.0
    meaningful_overlap = (title_overlap | tag_overlap | lesson_overlap) - {
        "concise",
        "exact",
        "morning",
        "time",
        "time_query",
        "today",
        "tonight",
    }
    if not meaningful_overlap:
        return 0.0
    score = (len(title_overlap) * 3.0) + (len(tag_overlap) * 2.0) + len(lesson_overlap)
    if {"arrival", "departure"} & message_terms and {"arrival", "departure"} & (title_terms | tag_terms):
        score += 1.5
    if "time_query" in message_terms and "time_query" in (title_terms | tag_terms | lesson_terms):
        score += 1.0
    try:
        score += max(0.0, min(float(getattr(row, "confidence", 0.0) or 0.0), 1.0)) * 0.2
    except (TypeError, ValueError):
        pass
    return score


def _training_terms(value: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", str(value or "").lower())
    terms: set[str] = set()
    for token in tokens:
        if token in TRAINING_RELEVANCE_STOPWORDS:
            continue
        if len(token) < 3:
            continue
        terms.add(token)
        expanded = _expand_training_term(token)
        if expanded:
            terms.add(expanded)
    if "get" in tokens and "back" in tokens:
        terms.add("arrival")
    return terms


def _expand_training_term(token: str) -> str:
    return TRAINING_TERM_ALIASES.get(token, "")


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _first_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    candidates = [match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.I | re.S)]
    candidates.append(text.strip())
    for candidate in candidates:
        start = candidate.find("{")
        if start < 0:
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(candidate)):
            char = candidate[index]
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
                        parsed = json.loads(candidate[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    return parsed if isinstance(parsed, dict) else None
    return None


alfred_feedback_service = AlfredFeedbackService()
