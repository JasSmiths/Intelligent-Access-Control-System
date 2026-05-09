"""Structured answer contracts for Alfred critical-domain replies."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


AnswerDomain = Literal["access_logs", "alerts", "visitor_passes", "schedules", "general"]


class AnswerFact(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    value: Any = None
    display_value: str | None = None
    kind: str = "text"
    source: str | None = None
    must_appear: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnswerArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    domain: AnswerDomain = "general"
    answer_type: str
    subject_label: str | None = None
    primary_fact: AnswerFact | None = None
    supporting_facts: list[AnswerFact] = Field(default_factory=list)
    time_scope: dict[str, Any] = Field(default_factory=dict)
    source_records: list[dict[str, Any]] = Field(default_factory=list)
    uncertainty: str | None = None
    display: dict[str, Any] = Field(default_factory=dict)
    fallback_text: str | None = None


class AnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_text: str
    fact_ids_used: list[str] = Field(default_factory=list)
    style: str = "natural_concise"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_question: str | None = None


class AnswerVerifierResult(BaseModel):
    approved: bool
    reasons: list[str] = Field(default_factory=list)
    repair_count: int = 0
    fallback_required: bool = False


ANSWER_DRAFT_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "alfred_answer_draft",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer_text": {"type": "string"},
            "fact_ids_used": {
                "type": "array",
                "items": {"type": "string"},
            },
            "style": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "needs_clarification": {"type": "boolean"},
            "clarification_question": {"type": ["string", "null"]},
        },
        "required": [
            "answer_text",
            "fact_ids_used",
            "style",
            "confidence",
            "needs_clarification",
            "clarification_question",
        ],
    },
}


def artifact_payload(
    *,
    domain: AnswerDomain,
    answer_type: str,
    subject_label: str | None = None,
    primary_fact: dict[str, Any] | AnswerFact | None = None,
    supporting_facts: list[dict[str, Any] | AnswerFact] | None = None,
    time_scope: dict[str, Any] | None = None,
    source_records: list[dict[str, Any]] | None = None,
    uncertainty: str | None = None,
    display: dict[str, Any] | None = None,
    fallback_text: str | None = None,
) -> dict[str, Any]:
    artifact = AnswerArtifact(
        domain=domain,
        answer_type=answer_type,
        subject_label=subject_label,
        primary_fact=_fact(primary_fact),
        supporting_facts=[_fact(item) for item in (supporting_facts or []) if _fact(item) is not None],
        time_scope=time_scope or {},
        source_records=source_records or [],
        uncertainty=uncertainty,
        display=display or {},
        fallback_text=fallback_text,
    )
    return artifact.model_dump(mode="json", exclude_none=True)


def extract_answer_artifacts(tool_results: list[dict[str, Any]]) -> list[AnswerArtifact]:
    artifacts: list[AnswerArtifact] = []
    for result in tool_results:
        output = result.get("output") if isinstance(result.get("output"), dict) else {}
        for payload in _artifact_candidates(output):
            try:
                artifact = AnswerArtifact.model_validate(payload)
            except ValidationError:
                continue
            artifacts.append(artifact)
    return artifacts


def select_answer_artifacts(artifacts: list[AnswerArtifact]) -> list[AnswerArtifact]:
    if not artifacts:
        return []
    scored = [(_artifact_priority(artifact), index, artifact) for index, artifact in enumerate(artifacts)]
    top_score = max(score for score, _index, _artifact in scored)
    selected = [(index, artifact) for score, index, artifact in scored if score == top_score]
    domains = {artifact.domain for _index, artifact in selected}
    answer_types = {artifact.answer_type for _index, artifact in selected}
    if len(domains) == 1 and len(answer_types) <= 2:
        return [artifact for _index, artifact in sorted(selected, key=lambda item: item[0])]
    first_index, first_artifact = min(selected, key=lambda item: item[0])
    return [first_artifact]


def rendered_answer_draft(text: str, artifacts: list[AnswerArtifact]) -> AnswerDraft:
    return AnswerDraft(
        answer_text=text,
        fact_ids_used=[
            fact.id
            for artifact in artifacts
            for fact in [artifact.primary_fact, *artifact.supporting_facts]
            if fact and fact.must_appear
        ],
        style="safe_renderer",
        confidence=1.0,
        needs_clarification=False,
        clarification_question=None,
    )


def parse_answer_draft(text: str) -> AnswerDraft | None:
    payload = _first_json_object(text)
    if not isinstance(payload, dict):
        return None
    try:
        return AnswerDraft.model_validate(payload)
    except ValidationError:
        return None


def verify_answer_draft(draft: AnswerDraft | None, artifacts: list[AnswerArtifact]) -> AnswerVerifierResult:
    if draft is None:
        return AnswerVerifierResult(
            approved=False,
            reasons=["Composer did not return valid answer JSON."],
            fallback_required=True,
        )
    text = draft.answer_text.strip()
    if not text and not draft.needs_clarification:
        return AnswerVerifierResult(
            approved=False,
            reasons=["Composer returned an empty answer."],
            fallback_required=True,
        )
    fact_map = _facts_by_id(artifacts)
    used_ids = set(draft.fact_ids_used or [])
    unknown_ids = sorted(fact_id for fact_id in used_ids if fact_id not in fact_map)
    reasons: list[str] = []
    if unknown_ids:
        reasons.append(f"Composer cited facts not present in the answer artifact: {', '.join(unknown_ids)}.")

    required_ids = {
        fact.id
        for artifact in artifacts
        for fact in [artifact.primary_fact, *artifact.supporting_facts]
        if fact and fact.must_appear
    }
    missing_ids = sorted(required_ids - used_ids)
    if missing_ids:
        reasons.append(f"Composer omitted required fact IDs: {', '.join(missing_ids)}.")

    for fact_id in sorted(required_ids & used_ids):
        fact = fact_map[fact_id]
        display_value = str(fact.display_value or fact.value or "").strip()
        if display_value and display_value not in text:
            reasons.append(f"Required fact {fact_id} display value was not present in the answer text.")

    if draft.needs_clarification and not (draft.clarification_question or "").strip():
        reasons.append("Composer asked for clarification without a question.")

    return AnswerVerifierResult(
        approved=not reasons,
        reasons=reasons,
        fallback_required=bool(reasons),
    )


def render_answer_from_artifacts(artifacts: list[AnswerArtifact]) -> str:
    artifacts = select_answer_artifacts(artifacts)
    if not artifacts:
        return "I checked the system, but I need to summarize the result before showing it."
    if len(artifacts) == 1:
        return render_answer_artifact(artifacts[0])
    alert_artifacts = [artifact for artifact in artifacts if artifact.domain == "alerts"]
    if len(alert_artifacts) == len(artifacts):
        return render_alert_activity(alert_artifacts)
    return " ".join(render_answer_artifact(artifact) for artifact in artifacts[:3]).strip()


def render_answer_artifact(artifact: AnswerArtifact) -> str:
    if artifact.fallback_text:
        return artifact.fallback_text.strip()
    if artifact.domain == "access_logs":
        return _render_access_artifact(artifact)
    if artifact.domain == "alerts":
        return _render_alert_artifact(artifact)
    if artifact.domain == "visitor_passes":
        return _render_visitor_artifact(artifact)
    if artifact.domain == "schedules":
        return _render_schedule_artifact(artifact)
    subject = artifact.subject_label or "That"
    fact = artifact.primary_fact
    if fact and (fact.display_value or fact.value is not None):
        return f"{subject}: {fact.display_value or fact.value}."
    return "I checked, but I could not find a matching record."


def answer_artifacts_for_prompt(artifacts: list[AnswerArtifact]) -> list[dict[str, Any]]:
    return [artifact.model_dump(mode="json", exclude_none=True) for artifact in artifacts]


def renderer_fact_payload(fact: AnswerFact | None) -> dict[str, Any] | None:
    return fact.model_dump(mode="json", exclude_none=True) if fact else None


def _fact(value: dict[str, Any] | AnswerFact | None) -> AnswerFact | None:
    if value is None:
        return None
    if isinstance(value, AnswerFact):
        return value
    return AnswerFact.model_validate(value)


def _artifact_candidates(output: dict[str, Any]) -> list[Any]:
    value = output.get("answer_artifacts")
    if isinstance(value, list):
        return value
    value = output.get("answer_artifact")
    if isinstance(value, dict):
        return [value]
    return []


def _facts_by_id(artifacts: list[AnswerArtifact]) -> dict[str, AnswerFact]:
    facts: dict[str, AnswerFact] = {}
    for artifact in artifacts:
        for fact in [artifact.primary_fact, *artifact.supporting_facts]:
            if fact:
                facts[fact.id] = fact
    return facts


def _artifact_priority(artifact: AnswerArtifact) -> int:
    value = artifact.primary_fact.value if artifact.primary_fact else None
    if artifact.answer_type.endswith("_empty") or artifact.answer_type.startswith("no_") or value == 0:
        empty_penalty = 50
    else:
        empty_penalty = 0
    if artifact.domain == "access_logs":
        if artifact.answer_type in {"absence_duration", "visit_duration"}:
            return 130 - empty_penalty
        if artifact.answer_type in {"latest_departure", "latest_arrival", "access_event"}:
            return 120 - empty_penalty
        return 80 - empty_penalty
    if artifact.domain == "alerts":
        return 110 - empty_penalty
    if artifact.domain == "visitor_passes":
        if artifact.answer_type in {"visitor_departure", "visitor_arrival", "visitor_duration"}:
            return 100 - empty_penalty
        return 75 - empty_penalty
    if artifact.domain == "schedules":
        return 90 - empty_penalty
    return 60 - empty_penalty


def _render_access_artifact(artifact: AnswerArtifact) -> str:
    subject = artifact.subject_label or "The matched subject"
    fact = artifact.primary_fact
    display = fact.display_value if fact else ""
    if artifact.answer_type == "absence_duration":
        interval = artifact.source_records[0] if artifact.source_records else {}
        left_at = str(interval.get("left_at") or "")
        returned_at = str(interval.get("returned_at") or "")
        if str(interval.get("returned_at") or "") == "still_away":
            suffix = f" since {left_at}" if left_at else ""
            return f"{subject} has been out for {display}{suffix}. Still away, so the clock is still running."
        if left_at and returned_at:
            return f"{subject} was out for {display}, from {left_at} to {returned_at}."
        return f"{subject} was out for {display}."
    if artifact.answer_type in {"latest_departure", "latest_arrival", "access_event"}:
        verb = str(artifact.display.get("verb") or "was recorded")
        return f"{subject} {verb} at {display}." if display else f"{subject} {verb} recently."
    if artifact.answer_type == "no_access_match":
        return f"I couldn't find any matching access events for {subject}."
    return f"{subject}: {display}." if display else "I checked the access logs, but could not find a matching record."


def _render_alert_artifact(artifact: AnswerArtifact) -> str:
    fact = artifact.primary_fact
    display = fact.display_value if fact else ""
    if artifact.answer_type == "alert_activity_empty":
        scope = str(artifact.time_scope.get("label") or "that period")
        return f"No alerts were raised or resolved {scope}."
    if artifact.answer_type == "alert_activity":
        return display or "I found alert activity."
    return display or "I checked the alerts."


def render_alert_activity(artifacts: list[AnswerArtifact]) -> str:
    texts = [render_answer_artifact(artifact) for artifact in artifacts if render_answer_artifact(artifact)]
    return " ".join(texts) if texts else "I checked the alerts and found nothing matching that."


def _render_visitor_artifact(artifact: AnswerArtifact) -> str:
    subject = artifact.subject_label or "The visitor"
    fact = artifact.primary_fact
    display = fact.display_value if fact else ""
    if artifact.answer_type == "visitor_pass_empty":
        return f"I couldn't find a matching visitor pass for {subject}."
    if artifact.answer_type == "visitor_departure":
        return f"{subject} left at {display}." if display else f"{subject} has no recorded departure yet."
    if artifact.answer_type == "visitor_arrival":
        return f"{subject} arrived at {display}." if display else f"{subject} has no recorded arrival yet."
    if artifact.answer_type == "visitor_duration":
        return f"{subject} was on site for {display}." if display else f"{subject}'s visit has no complete duration yet."
    return f"{subject}: {display}." if display else f"I found {subject}'s visitor pass."


def _render_schedule_artifact(artifact: AnswerArtifact) -> str:
    subject = artifact.subject_label or "That schedule"
    fact = artifact.primary_fact
    display = fact.display_value if fact else ""
    return display or f"I checked {subject}'s schedule."


def _first_json_object(text: str) -> Any:
    decoder = json.JSONDecoder()
    raw = text.strip()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw[index:])
            return value
        except json.JSONDecodeError:
            continue
    return None


def timestamp_id(prefix: str) -> str:
    now = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}_{now}"
