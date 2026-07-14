from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.services.investigations.contracts import DispatchState, Outcome


COMMAND_AUDIT_PREFIXES = (
    "access_device.command",
    "cover.command",
    "garage_door.close",
    "garage_door.open",
    "gate.close",
    "gate.command",
    "gate.open",
)
COMMAND_SIGNAL_KEYS = (
    "accepted",
    "command_id",
    "command_sent",
    "dispatch_state",
    "mechanically_confirmed",
    "requires_reconciliation",
    "sent",
    "verified",
)


@dataclass(frozen=True)
class EpisodeAssessment:
    outcome: Outcome
    dispatch_state: DispatchState
    reason_code: str
    reason: str
    certainty: str = "high"


def assess_episode(
    *,
    trace: Mapping[str, Any] | None = None,
    audits: Sequence[Mapping[str, Any]] = (),
    automation: Mapping[str, Any] | None = None,
    gate_commands: Sequence[Mapping[str, Any]] = (),
) -> EpisodeAssessment:
    state_observation = _standalone_state_observation(
        trace=trace,
        automation=automation,
        audits=audits,
        gate_commands=gate_commands,
    )
    if state_observation:
        return state_observation

    records: list[Any] = [record for record in (trace, automation) if record]
    records.extend(audits)
    records.extend(gate_commands)
    values = list(_walk(records))

    command_records = _command_evidence(
        trace=trace,
        audits=audits,
        automation=automation,
        gate_commands=gate_commands,
    )
    command_values = list(_walk(command_records))
    command_states = {
        state for record in command_records if (state := _command_dispatch_state(record))
    }

    condition = _failed_condition(automation)
    schedule_denied = _truthy_key(values, "schedule_denied") or _contains_value(
        values,
        {
            "outside_schedule",
            "schedule_denied",
            "schedule_not_allowed",
            "schedule_outside_window",
            "default_policy_denied",
            "schedule_not_found",
        },
    )
    action_results = _sequence(_mapping_value(automation, "action_results"))
    action_skipped = any(_normalized(item.get("status")) == "skipped" for item in action_results if isinstance(item, Mapping))
    automation_status = _normalized(_mapping_value(automation, "status"))
    skipped_command = _has_explicit_skipped_command(audits)

    dispatch_state: DispatchState
    if "attempted_rejected" in command_states:
        dispatch_state = "attempted_rejected"
    elif "accepted_unverified" in command_states:
        dispatch_state = "accepted_unverified"
    elif "verified" in command_states:
        dispatch_state = "verified"
    elif schedule_denied or "withheld" in command_states or (condition is not None and not command_records):
        dispatch_state = "withheld"
    elif automation_status == "skipped" or action_skipped or skipped_command:
        dispatch_state = "withheld"
    elif command_records:
        dispatch_state = "unknown"
    else:
        dispatch_state = "not_applicable"

    if schedule_denied:
        return EpisodeAssessment(
            outcome="blocked",
            dispatch_state="withheld",
            reason_code="schedule_not_allowed",
            reason=_schedule_reason(values),
        )
    if condition is not None:
        condition_type = str(condition.get("type") or condition.get("name") or "condition").replace("_", " ")
        detail = _first_text(condition, ("reason", "detail", "message", "description"))
        if not detail:
            details = condition.get("details")
            detail = _first_text(details, ("reason", "detail", "message")) if isinstance(details, Mapping) else ""
        return EpisodeAssessment(
            outcome="blocked",
            dispatch_state="withheld",
            reason_code="condition_failed",
            reason=detail or f"The {condition_type} condition did not pass, so no action was dispatched.",
        )
    if _trace_decision_denied(trace):
        return EpisodeAssessment(
            outcome="blocked",
            dispatch_state="withheld",
            reason_code="access_denied",
            reason=_detail_reason(values) or "IACS denied access, so no device command was sent.",
        )
    if _primary_has_status(trace, automation, audits, {"cancelled", "canceled"}):
        return EpisodeAssessment(
            outcome="cancelled",
            dispatch_state=dispatch_state,
            reason_code="cancelled",
            reason="The activity was cancelled before it completed.",
        )
    if dispatch_state == "accepted_unverified":
        return EpisodeAssessment(
            outcome="pending",
            dispatch_state=dispatch_state,
            reason_code="state_not_confirmed",
            reason=_detail_reason(command_values)
            or "The command was accepted, but IACS did not record the expected resulting device state.",
        )
    if dispatch_state == "attempted_rejected":
        return EpisodeAssessment(
            outcome="failed",
            dispatch_state=dispatch_state,
            reason_code="integration_rejected",
            reason=_detail_reason(command_values)
            or "IACS attempted the command, but the integration or device did not accept it.",
        )
    if _primary_failed(trace, automation, audits):
        return EpisodeAssessment(
            outcome="failed",
            dispatch_state=dispatch_state,
            reason_code="execution_failed",
            reason=_primary_failure_reason(trace, automation, audits)
            or "The activity failed before it completed.",
        )
    if dispatch_state == "verified":
        return EpisodeAssessment(
            outcome="succeeded",
            dispatch_state=dispatch_state,
            reason_code="completed",
            reason="The activity completed successfully.",
        )
    if (
        automation_status == "skipped"
        or action_skipped
        or skipped_command
        or _primary_has_status(trace, automation, audits, {"skipped", "suppressed"})
    ):
        return EpisodeAssessment(
            outcome="skipped",
            dispatch_state=dispatch_state,
            reason_code="intentionally_skipped",
            reason=_detail_reason(values) or "IACS intentionally skipped this activity.",
        )
    if _primary_has_status(
        trace,
        automation,
        audits,
        {"pending", "running", "processing", "leased", "reconciliation_required"},
    ):
        return EpisodeAssessment(
            outcome="pending",
            dispatch_state=dispatch_state,
            reason_code="incomplete",
            reason="The activity has not reached a recorded final outcome.",
        )
    if _primary_has_status(
        trace,
        automation,
        audits,
        {"ok", "success", "succeeded", "completed", "reconciled"},
    ):
        return EpisodeAssessment(
            outcome="succeeded",
            dispatch_state=dispatch_state,
            reason_code="completed",
            reason="The activity completed successfully.",
        )
    return EpisodeAssessment(
        outcome="unknown",
        dispatch_state=dispatch_state,
        reason_code="insufficient_evidence",
        reason="The available evidence does not record a definitive outcome.",
        certainty="low",
    )


