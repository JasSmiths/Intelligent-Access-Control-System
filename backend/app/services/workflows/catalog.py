from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GATE_MALFUNCTION_EVENT_TYPE = "gate_malfunction"
INTEGRATION_DEGRADED_EVENT_TYPE = "integration_degraded"


@dataclass(frozen=True)
class WorkflowVariable:
    name: str
    token: str
    label: str
    scope: str


@dataclass(frozen=True)
class WorkflowVariableDefinition:
    name: str
    label: str
    notification_group: str | None = None
    automation_scope: str | None = None
    automation_label: str | None = None

    @property
    def token(self) -> str:
        return f"@{self.name}"


V = WorkflowVariableDefinition
VARIABLE_DEFINITIONS: tuple[WorkflowVariableDefinition, ...] = (
    V("FirstName", "First name", "Person", "person"),
    V("LastName", "Last name", "Person", "person"),
    V("DisplayName", "Display name", "Person", "person"),
    V("PersonId", "Person ID", None, "person"),
    V("GroupName", "Group name", "Person"),
    V("FirstNamePossessive", "First name possessive", "Person"),
    V("ObjectPronoun", "Object pronoun", "Person"),
    V("PossessiveDeterminer", "Possessive determiner", "Person"),
    V("Registration", "Registration", "Vehicle", "vehicle"),
    V("VehicleRegistrationNumber", "Registration number", "Vehicle", "vehicle"),
    V("VehicleId", "Vehicle ID", None, "vehicle"),
    V("VehicleName", "Friendly vehicle name", "Vehicle", "vehicle", "Vehicle display name"),
    V("VehicleDisplayName", "Vehicle display name", "Vehicle"),
    V("VehicleMake", "Vehicle make", "Vehicle", "vehicle"),
    V("VehicleType", "Vehicle type", "Vehicle"),
    V("VehicleModel", "Vehicle model", "Vehicle"),
    V("VehicleColor", "Vehicle colour", "Vehicle", "vehicle"),
    V("VehicleColour", "Vehicle colour", "Vehicle", "vehicle"),
    V("MotStatus", "MOT status", "Vehicle"),
    V("MotExpiry", "MOT expiry", "Vehicle"),
    V("TaxStatus", "Tax status", "Vehicle"),
    V("TaxExpiry", "Tax expiry", "Vehicle"),
    V("Time", "Event time", "Event", "time"),
    V("OccurredAt", "Event timestamp", "Event", "event"),
    V("Date", "Event date", None, "time"),
    V("GateStatus", "Gate status", "Event"),
    V("Direction", "Entry or exit", "Event"),
    V("Decision", "Access decision", "Event"),
    V("TimingClassification", "Timing classification", "Event"),
    V("Source", "Source", "Event", "event"),
    V("Severity", "Severity", "Event"),
    V("EventType", "Event type", "Event", "event"),
    V("Subject", "Subject", "Event", "event"),
    V("Message", "Message", "Event", "event"),
    V("MaintenanceModeReason", "Maintenance mode reason", "Event", "maintenance", "Maintenance reason"),
    V("MaintenanceModeDuration", "Maintenance duration", None, "maintenance"),
    V("IntegrationName", "Integration name", "Integration"),
    V("IntegrationStatus", "Integration status", "Integration"),
    V("IntegrationReason", "Degraded reason", "Integration"),
    V("IntegrationLastConnectedAt", "Last connected at", "Integration"),
    V("IntegrationLastFailureAt", "Last failure at", "Integration"),
    V("GarageDoor", "Garage door", "Integration"),
    V("EntityId", "Entity ID", "Integration"),
    V("VisitorPassId", "Visitor Pass ID", None, "visitor_pass"),
    V("VisitorName", "Visitor name", "Visitor Pass", "visitor_pass"),
    V("VisitorPassName", "Visitor Pass name", "Visitor Pass"),
    V("VisitorPassRegistration", "Visitor Pass registration", "Visitor Pass"),
    V("VisitorPassTimeWindow", "Visitor Pass time window", "Visitor Pass"),
    V("VisitorPassVehicleRegistration", "Visitor Pass vehicle registration", "Visitor Pass", "visitor_pass"),
    V("VisitorPassVehicleMake", "Visitor Pass vehicle make", "Visitor Pass", "visitor_pass"),
    V("VisitorPassVehicleColour", "Visitor Pass vehicle colour", "Visitor Pass", "visitor_pass"),
    V("VisitorPassDurationOnSite", "Visitor Pass duration on site", "Visitor Pass", "visitor_pass", "Visitor Pass duration"),
    V("VisitorPassCurrentWindow", "Visitor Pass current window", "Visitor Pass"),
    V("VisitorPassRequestedWindow", "Visitor Pass requested window", "Visitor Pass"),
    V("VisitorPassOriginalTime", "Visitor Pass original time", "Visitor Pass"),
    V("VisitorPassRequestedTime", "Visitor Pass requested time", "Visitor Pass"),
    V("VisitorPassVisitorMessage", "Visitor Pass visitor message", "Visitor Pass"),
    V("MalfunctionDuration", "Malfunction duration", "Malfunction"),
    V("MalfunctionOpenedTime", "Gate opened time", "Malfunction"),
    V("MalfunctionFixAttemptTime", "Latest fix attempt time", "Malfunction"),
    V("MalfunctionFixAttempts", "Fix attempt count", "Malfunction"),
    V("MalfunctionResolutionTime", "Resolution time", "Malfunction"),
    V("MalfunctionStage", "Malfunction stage", "Malfunction"),
    V("LastKnownVehicle", "Last known vehicle", "Malfunction"),
    V("NewWinnerName", "New winner", "Leaderboard"),
    V("OvertakenName", "Overtaken person", "Leaderboard"),
    V("ReadCount", "Read count", "Leaderboard"),
    V("WebhookKey", "Webhook key", None, "webhook"),
    V("WebhookSenderIp", "Webhook sender IP", None, "webhook"),
    V("AlfredPhrase", "Alfred phrase", None, "ai"),
    V("AlfredIssue", "Alfred issue", None, "ai"),
)

