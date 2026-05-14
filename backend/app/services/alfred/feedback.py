"""Feedback, lessons, and eval examples for Alfred v3 learning."""

from __future__ import annotations

import json
import re
import asyncio
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from app.ai.providers import ChatMessageInput, complete_with_provider_options, get_llm_provider
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AlfredEvalExample, AlfredFeedback, AlfredLesson, ChatMessage, User
from app.services.alfred.embeddings import embedding_text, generate_embedding
from app.services.settings import get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, emit_audit_log, sanitize_payload

logger = get_logger(__name__)

FEEDBACK_ANALYSIS_PROMPT = """You convert Alfred response feedback into durable IACS learning.
Do not create keyword rules. Produce concise behavioral guidance and examples that help an LLM answer similar future IACS requests better.
Only use the provided turn snapshot and user feedback. Do not invent system facts.
Never include secrets, IDs, raw tool JSON, file URLs, or private contact details in lesson text.
Never create lessons that tell Alfred to mention time zones, timezone labels, or local-time labels; IACS user-facing times use the site clock silently.

Return compact JSON:
{"summary":"short","lesson":{"title":"short","lesson":"one concise instruction","tags":["style"],"confidence":0.0},"corrected_answer":"optional corrected answer","eval_example":{"prompt":"user prompt","ideal_answer":"ideal answer"}}"""

TURN_REFLECTION_PROMPT = """Reflect briefly on this completed Alfred IACS chat turn.
Do not expose or store hidden reasoning. Do not create keyword rules. Do not invent system facts.
Generalize only durable behavior that would improve future similar turns.
Never include secrets, IDs, raw tool JSON, file URLs, private contact details, or transient visitor details.
Never create lessons that tell Alfred to mention time zones, timezone labels, or local-time labels; IACS user-facing times use the site clock silently.
If there is no useful generalized lesson, return {"reflection":null}.

Return compact JSON:
{"reflection":{"went_well":"short","could_improve":"short","key_lesson":"one concise generalized instruction","title":"short","tags":["reflection"],"confidence":0.0}}"""

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
    {
        "title": "Investigate missing access as a full chain",
        "lesson": (
            "For missing access, gate, or notification incidents, do not stop at no matching access event. "
            "Run the incident investigation path, check suppressed reads, and explain the LPR to access event "
            "to gate to notification chain from tool evidence."
        ),
        "tags": ["access-incidents", "suppressed-reads", "diagnostic-chain"],
        "confidence": 0.98,
    },
    {
        "title": "Distinguish absence duration from visit duration",
        "lesson": (
            "When the user asks how long someone was away from site, answer the absence interval by pairing "
            "the person's exit with their next entry. Use visit duration only for questions about how long "
            "someone stayed on site, was here, or visited."
        ),
        "tags": ["access-logs", "absence-duration", "semantic-routing"],
        "confidence": 0.97,
    },
    {
        "title": "Keep duration answers human",
        "lesson": (
            "For simple absence or visit duration questions, answer with the duration first in Alfred's natural voice. "
            "Include the exact leave, return, or as-of times as evidence, but avoid robotic audit-log phrasing."
        ),
        "tags": ["access-logs", "absence-duration", "visit-duration", "concise-response", "persona"],
        "confidence": 0.96,
    },
    {
        "title": "Use the site clock silently",
        "lesson": (
            "For user-facing time answers, give the time plainly on the site clock. Do not mention time zone names, "
            "time zone abbreviations, UTC offsets, or local-time labels."
        ),
        "tags": ["time", "site-clock", "concise-response", "persona"],
        "confidence": 0.96,
    },
)

