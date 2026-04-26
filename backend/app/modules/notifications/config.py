from copy import deepcopy
from typing import Any


CHANNEL_IDS = {"mobile", "in_app", "voice"}
LEGACY_GROUPED_RULE_IDS = {"critical-access-alerts", "schedule-and-presence-warnings"}


NOTIFICATION_VARIABLES: list[dict[str, str]] = [
    {"token": "[FirstName]", "label": "First name", "source": "Person"},
    {"token": "[FirstNamePossessive]", "label": "First name possessive", "source": "Person"},
    {"token": "[ObjectPronoun]", "label": "Object pronoun", "source": "Person"},
    {"token": "[PossessiveDeterminer]", "label": "Possessive pronoun", "source": "Person"},
    {"token": "[LastName]", "label": "Last name", "source": "Person"},
    {"token": "[DisplayName]", "label": "Display name", "source": "Person"},
    {"token": "[GroupName]", "label": "Group name", "source": "Person"},
    {"token": "[VehicleRegistrationNumber]", "label": "Registration number", "source": "Vehicle"},
    {"token": "[VehicleDisplayName]", "label": "Friendly vehicle name", "source": "Vehicle"},
    {"token": "[VehicleMake]", "label": "Vehicle make", "source": "Vehicle"},
    {"token": "[VehicleModel]", "label": "Vehicle model", "source": "Vehicle"},
    {"token": "[VehicleColor]", "label": "Vehicle colour", "source": "Vehicle"},
    {"token": "[Direction]", "label": "Entry or exit direction", "source": "Event"},
    {"token": "[Decision]", "label": "Access decision", "source": "Event"},
    {"token": "[TimingClassification]", "label": "Timing classification", "source": "Event"},
    {"token": "[Source]", "label": "LPR source", "source": "Event"},
    {"token": "[Severity]", "label": "Severity", "source": "Event"},
    {"token": "[EventType]", "label": "Event type", "source": "Event"},
    {"token": "[Subject]", "label": "Notification subject", "source": "Event"},
    {"token": "[Message]", "label": "Event message", "source": "Event"},
    {"token": "[OccurredAt]", "label": "Event time", "source": "Event"},
    {"token": "[GarageDoor]", "label": "Garage door name", "source": "Integration"},
    {"token": "[EntityId]", "label": "Integration entity ID", "source": "Integration"},
]


def _event_rule(
    rule_id: str,
    name: str,
    event_type: str,
    severity: str,
    title_template: str,
    body_template: str,
    *,
    voice_body: str,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "name": name,
        "description": f"Notification sent for {name.lower()} events.",
        "enabled": True,
        "event_types": [event_type],
        "severities": [severity],
        "channels": {
            "mobile": {
                "enabled": True,
                "endpoint_ids": ["apprise:*"],
                "title_template": title_template,
                "body_template": body_template,
                "snapshot": {"enabled": False, "camera_id": ""},
            },
            "in_app": {
                "enabled": True,
                "endpoint_ids": ["dashboard"],
                "title_template": title_template,
                "body_template": body_template,
                "snapshot": {"enabled": False, "camera_id": ""},
            },
            "voice": {
                "enabled": False,
                "endpoint_ids": [],
                "title_template": "",
                "body_template": voice_body,
                "snapshot": {"enabled": False, "camera_id": ""},
            },
        },
    }


DEFAULT_NOTIFICATION_RULES: list[dict[str, Any]] = [
    _event_rule(
        "authorised-entry",
        "Authorised Entry",
        "authorized_entry",
        "info",
        "[FirstName] arrived at the gate",
        "[FirstNamePossessive] [VehicleDisplayName] has been detected at the gate. I've let [ObjectPronoun] in.",
        voice_body="[FirstName] has arrived at the gate.",
    ),
    _event_rule(
        "unauthorised-plate",
        "Unauthorised Plate",
        "unauthorized_plate",
        "critical",
        "Unauthorised plate [VehicleRegistrationNumber]",
        "Unauthorised plate [VehicleRegistrationNumber] was detected at [Source]. The gate stayed closed.",
        voice_body="Alert. Unauthorised plate [VehicleRegistrationNumber] detected at the gate.",
    ),
    _event_rule(
        "outside-schedule",
        "Outside Schedule",
        "outside_schedule",
        "warning",
        "[DisplayName] outside schedule",
        "[DisplayName] arrived outside their approved schedule in [VehicleDisplayName] ([VehicleRegistrationNumber]). Timing was classified as [TimingClassification].",
        voice_body="[DisplayName] has arrived outside schedule.",
    ),
    _event_rule(
        "duplicate-entry",
        "Duplicate Entry",
        "duplicate_entry",
        "warning",
        "[DisplayName] duplicate entry",
        "[DisplayName] is already marked as present, but [VehicleRegistrationNumber] was read as another entry at [Source].",
        voice_body="[DisplayName] has produced a duplicate entry event.",
    ),
    _event_rule(
        "duplicate-exit",
        "Duplicate Exit",
        "duplicate_exit",
        "info",
        "[DisplayName] duplicate exit",
        "[DisplayName] is already marked as away, but [VehicleRegistrationNumber] was read as another exit at [Source].",
        voice_body="[DisplayName] has produced a duplicate exit event.",
    ),
    _event_rule(
        "gate-open-failed",
        "Gate Open Failed",
        "gate_open_failed",
        "critical",
        "Gate command failed",
        "The gate command failed for [VehicleRegistrationNumber] at [Source]. [Message]",
        voice_body="Alert. Gate command failed.",
    ),
    _event_rule(
        "garage-door-open-failed",
        "Garage Door Failed",
        "garage_door_open_failed",
        "critical",
        "Garage door command failed",
        "Garage door [GarageDoor] did not open for [DisplayName]. Entity [EntityId]. [Message]",
        voice_body="Alert. Garage door command failed.",
    ),
    _event_rule(
        "ai-anomaly-alert",
        "AI Anomaly Alert",
        "agent_anomaly_alert",
        "critical",
        "[Severity]: [Subject]",
        "AI anomaly alert for [Subject]. [Message]",
        voice_body="Alert. [Message]",
    ),
    {
        "id": "integration-tests",
        "name": "Integration Test Messages",
        "description": "Manual test messages sent from settings and integration screens.",
        "enabled": True,
        "event_types": ["integration_test"],
        "severities": ["info", "warning", "critical"],
        "channels": {
            "mobile": {
                "enabled": True,
                "endpoint_ids": ["apprise:*"],
                "title_template": "[Subject]",
                "body_template": "Integration test message: [Message]",
                "snapshot": {"enabled": False, "camera_id": ""},
            },
            "in_app": {
                "enabled": True,
                "endpoint_ids": ["dashboard"],
                "title_template": "[Subject]",
                "body_template": "Integration test message: [Message]",
                "snapshot": {"enabled": False, "camera_id": ""},
            },
            "voice": {
                "enabled": False,
                "endpoint_ids": [],
                "title_template": "",
                "body_template": "Integration test message. [Message]",
                "snapshot": {"enabled": False, "camera_id": ""},
            },
        },
    },
]