def _standalone_state_observation(
    *,
    trace: Mapping[str, Any] | None,
    automation: Mapping[str, Any] | None,
    audits: Sequence[Mapping[str, Any]],
    gate_commands: Sequence[Mapping[str, Any]],
) -> EpisodeAssessment | None:
    if trace or automation or gate_commands or len(audits) != 1:
        return None
    audit = audits[0]
    action = str(audit.get("action") or "").strip().lower()
    if not action.endswith(".state_changed") or _normalized(audit.get("outcome")) not in {
        "ok",
        "success",
        "succeeded",
    }:
        return None
    metadata = audit.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    diff = audit.get("diff")
    diff = diff if isinstance(diff, Mapping) else {}
    new_state = diff.get("new")
    new_state = new_state if isinstance(new_state, Mapping) else {}
    state = str(metadata.get("raw_state") or new_state.get("state") or "unknown").strip()
    target = str(audit.get("target_label") or audit.get("target_entity") or "The device").strip()
    source = str(metadata.get("source") or "IACS").replace("_", " ").strip().title()
    return EpisodeAssessment(
        outcome="succeeded",
        dispatch_state="not_applicable",
        reason_code="state_observed",
        reason=f"{source} reported {target} as {state.replace('_', ' ')}.",
    )


def _command_evidence(
    *,
    trace: Mapping[str, Any] | None,
    audits: Sequence[Mapping[str, Any]],
    automation: Mapping[str, Any] | None,
    gate_commands: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = [record for record in gate_commands if isinstance(record, Mapping)]

    for audit in audits:
        action = str(audit.get("action") or "").strip().lower()
        if not action.startswith(COMMAND_AUDIT_PREFIXES):
            continue
        metadata = audit.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        records.append(
            {
                "audit_action": action,
                "audit_outcome": audit.get("outcome"),
                "audit_level": audit.get("level"),
                **metadata,
            }
        )

    for action in _sequence(_mapping_value(automation, "action_results")):
        if not isinstance(action, Mapping):
            continue
        outcomes = [item for item in _sequence(action.get("outcomes")) if isinstance(item, Mapping)]
        if outcomes:
            records.extend(outcomes)
        elif _mapping_has_command_signal(action):
            records.append(action)

    for candidate in (trace, trace.get("context") if trace else None):
        if not isinstance(candidate, Mapping) or not _mapping_has_command_signal(candidate):
            continue
        records.append(
            {
                key: candidate[key]
                for key in (
                    *COMMAND_SIGNAL_KEYS,
                    "detail",
                    "error",
                    "reason",
                    "reason_code",
                    "state",
                    "status",
                )
                if key in candidate
            }
        )
    return records


def _mapping_has_command_signal(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in COMMAND_SIGNAL_KEYS)


def _command_dispatch_state(record: Mapping[str, Any]) -> DispatchState | None:
    explicit = _normalized(record.get("dispatch_state"))
    aliases: dict[str, DispatchState] = {
        "withheld": "withheld",
        "attempted_rejected": "attempted_rejected",
        "attempted": "attempted_rejected",
        "accepted_unverified": "accepted_unverified",
        "accepted": "accepted_unverified",
        "verified": "verified",
        "not_applicable": "not_applicable",
        "unknown": "unknown",
    }
    if explicit in aliases:
        return aliases[explicit]

    reason_code = _normalized(record.get("reason_code"))
    if (
        record.get("command_sent") is False
        or record.get("sent") is False
        or record.get("schedule_denied") is True
        or reason_code in {"device_disabled", "schedule_denied", "schedule_not_allowed", "schedule_outside_window"}
    ):
        return "withheld"

    state = _normalized(record.get("state"))
    audit_action = _normalized(record.get("audit_action"))
    audit_outcome = _normalized(record.get("audit_outcome") or record.get("outcome"))
    if (
        record.get("mechanically_confirmed") is True
        or record.get("verified") is True
        or state == "reconciled"
        or (".open" in audit_action and state in {"open", "opening"})
        or (".close" in audit_action and state in {"closed", "closing"})
    ):
        return "verified"
    if record.get("accepted") is True or audit_outcome == "accepted":
        return "accepted_unverified"
    if record.get("accepted") is False:
        return "attempted_rejected"
    if state in {"failed", "failure", "provider_rejected", "rejected"} or audit_outcome in {
        "error",
        "failed",
        "failure",
        "rejected",
    }:
        return "attempted_rejected"
    if audit_outcome in {"skipped", "suppressed"}:
        return "withheld"
    return None


def _primary_records(
    trace: Mapping[str, Any] | None,
    automation: Mapping[str, Any] | None,
    audits: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    records = [record for record in (trace, automation) if isinstance(record, Mapping)]
    if not records:
        records.extend(record for record in audits if isinstance(record, Mapping))
    return records


def _primary_has_status(
    trace: Mapping[str, Any] | None,
    automation: Mapping[str, Any] | None,
    audits: Sequence[Mapping[str, Any]],
    expected: set[str],
) -> bool:
    return any(
        _normalized(record.get(key)) in expected
        for record in _primary_records(trace, automation, audits)
        for key in ("status", "outcome", "state")
        if key in record
    )


def _primary_failed(
    trace: Mapping[str, Any] | None,
    automation: Mapping[str, Any] | None,
    audits: Sequence[Mapping[str, Any]],
) -> bool:
    records = _primary_records(trace, automation, audits)
    return _primary_has_status(trace, automation, audits, {"error", "failed", "failure"}) or any(
        bool(record.get("error")) for record in records
    )


def _primary_failure_reason(
    trace: Mapping[str, Any] | None,
    automation: Mapping[str, Any] | None,
    audits: Sequence[Mapping[str, Any]],
) -> str:
    for record in _primary_records(trace, automation, audits):
        detail = _first_text(record, ("error", "detail", "message", "reason"))
        if detail:
            return detail
        metadata = record.get("metadata")
        if isinstance(metadata, Mapping):
            detail = _first_text(metadata, ("error", "detail", "message", "reason"))
            if detail:
                return detail
    return ""


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key).lower(), item
            yield from _walk(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _walk(item)


def _normalized(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value or "").strip().lower().replace(" ", "_")


def _truthy_key(values: Sequence[tuple[str, Any]], key: str) -> bool:
    return any(item_key == key and item is True for item_key, item in values)


def _contains_value(values: Sequence[tuple[str, Any]], expected: set[str]) -> bool:
    return any(_normalized(item) in expected for _, item in values if not isinstance(item, (Mapping, list, tuple)))


def _trace_decision_denied(trace: Mapping[str, Any] | None) -> bool:
    if not trace:
        return False
    context = trace.get("context")
    context_decision = context.get("decision") if isinstance(context, Mapping) else None
    return _normalized(trace.get("decision") or context_decision) == "denied"


def _has_explicit_skipped_command(audits: Sequence[Mapping[str, Any]]) -> bool:
    prefixes = ("gate.", "garage_door.", "access_device.command")
    return any(
        _normalized(audit.get("outcome")) in {"skipped", "suppressed"}
        and str(audit.get("action") or "").startswith(prefixes)
        for audit in audits
    )


def _mapping_value(value: Mapping[str, Any] | None, key: str) -> Any:
    return value.get(key) if value else None


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _failed_condition(automation: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    results = _sequence(_mapping_value(automation, "condition_results"))
    for result in results:
        if isinstance(result, Mapping) and result.get("passed") is False:
            return result
    return None


def _first_text(value: Mapping[str, Any] | None, keys: Sequence[str]) -> str:
    if not value:
        return ""
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _detail_reason(values: Sequence[tuple[str, Any]]) -> str:
    for preferred in ("detail", "reason", "error", "message", "description"):
        for key, item in values:
            if key == preferred and isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _schedule_reason(values: Sequence[tuple[str, Any]]) -> str:
    detail = _detail_reason(values)
    if detail:
        return detail
    schedule_name = next(
        (str(item).strip() for key, item in values if key == "schedule_name" and str(item).strip()),
        "the permitted schedule",
    )
    return f"The action was outside {schedule_name}, so IACS did not send a command."