NOTIFICATION_TRIGGER_DEFINITIONS = (
    ("ai_agents", "AI Agents", (("agent_anomaly_alert", "AI Anomaly Alert", "critical", "The AI agent raises an explicit anomaly alert."),)),
    ("compliance", "Compliance", (
        ("expired_mot_detected", "Expired MOT Detected", "warning", "DVLA reports a vehicle MOT status other than Valid or Not Required on arrival."),
        ("expired_tax_detected", "Expired Tax Detected", "warning", "DVLA reports a vehicle tax status other than Taxed or SORN on arrival."),
    )),
    ("gate_actions", "Gate Actions", (
        ("garage_door_open_failed", "Garage Door Failed", "critical", "A linked garage door command failed."),
        ("gate_open_failed", "Gate Open Failed", "critical", "The access decision was granted but the gate command failed."),
    )),
    ("gate_malfunctions", "Gate Malfunctions", (
        (GATE_MALFUNCTION_EVENT_TYPE, "Gate Malfunction", "critical", "The primary gate malfunction lifecycle changed stage."),
    )),
    ("integrations", "Integrations", (
        (INTEGRATION_DEGRADED_EVENT_TYPE, "Integration Degraded", "warning", "A configured integration moved into a degraded or unreachable state."),
    )),
    ("leaderboard", "Leaderboard", (
        ("leaderboard_overtake", "Leaderboard Overtake", "info", "A known vehicle takes the top spot on Top Charts."),
    )),
    ("maintenance_mode", "Maintenance Mode", (
        ("maintenance_mode_disabled", "Maintenance Mode Disabled", "info", "The global automation kill-switch was disabled."),
        ("maintenance_mode_enabled", "Maintenance Mode Enabled", "warning", "The global automation kill-switch was enabled."),
    )),
    ("vehicle_detections", "Vehicle Detections", (
        ("authorized_entry", "Authorised Vehicle Detected", "info", "A known vehicle is granted entry inside its access policy."),
        ("duplicate_entry", "Duplicate Entry", "warning", "A person already marked home is detected entering again."),
        ("duplicate_exit", "Duplicate Exit", "info", "A person already marked away is detected exiting again."),
        ("outside_schedule", "Outside Schedule", "warning", "A known vehicle is denied by schedule or access policy."),
        ("unauthorized_plate", "Unknown Vehicle Detected", "warning", "An unknown or inactive vehicle plate is denied."),
        ("visitor_pass_vehicle_arrived", "Visitor Pass Vehicle Arrived", "info", "A vehicle matched to a Visitor Pass has arrived on site."),
        ("visitor_pass_vehicle_exited", "Visitor Pass Vehicle Exited", "info", "A vehicle matched to a Visitor Pass has left the site."),
    )),
    ("visitor_pass", "Visitor Pass", (
        ("visitor_pass_arranged", "Visitor Pass Arranged", "info", "A WhatsApp visitor completed their Visitor Pass setup."),
        ("visitor_pass_cancelled", "Visitor Pass Cancelled", "info", "A scheduled or active Visitor Pass was cancelled."),
        ("visitor_pass_created", "Visitor Pass Created", "info", "A new Visitor Pass was created."),
        ("visitor_pass_expired", "Visitor Pass Expired", "warning", "A Visitor Pass window elapsed without being used."),
        ("visitor_pass_timeframe_change_requested", "Visitor Pass Timeframe Change Requested", "warning", "A WhatsApp visitor requested a Visitor Pass timeframe change that needs Admin approval."),
        ("visitor_pass_used", "Visitor Pass Used", "info", "A Visitor Pass was matched to an arriving vehicle."),
    )),
)