DEFAULT_SEEDED_EVAL_EXAMPLES = (
    {
        "prompt": "Ash came back at 18:18 but he wasnt let in and no notification fired, fully investigate what happened",
        "ideal_answer": (
            "Trace the chain from evidence: camera/webhook, access-event outcome, gate-command outcome, "
            "notification outcome, root cause, and repair availability. If the incident tool finds an "
            "IACS suppressed read, say that IACS received the plate read but suppressed it as "
            "`vehicle_session_already_active` against an earlier event, so no access event was finalized "
            "and notifications never ran. Offer only the confirmation-required history/presence backfill; "
            "do not imply gate, garage, automation, or notification actions will be replayed."
        ),
        "lesson": (
            "Missing access plus no notification questions require the full incident investigation path, "
            "including suppressed-read evidence and the LPR to access event to gate to notification chain."
        ),
        "metadata": {
            "seed": "ash_1818_suppressed_read_incident",
            "tags": ["access-incidents", "suppressed-reads", "notifications"],
        },
    },
    {
        "prompt": "How long was Sylv out for?",
        "ideal_answer": (
            "Treat this as an absence-duration question. Use access-log evidence to pair Sylv's exit with "
            "the following entry, then answer with the off-site interval and the leave/return times. Do not "
            "use the on-site visit-duration calculation for this wording."
        ),
        "lesson": (
            "Absence-duration questions require exit-to-next-entry pairing; visit-duration questions require "
            "entry-to-next-exit pairing."
        ),
        "metadata": {
            "seed": "sylv_absence_duration_exit_to_entry",
            "tags": ["access-logs", "absence-duration", "semantic-routing"],
        },
    },
    {
        "prompt": "how long has Ash been gone?",
        "ideal_answer": (
            "Use the latest granted exit for Ash and, if there is no later entry, answer the ongoing absence "
            "duration naturally from that exit to now. Do not use robotic 'latest logged departure' wording "
            "and do not mention a time zone."
        ),
        "lesson": (
            "Ongoing absence answers should be concise, warm, and based on the selected exit-to-now interval."
        ),
        "metadata": {
            "seed": "ash_ongoing_absence_human_voice",
            "tags": ["access-logs", "absence-duration", "persona"],
        },
    },
    {
        "prompt": "When did Sylv get back?",
        "ideal_answer": (
            "Treat 'got back' as latest granted entry evidence for Sylv. Answer with the return time plainly, "
            "without redundant access-log terminology such as 'arrival logged as entry'."
        ),
        "lesson": "Return-time questions should answer from entry evidence in ordinary language.",
        "metadata": {
            "seed": "sylv_return_time_plain_language",
            "tags": ["access-logs", "arrival-return", "concise-response"],
        },
    },
    {
        "prompt": "What alerts have been raised/resolved today?",
        "ideal_answer": (
            "Use alert activity only: alerts raised and alerts resolved in today's period. Do not include gate "
            "maintenance, device health, or malfunction summaries unless the user asks for those domains."
        ),
        "lesson": "Raised/resolved alert questions require the alert activity tool and must stay scoped to alerts.",
        "metadata": {
            "seed": "alerts_raised_resolved_today_scope",
            "tags": ["alerts", "scope-control"],
        },
    },
    {
        "prompt": "oil delivery arrive today?",
        "ideal_answer": (
            "Search active and resolved alert evidence for likely delivery matches. If none match, say plainly "
            "that no matching oil delivery alert was found today; do not use cute ledger phrasing."
        ),
        "lesson": "No-match delivery answers should be factual and plain, with no decorative ledger wording.",
        "metadata": {
            "seed": "oil_delivery_no_match_plain",
            "tags": ["alerts", "delivery", "no-match"],
        },
    },
    {
        "prompt": "What time does Steph usually get back?",
        "ideal_answer": (
            "Use arrival/return semantics and source-of-truth access evidence or rhythm summaries. Do not answer "
            "from departure data when the user asks when someone gets back."
        ),
        "lesson": "Usual return questions require arrival/entry evidence, not departure/exit evidence.",
        "metadata": {
            "seed": "steph_usual_return_arrival_semantics",
            "tags": ["access-logs", "arrival-return", "rhythm"],
        },
    },
    {
        "prompt": "what time did Stu leave?",
        "ideal_answer": (
            "Resolve Stu as a visitor if that is the matching source-of-truth record, then answer from the visitor "
            "pass departure evidence as a first-class access subject."
        ),
        "lesson": "Visitor pass subjects must be treated as first-class access subjects for arrival and departure answers.",
        "metadata": {
            "seed": "stu_visitor_departure_path",
            "tags": ["visitor-passes", "access-logs", "departure"],
        },
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
    def schedule_reflection(
        self,
        provider: Any,
        *,
        user_message: str,
        assistant_text: str,
        tool_results: list[dict[str, Any]],
        actor_context: dict[str, Any],
        session_id: str,
        provider_name: str,
        model_name: str | None,
    ) -> None:
        try:
            task = asyncio.create_task(
                self.reflect_on_turn(
                    provider,
                    user_message=user_message,
                    assistant_text=assistant_text,
                    tool_results=tool_results,
                    actor_context=actor_context,
                    session_id=session_id,
                    provider_name=provider_name,
                    model_name=model_name,
                )
            )
        except RuntimeError:
            logger.info("alfred_reflection_not_scheduled", extra={"session_id": session_id})
            return
        task.add_done_callback(self._log_reflection_failure)

    def _log_reflection_failure(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.info("alfred_reflection_failed", extra={"error": str(exc)[:240]})

    async def reflect_on_turn(
        self,
        provider: Any,
        *,
        user_message: str,
        assistant_text: str,
        tool_results: list[dict[str, Any]],
        actor_context: dict[str, Any],
        session_id: str,
        provider_name: str,
        model_name: str | None,
    ) -> dict[str, Any] | None:
        """Draft one generalized lesson from a completed safe turn, if useful."""

        runtime = await get_runtime_config()
        if not bool(getattr(runtime, "alfred_reflection_enabled", False)):
            return None
        if _reflection_input_unsafe(user_message, assistant_text, tool_results):
            return None
        user = actor_context.get("user") if isinstance(actor_context, dict) else {}
        actor_uuid = _coerce_uuid((user or {}).get("id"))
        actor_role = str((user or {}).get("role") or "standard").strip().lower()
        if not actor_uuid:
            return None
        try:
            result = await _provider_complete(
                provider,
                [
                    ChatMessageInput("system", TURN_REFLECTION_PROMPT),
                    ChatMessageInput(
                        "user",
                        json.dumps(
                            {
                                "user_message": user_message,
                                "assistant_text": assistant_text,
                                "tool_names": _tool_names_for_reflection(tool_results),
                                "tool_result_count": len(tool_results),
                                "actor_role": actor_role,
                                "provider": provider_name,
                                "model": model_name,
                            },
                            separators=(",", ":"),
                            default=str,
                        ),
                    ),
                ],
                model_name=model_name,
            )
        except Exception as exc:
            logger.info("alfred_reflection_provider_failed", extra={"error": str(exc)[:180]})
            return None
        payload = _first_json_object(result.text)
        reflection = payload.get("reflection") if isinstance(payload, dict) else None
        if not isinstance(reflection, dict):
            return None
        lesson_text = str(reflection.get("key_lesson") or "").strip()
        if not lesson_text or _contains_placeholder(lesson_text) or _reflection_text_unsafe(lesson_text):
            return None
        tags = reflection.get("tags") if isinstance(reflection.get("tags"), list) else []
        analysis = sanitize_payload(
            {
                "summary": "Post-turn reflection lesson drafted.",
                "reflection": {
                    "went_well": str(reflection.get("went_well") or "")[:300],
                    "could_improve": str(reflection.get("could_improve") or "")[:300],
                },
                "lesson": {
                    "title": str(reflection.get("title") or "Alfred reflection lesson").strip()[:180],
                    "lesson": lesson_text[:2000],
                    "tags": [str(tag).strip()[:40] for tag in tags[:10] if str(tag).strip()] + ["reflection"],
                    "confidence": reflection.get("confidence", 0.55),
                },
            }
        )
        return await self._create_lesson_from_analysis(
            feedback_id=f"reflection:{session_id}",
            actor_uuid=actor_uuid,
            actor_role=actor_role,
            rating="reflection",
            learning_mode=str(getattr(runtime, "alfred_learning_mode", "review_then_learn")),
            analysis=analysis,
        )

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

    async def seed_default_eval_examples(self) -> None:
        async with AsyncSessionLocal() as session:
            for item in DEFAULT_SEEDED_EVAL_EXAMPLES:
                existing = await session.scalar(
                    select(AlfredEvalExample)
                    .where(AlfredEvalExample.scope == "site")
                    .where(AlfredEvalExample.prompt == item["prompt"])
                )
                if existing:
                    continue
                session.add(
                    AlfredEvalExample(
                        feedback_id=None,
                        scope="site",
                        prompt=item["prompt"],
                        bad_answer=None,
                        ideal_answer=item["ideal_answer"],
                        corrected_answer=None,
                        lesson=item["lesson"],
                        metadata_=sanitize_payload(item.get("metadata") or {}),
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
        rows = [row for row in rows if not _lesson_text_unsafe_for_learning(row.lesson)]
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
        feedback_embedding = await generate_embedding(
            _feedback_embedding_text(feedback),
            purpose="alfred_feedback",
        )
        learning_mode = runtime.alfred_learning_mode
        source_channel = feedback.source_channel
        provider_name = feedback.provider or runtime.llm_provider
        model_name = feedback.model or ""
        role = (feedback.actor_role or "standard").strip().lower()
        actor_uuid = feedback.actor_user_id

        analysis = await self._analyze_feedback(
            runtime_provider=runtime.llm_provider,
            model_name=str(getattr(runtime, "alfred_background_model", "") or "").strip() or None,
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
                if row.embedding is None:
                    row.embedding = feedback_embedding
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
            user_labels = await self._user_label_map(session, (row.actor_user_id for row in rows))
        return [
            self._public_feedback(row) | {"source": _feedback_training_source(row, user_labels)}
            for row in rows
        ]

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
            feedback_rows = await self._source_feedback_rows(session, rows)
            user_labels = await self._user_label_map(
                session,
                [
                    *(getattr(row, "created_by_user_id", None) for row in rows),
                    *(getattr(row, "owner_user_id", None) for row in rows),
                    *(row.actor_user_id for row in feedback_rows),
                ],
            )
        feedback_by_id = {str(row.id): row for row in feedback_rows}
        return [
            self._public_lesson(row, include_owner=True)
            | {"source": _lesson_training_source(row, feedback_by_id, user_labels)}
            for row in rows
        ]

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
            refresh_embedding = False
            if title is not None:
                row.title = title.strip()[:180] or row.title
                row.edited_by_user_id = reviewer.id
                refresh_embedding = True
            if lesson_text is not None:
                row.lesson = lesson_text.strip() or row.lesson
                row.edited_by_user_id = reviewer.id
                refresh_embedding = True
            if refresh_embedding:
                row.embedding = await generate_embedding(
                    embedding_text(row.title, row.lesson, " ".join(row.tags or [])),
                    purpose="alfred_lesson_review",
                )
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
            feedback_rows = await self._feedback_rows_by_ids(
                session,
                (row.feedback_id for row in rows if row.feedback_id),
            )
            user_labels = await self._user_label_map(session, (row.actor_user_id for row in feedback_rows))
        feedback_by_id = {str(row.id): row for row in feedback_rows}
        return [
            self._public_eval(row) | {"source": _eval_training_source(row, feedback_by_id, user_labels)}
            for row in rows
        ]

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
        model_name: str | None = None,
        rating: str,
        reason: str,
        ideal_answer: str,
        original_prompt: str,
        original_answer: str,
        turn_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            provider = get_llm_provider(runtime_provider)
            result = await _provider_complete(
                provider,
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
                ],
                model_name=model_name,
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
        unsafe_lesson = _lesson_text_unsafe_for_learning(lesson_text)
        if unsafe_lesson:
            status = "rejected"
        elif rating == "reflection":
            status = "pending"
        else:
            status = "active" if learning_mode == "auto_learn" else "pending"
        if scope == "site" and actor_role != "admin":
            scope = "user"
        now = datetime.now(tz=UTC)
        try:
            confidence = max(0.0, min(1.0, float(raw_lesson.get("confidence", 0.6))))
        except (TypeError, ValueError):
            confidence = 0.6
        tags = [
            str(tag).strip()[:40]
            for tag in (raw_lesson.get("tags") if isinstance(raw_lesson.get("tags"), list) else [])[:12]
            if str(tag).strip()
        ]
        if unsafe_lesson and "quarantined" not in tags:
            tags.append("quarantined")
        title = str(raw_lesson.get("title") or "Alfred feedback lesson").strip()[:180]
        embedding = await generate_embedding(
            embedding_text(title, lesson_text, " ".join(tags)),
            purpose="alfred_lesson",
        )
        async with AsyncSessionLocal() as session:
            row = AlfredLesson(
                scope=scope,
                owner_user_id=None if scope == "site" else actor_uuid,
                title=title,
                lesson=lesson_text[:2000],
                tags=tags,
                source_feedback_ids=[feedback_id],
                confidence=confidence,
                embedding=embedding,
                status=status,
                created_by_user_id=actor_uuid,
                approved_by_user_id=actor_uuid if status == "active" and actor_role == "admin" else None,
                approved_at=now if status == "active" and actor_role == "admin" else None,
                active_at=now if status == "active" else None,
                rejected_by_user_id=actor_uuid if unsafe_lesson else None,
                rejected_at=now if unsafe_lesson else None,
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
        embedding = await generate_embedding(
            embedding_text(prompt, safe_ideal_answer, safe_corrected_answer, lesson_text),
            purpose="alfred_eval_example",
        )
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
                embedding=embedding,
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

    async def _source_feedback_rows(
        self,
        session: Any,
        lessons: Iterable[AlfredLesson],
    ) -> list[AlfredFeedback]:
        feedback_ids: set[uuid.UUID] = set()
        for lesson in lessons:
            for source_id in _source_feedback_values(lesson):
                feedback_id = _coerce_uuid(source_id)
                if feedback_id:
                    feedback_ids.add(feedback_id)
        return await self._feedback_rows_by_ids(session, feedback_ids)

    async def _feedback_rows_by_ids(self, session: Any, feedback_ids: Iterable[Any]) -> list[AlfredFeedback]:
        ids = {feedback_id for feedback_id in (_coerce_uuid(value) for value in feedback_ids) if feedback_id}
        if not ids:
            return []
        return (
            await session.scalars(
                select(AlfredFeedback).where(AlfredFeedback.id.in_(ids))
            )
        ).all()

    async def _user_label_map(self, session: Any, user_ids: Iterable[Any]) -> dict[str, str]:
        ids = {user_id for user_id in (_coerce_uuid(value) for value in user_ids) if user_id}
        if not ids:
            return {}
        rows = (await session.scalars(select(User).where(User.id.in_(ids)))).all()
        return {str(row.id): _user_display_name(row) for row in rows}


def _feedback_training_source(row: AlfredFeedback, user_labels: dict[str, str]) -> dict[str, Any]:
    actor_id = str(row.actor_user_id) if getattr(row, "actor_user_id", None) else None
    return _training_source_payload(
        kind="user_feedback",
        label=user_labels.get(actor_id or "", "") or "Unknown user",
        detail=_source_channel_label(getattr(row, "source_channel", None)),
        channel=getattr(row, "source_channel", None),
        actor_user_id=actor_id,
    )


def _lesson_training_source(
    row: AlfredLesson,
    feedback_by_id: dict[str, AlfredFeedback],
    user_labels: dict[str, str],
) -> dict[str, Any]:
    source_ids = _source_feedback_values(row)
    actor_id = str(row.created_by_user_id) if getattr(row, "created_by_user_id", None) else None
    actor_label = user_labels.get(actor_id or "", "")
    if any(str(source_id).startswith("reflection:") for source_id in source_ids):
        return _training_source_payload(
            kind="self_learning",
            label="Alfred Self Learning",
            detail=f"from {actor_label}'s chat" if actor_label else "Post-turn reflection",
            actor_user_id=actor_id,
        )

    for source_id in source_ids:
        feedback_id = _coerce_uuid(source_id)
        if not feedback_id:
            continue
        feedback = feedback_by_id.get(str(feedback_id))
        if feedback:
            return _feedback_training_source(feedback, user_labels)

    if actor_id:
        return _training_source_payload(
            kind="manual_training",
            label=actor_label or "Unknown user",
            detail="Manual training",
            actor_user_id=actor_id,
        )

    if not source_ids:
        return _training_source_payload(kind="seed", label="Alfred Seed Data", detail="Built-in lesson")

    return _training_source_payload(kind="system", label="Alfred Training", detail="Unknown source")


def _eval_training_source(
    row: AlfredEvalExample,
    feedback_by_id: dict[str, AlfredFeedback],
    user_labels: dict[str, str],
) -> dict[str, Any]:
    feedback = feedback_by_id.get(str(row.feedback_id)) if getattr(row, "feedback_id", None) else None
    if feedback:
        return _feedback_training_source(feedback, user_labels)
    metadata = row.metadata_ if isinstance(getattr(row, "metadata_", None), dict) else {}
    if metadata.get("seed"):
        return _training_source_payload(kind="seed", label="Alfred Seed Data", detail="Built-in eval")
    source_channel = metadata.get("source_channel")
    if source_channel:
        return _training_source_payload(
            kind="system",
            label="Alfred Training",
            detail=_source_channel_label(str(source_channel)),
            channel=str(source_channel),
        )
    return _training_source_payload(kind="system", label="Alfred Training", detail="Generated eval")


def _source_feedback_values(row: Any) -> list[str]:
    values = getattr(row, "source_feedback_ids", []) or []
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value).strip()]


def _training_source_payload(
    *,
    kind: str,
    label: str,
    detail: str | None = None,
    channel: str | None = None,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": str(label or "Unknown source").strip()[:160] or "Unknown source",
        "detail": str(detail or "").strip()[:160] or None,
        "channel": str(channel or "").strip()[:40] or None,
        "actor_user_id": actor_user_id,
    }


def _source_channel_label(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"dashboard", "ui", "web"}:
        return "UI"
    if normalized in {"whatsapp", "whatsapp_cloud"}:
        return "WhatsApp"
    if normalized == "discord":
        return "Discord"
    if normalized == "alfred":
        return "Alfred"
    if not normalized:
        return "Unknown source"
    return re.sub(r"[_-]+", " ", normalized).strip().title()


def _user_display_name(user: User) -> str:
    return (
        str(getattr(user, "full_name", "") or "").strip()
        or " ".join(
            part
            for part in [
                str(getattr(user, "first_name", "") or "").strip(),
                str(getattr(user, "last_name", "") or "").strip(),
            ]
            if part
        )
        or str(getattr(user, "username", "") or "").strip()
        or "Unknown user"
    )


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


def _feedback_embedding_text(feedback: AlfredFeedback) -> str:
    return embedding_text(
        feedback.original_user_prompt,
        feedback.original_assistant_response,
        feedback.reason or "",
        feedback.ideal_answer or "",
    )


def _tool_names_for_reflection(tool_results: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("name") or "")[:120] for item in tool_results[:12] if isinstance(item, dict)]


def _reflection_input_unsafe(
    user_message: str,
    assistant_text: str,
    tool_results: list[dict[str, Any]],
) -> bool:
    text_value = json.dumps(
        {
            "user_message": user_message,
            "assistant_text": assistant_text,
            "tool_results": tool_results[:12],
        },
        default=str,
    ).lower()
    return _reflection_text_unsafe(text_value)


def _reflection_text_unsafe(value: str) -> bool:
    lowered = str(value or "").lower()
    return any(
        marker in lowered
        for marker in (
            "api_key",
            "apikey",
            "secret",
            "password",
            "token",
            "bearer ",
            "cookie",
            "set-cookie",
            "raw_payload",
            "private key",
        )
    )


def _lesson_text_unsafe_for_learning(value: str) -> bool:
    lowered = str(value or "").lower()
    if _reflection_text_unsafe(lowered):
        return True
    time_markers = ("time zone", "timezone", "utc offset", "gmt", "bst")
    instruction_markers = ("mention", "state", "include", "explicit", "label", "show", "clarify")
    prohibition_markers = (
        "do not mention",
        "never mention",
        "do not include",
        "never include",
        "do not state",
        "never state",
        "avoid mention",
        "avoid mentioning",
    )
    if any(marker in lowered for marker in time_markers) and any(marker in lowered for marker in prohibition_markers):
        return False
    return any(marker in lowered for marker in time_markers) and any(
        marker in lowered for marker in instruction_markers
    )


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


async def _provider_complete(provider: Any, messages: list[ChatMessageInput], *, model_name: str | None) -> Any:
    return await complete_with_provider_options(
        provider,
        messages,
        model=model_name,
        max_output_tokens=700,
        request_purpose="alfred.feedback_reflection",
    )


alfred_feedback_service = AlfredFeedbackService()