def default_notification_rules() -> list[dict[str, Any]]:
    return deepcopy(DEFAULT_NOTIFICATION_RULES)


def default_channel(channel_id: str) -> dict[str, Any]:
    if channel_id == "mobile":
        return {
            "enabled": False,
            "endpoint_ids": ["apprise:*"],
            "title_template": "[Severity]: [Subject]",
            "body_template": "[Message]",
            "snapshot": {"enabled": False, "camera_id": ""},
        }
    if channel_id == "voice":
        return {
            "enabled": False,
            "endpoint_ids": [],
            "title_template": "",
            "body_template": "[Message]",
            "snapshot": {"enabled": False, "camera_id": ""},
        }
    return {
        "enabled": True,
        "endpoint_ids": ["dashboard"],
        "title_template": "[Severity]: [Subject]",
        "body_template": "[Message]",
        "snapshot": {"enabled": False, "camera_id": ""},
    }


def normalize_notification_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raw_rules = default_notification_rules()
    else:
        raw_rules = value
    normalized: list[dict[str, Any]] = []
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            continue
        rule_id = str(raw_rule.get("id") or f"notification-rule-{index + 1}").strip()
        name = str(raw_rule.get("name") or "Notification Rule").strip()
        event_types = _normalize_string_list(raw_rule.get("event_types"))
        severities = _normalize_string_list(raw_rule.get("severities"))
        if not event_types:
            event_types = ["integration_test"]
        if not severities:
            severities = ["info", "warning", "critical"]

        channels: dict[str, dict[str, Any]] = {}
        raw_channels = raw_rule.get("channels") if isinstance(raw_rule.get("channels"), dict) else {}
        for channel_id in sorted(CHANNEL_IDS):
            raw_channel = raw_channels.get(channel_id) if isinstance(raw_channels, dict) else None
            base = default_channel(channel_id)
            if isinstance(raw_channel, dict):
                base.update(
                    {
                        "enabled": bool(raw_channel.get("enabled")),
                        "endpoint_ids": _normalize_string_list(raw_channel.get("endpoint_ids")),
                        "title_template": str(raw_channel.get("title_template") or ""),
                        "body_template": str(raw_channel.get("body_template") or ""),
                        "snapshot": _normalize_snapshot(raw_channel.get("snapshot")),
                    }
                )
            if channel_id == "in_app" and not base["endpoint_ids"]:
                base["endpoint_ids"] = ["dashboard"]
            channels[channel_id] = base

        normalized.append(
            {
                "id": rule_id[:80],
                "name": name[:120],
                "description": str(raw_rule.get("description") or "")[:300],
                "enabled": raw_rule.get("enabled") is not False,
                "event_types": event_types,
                "severities": severities,
                "channels": channels,
            }
        )

    return normalized


def remove_legacy_grouped_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        rule
        for rule in rules
        if str(rule.get("id")) not in LEGACY_GROUPED_RULE_IDS and len(rule.get("event_types") or []) <= 1
    ]


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [item.strip() for item in value.replace("\n", ",").split(",")]
    elif isinstance(value, list):
        values = [str(item).strip() for item in value]
    else:
        values = []
    return [item for item in values if item]


def _normalize_snapshot(value: Any) -> dict[str, str | bool]:
    if not isinstance(value, dict):
        return {"enabled": False, "camera_id": ""}
    return {
        "enabled": bool(value.get("enabled")),
        "camera_id": str(value.get("camera_id") or ""),
    }