AUTOMATION_TRIGGER_DEFINITIONS = (
    ("time_date", "Time & Date", (
        ("time.specific_datetime", "Specific Date & Time", "Run at one chosen date/time, or recur from that date/time.", ("time", "event")),
        ("time.every_x", "Every X", "Run every configured number of minutes, hours, or days.", ("time", "event")),
        ("time.cron", "Cron Job", "Run from a raw five-field cron expression.", ("time", "event")),
        ("time.ai_text", "AI Text Input", "Parse natural-language schedule text into cron and optional end date.", ("time", "event")),
    )),
    ("vehicle_detections", "Vehicle Detections", (
        ("vehicle.known_plate", "Known Plate", "A known vehicle is detected.", ("person", "vehicle", "event")),
        ("vehicle.unknown_plate", "Unknown Plate", "An unknown plate is detected.", ("vehicle", "event")),
        ("vehicle.outside_schedule", "Outside of Schedule", "A known vehicle is denied by its access schedule.", ("person", "vehicle", "event")),
    )),
    ("maintenance_mode", "Maintenance Mode", (
        ("maintenance_mode.enabled", "Maintenance Mode Enabled", "The global automation kill-switch was enabled.", ("maintenance", "event")),
        ("maintenance_mode.disabled", "Maintenance Mode Disabled", "The global automation kill-switch was disabled.", ("maintenance", "event")),
    )),
    ("visitor_pass", "Visitor Pass", (
        ("visitor_pass.created", "Visitor Pass Created", "A Visitor Pass was created.", ("visitor_pass", "vehicle", "event")),
        ("visitor_pass.detected", "Visitor Pass Detected", "A Visitor Pass vehicle was detected.", ("visitor_pass", "vehicle", "event")),
        ("visitor_pass.used", "Visitor Pass Used", "A Visitor Pass was claimed by an arriving vehicle.", ("visitor_pass", "vehicle", "event")),
        ("visitor_pass.expired", "Visitor Pass Expired", "A Visitor Pass window expired unused.", ("visitor_pass", "event")),
    )),
    ("ai_agent", "AI Agent", (
        ("ai.phrase_received", "Phrase Received", "Alfred receives a phrase that matches this automation.", ("ai", "event")),
        ("ai.issue_detected", "Issue Detected", "Alfred autonomously flags an anomaly.", ("ai", "event")),
    )),
    ("webhook", "Webhook", (
        ("webhook.received", "Webhook Received", "A webhook is received on an automation endpoint.", ("webhook", "event")),
        ("webhook.unrecognized", "Unrecognised Webhook", "A webhook key has no matching active receiver rule.", ("webhook", "event")),
        ("webhook.new_sender", "New Webhook Sender", "A webhook key is used by an unseen source IP.", ("webhook", "event")),
    )),
)

AUTOMATION_CONDITION_DEFINITIONS = (
    ("person", "Person", (("person.on_site", "Person On Site", ("person",)), ("person.off_site", "Person Off Site", ("person",)))),
    ("vehicles", "Vehicles", (("vehicle.on_site", "Vehicle On Site", ("vehicle", "person")), ("vehicle.off_site", "Vehicle Off Site", ("vehicle", "person")))),
    ("maintenance_mode", "Maintenance Mode", (("maintenance_mode.enabled", "Maintenance Mode Enabled", ("maintenance",)), ("maintenance_mode.disabled", "Maintenance Mode Disabled", ("maintenance",)))),
)

AUTOMATION_ACTION_DEFINITIONS = (
    ("notifications", "Notifications", (
        ("notification.enable", "Enable Notification", "Enable an existing notification workflow."),
        ("notification.disable", "Disable Notification", "Disable an existing notification workflow."),
    )),
    ("gate_actions", "Gate Actions", (("gate.open", "Open the Gate", "Open configured gate entities through the gate controller."),)),
    ("garage_door_actions", "Garage Door Actions", (
        ("garage_door.open", "Open Garage Door", "Open one or more configured garage door entities."),
        ("garage_door.close", "Close Garage Door", "Close one or more configured garage door entities."),
    )),
    ("maintenance_mode", "Maintenance Mode", (
        ("maintenance_mode.enable", "Enable Maintenance Mode", "Enable the global automation kill-switch."),
        ("maintenance_mode.disable", "Disable Maintenance Mode", "Disable the global automation kill-switch."),
    )),
)


def notification_variable_groups() -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for definition in VARIABLE_DEFINITIONS:
        if definition.notification_group:
            groups.setdefault(definition.notification_group, []).append(
                {"name": definition.name, "token": definition.token, "label": definition.label}
            )
    return [{"group": group, "items": groups[group]} for group in groups]


def automation_variables() -> list[WorkflowVariable]:
    return [
        WorkflowVariable(
            definition.name,
            definition.token,
            definition.automation_label or definition.label,
            definition.automation_scope,
        )
        for definition in VARIABLE_DEFINITIONS
        if definition.automation_scope
    ]


def notification_trigger_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": group_id,
            "label": group_label,
            "events": [
                {"value": value, "label": label, "severity": severity, "description": description}
                for value, label, severity, description in events
            ],
        }
        for group_id, group_label, events in NOTIFICATION_TRIGGER_DEFINITIONS
    ]


def notification_actionable_catalog(gate_open_action: str) -> list[dict[str, Any]]:
    return [
        {
            "trigger_event": "unauthorized_plate",
            "actions": [
                {
                    "value": gate_open_action,
                    "label": "Open Gate",
                    "description": "Let the selected Home Assistant mobile recipient open the gate for this unknown plate.",
                },
            ],
        },
    ]


def automation_trigger_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": group_id,
            "label": group_label,
            "triggers": [
                {"type": item_type, "label": label, "description": description, "scopes": list(scopes)}
                for item_type, label, description, scopes in triggers
            ],
        }
        for group_id, group_label, triggers in AUTOMATION_TRIGGER_DEFINITIONS
    ]


def automation_condition_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": group_id,
            "label": group_label,
            "conditions": [
                {"type": item_type, "label": label, "scopes": list(scopes)}
                for item_type, label, scopes in conditions
            ],
        }
        for group_id, group_label, conditions in AUTOMATION_CONDITION_DEFINITIONS
    ]


def automation_action_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": group_id,
            "label": group_label,
            "actions": [
                {"type": item_type, "label": label, "description": description}
                for item_type, label, description in actions
            ],
        }
        for group_id, group_label, actions in AUTOMATION_ACTION_DEFINITIONS
    ]
