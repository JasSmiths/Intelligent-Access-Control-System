import type React from "react";
import type { AccessEvent, HomeAssistantManagedCover, IntegrationStatus, MaintenanceStatus, MovementSagaSummary, RealtimeMessage } from "../api/types";
import { isRecord, nullableString, numberPayload, stringPayload, titleCase } from "../lib/format";
export const REALTIME_REFRESH_MIN_INTERVAL_MS = 5000;
export const REALTIME_RECONNECT_DELAY_MS = 1500;
export const REALTIME_RESUME_REFRESH_MIN_INTERVAL_MS = 1000;
export const REALTIME_RESUME_RECONNECT_AFTER_MS = 30000;
export const REALTIME_CLIENT_PING_INTERVAL_MS = 25000;
export const REALTIME_PROBE_TIMEOUT_MS = 5000;
export const REALTIME_DEFER_PARSE_BYTES = 256 * 1024;
const REALTIME_DATA_REFRESH_EVENTS = new Set([
  "access_event.finalize_failed",
  "automation.run.failed",
  "automation.run.skipped",
  "automation.run.succeeded",
  "alerts.updated",
  "visitor_pass.created",
  "visitor_pass.updated",
  "visitor_pass.cancelled",
  "visitor_pass.deleted",
  "visitor_pass.status_changed",
  "visitor_pass.used",
  "visitor_pass.departure_recorded"
]);
const REALTIME_AUDIT_REFRESH_ACTION_PREFIXES = [
  "automation_rule.",
  "dependency_updates.",
  "group.",
  "notification_rule.",
  "person.",
  "schedule.",
  "settings.",
  "user.",
  "vehicle.",
  "visitor_pass."
];
export type NotificationToast = {
  id: string;
  title: string;
  body: string;
  event_type: string;
  severity: "info" | "warning" | "critical";
  snapshot_url?: string;
  actions?: NotificationToastAction[];
};
export type NotificationToastAction = {
  id: string;
  label: string;
  method: "POST";
  path: string;
};
export type RealtimeConnectionStatus = "connecting" | "checking" | "live" | "refreshing" | "reconnecting" | "offline" | "degraded";
export type RealtimeConnectionState = {
  status: RealtimeConnectionStatus;
  title: string;
  detail: string;
};
const REALTIME_STATUS_TITLES: Record<RealtimeConnectionStatus, string> = {
  connecting: "Opening stream",
  checking: "Verifying stream",
  live: "Realtime live",
  refreshing: "Syncing data",
  reconnecting: "Reconnecting",
  offline: "Network offline",
  degraded: "Stream degraded"
};
export function realtimeStatus(status: RealtimeConnectionStatus, detail: string): RealtimeConnectionState {
  return {
    status,
    title: REALTIME_STATUS_TITLES[status],
    detail
  };
}
export function realtimeProbeDetail(reason: string) {
  if (reason === "focus") return "Page focused; checking stream health";
  if (reason === "visibilitychange") return "Page visible; checking stream health";
  if (reason === "pageshow") return "Page restored; checking stream health";
  if (reason === "online") return "Network restored; checking stream health";
  if (reason === "interval") return "Routine stream health check";
  if (reason === "open") return "Waiting for server response";
  if (reason.startsWith("probe_timeout")) return "Health check timed out; opening a fresh stream";
  if (reason.startsWith("connect_timeout")) return "Connection attempt stalled; retrying";
  return "Checking live event stream";
}
export function applyMaintenanceRealtimeEvent(
  event: RealtimeMessage,
  setMaintenanceStatus: React.Dispatch<React.SetStateAction<MaintenanceStatus | null>>
) {
  if (event.type !== "maintenance_mode.changed") return false;
  const status = maintenanceStatusFromPayload(event.payload);
  if (!status) return false;
  setMaintenanceStatus(status);
  return true;
}
function maintenanceStatusFromPayload(payload: Record<string, unknown>): MaintenanceStatus | null {
  const candidate = isRecord(payload.status) ? payload.status : payload;
  if (typeof candidate.is_active !== "boolean") return null;
  return {
    is_active: candidate.is_active,
    enabled_by: nullableString(candidate.enabled_by),
    enabled_at: nullableString(candidate.enabled_at),
    source: nullableString(candidate.source),
    reason: nullableString(candidate.reason),
    duration_seconds: numberPayload(candidate.duration_seconds),
    duration_label: nullableString(candidate.duration_label),
    ha_entity_id: nullableString(candidate.ha_entity_id) ?? undefined
  };
}
export function applyIntegrationRealtimeEvent(
  event: RealtimeMessage,
  setIntegrationStatus: React.Dispatch<React.SetStateAction<IntegrationStatus | null>>
) {
  if (event.type === "home_assistant.status") {
    const payload = event.payload as Partial<IntegrationStatus>;
    setIntegrationStatus((current) => current ? { ...current, ...payload } : current);
    return true;
  }
  if (event.type === "access_device.status") {
    const payload = isRecord(event.payload.status) ? event.payload.status : event.payload;
    setIntegrationStatus((current) => current ? { ...current, ...(payload as Partial<IntegrationStatus>) } : current);
    return true;
  }
  if (event.type === "gate.state_changed") {
    const state = stringPayload(event.payload.state);
    const entityId = stringPayload(event.payload.entity_id);
    if (!state) return false;
    setIntegrationStatus((current) => {
      if (!current) return current;
      const gate_entities = updateManagedCoverState(current.gate_entities, entityId, state);
      return { ...current, gate_entities, current_gate_state: state, last_gate_state: state };
    });
    return true;
  }
  if (event.type === "door.state_changed") {
    const door = stringPayload(event.payload.door);
    const entityId = stringPayload(event.payload.entity_id);
    const state = stringPayload(event.payload.state);
    const stateKey = doorStateKey(door);
    if (!state || (!stateKey && door !== "garage_door")) return false;
    setIntegrationStatus((current) => {
      if (!current) return current;
      if (door === "garage_door") {
        return { ...current, garage_door_entities: updateManagedCoverState(current.garage_door_entities, entityId, state) };
      }
      return { ...current, [stateKey]: state };
    });
    return true;
  }
  return false;
}
export function accessEventFromRealtime(event: RealtimeMessage): AccessEvent | null {
  if (event.type !== "access_event.finalized") return null;
  const eventId = stringPayload(event.payload.event_id);
  const registrationNumber = stringPayload(event.payload.registration_number);
  const direction = stringPayload(event.payload.direction);
  const decision = stringPayload(event.payload.decision);
  const source = stringPayload(event.payload.source);
  const occurredAt = stringPayload(event.payload.occurred_at);
  const timingClassification = stringPayload(event.payload.timing_classification);
  if (!eventId || !registrationNumber || !isAccessDirection(direction) || !isAccessDecision(decision) || !source || !occurredAt) {
    return null;
  }
  return {
    id: eventId,
    registration_number: registrationNumber,
    direction,
    decision,
    confidence: numberPayload(event.payload.confidence),
    source,
    occurred_at: occurredAt,
    timing_classification: timingClassification || "unknown",
    anomaly_count: numberPayload(event.payload.anomaly_count),
    visitor_pass_id: stringPayload(event.payload.visitor_pass_id) || null,
    visitor_name: stringPayload(event.payload.visitor_name) || null,
    visitor_pass_mode: stringPayload(event.payload.visitor_pass_mode) || null,
    snapshot_url: stringPayload(event.payload.snapshot_url) || null,
    snapshot_captured_at: stringPayload(event.payload.snapshot_captured_at) || null,
    snapshot_bytes: nullableNumber(event.payload.snapshot_bytes),
    snapshot_width: nullableNumber(event.payload.snapshot_width),
    snapshot_height: nullableNumber(event.payload.snapshot_height),
    snapshot_camera: stringPayload(event.payload.snapshot_camera) || null,
    movement_saga: movementSagaFromPayload(event.payload.movement_saga)
  };
}
function movementSagaFromPayload(value: unknown): MovementSagaSummary | null {
  if (!isRecord(value)) return null;
  const state = stringPayload(value.state);
  if (!state) return null;
  const gate = isRecord(value.gate) ? value.gate : null;
  return {
    id: stringPayload(value.id) || null,
    state,
    reconciliation_required: value.reconciliation_required === true,
    gate_command_required: value.gate_command_required === true,
    presence_committed: value.presence_committed === true,
    failure_detail: stringPayload(value.failure_detail) || null,
    updated_at: stringPayload(value.updated_at) || null,
    detail: stringPayload(value.detail) || null,
    gate: gate ? {
      command_id: stringPayload(gate.command_id) || null,
      accepted: typeof gate.accepted === "boolean" ? gate.accepted : null,
      state: stringPayload(gate.state) || null,
      detail: stringPayload(gate.detail) || null,
      mechanically_confirmed: gate.mechanically_confirmed === true,
      requires_reconciliation: gate.requires_reconciliation === true
    } : null
  };
}
export function notificationToastFromRealtime(event: RealtimeMessage): NotificationToast | null {
  if (event.type !== "notification.in_app") return null;
  const title = stringPayload(event.payload.title);
  const body = stringPayload(event.payload.body);
  const eventType = stringPayload(event.payload.event_type);
  const severity = stringPayload(event.payload.severity);
  const snapshot = event.payload.snapshot;
  let snapshotUrl = "";
  if (snapshot && typeof snapshot === "object" && "image_url" in snapshot) {
    snapshotUrl = stringPayload((snapshot as Record<string, unknown>).image_url);
  }
  const actions = notificationToastActions(event.payload.actions);
  if (!title && !body) return null;
  return {
    id: `${event.created_at ?? Date.now()}-${eventType}-${Math.random().toString(16).slice(2)}`,
    title: title || titleCase(eventType),
    body,
    event_type: eventType,
    severity: isNotificationSeverity(severity) ? severity : "info",
    snapshot_url: snapshotUrl || undefined,
    actions: actions.length ? actions : undefined,
  };
}
function notificationToastActions(value: unknown): NotificationToastAction[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!isRecord(item)) return [];
    const method = stringPayload(item.method).toUpperCase();
    const action = {
      id: stringPayload(item.id),
      label: stringPayload(item.label),
      method: method === "POST" ? "POST" as const : null,
      path: stringPayload(item.path),
    };
    return action.id && action.label && action.method && action.path ? [action as NotificationToastAction] : [];
  });
}
export function shouldRefreshDataForRealtimeEvent(event: RealtimeMessage) {
  if (REALTIME_DATA_REFRESH_EVENTS.has(event.type) || event.type.startsWith("automation.run.")) {
    return true;
  }
  if (event.type !== "audit.log.created") return false;
  const payload = isRecord(event.payload.log) ? event.payload.log : event.payload;
  const action = stringPayload(payload.action);
  return REALTIME_AUDIT_REFRESH_ACTION_PREFIXES.some((prefix) => action.startsWith(prefix));
}
export function realtimeMessageForRouteConsumers(event: RealtimeMessage): RealtimeMessage | null {
  if (event.type.startsWith("visitor_pass.")) {
    const visitorPass = isRecord(event.payload.visitor_pass) ? compactVisitorPassRealtimePayload(event.payload.visitor_pass) : null;
    return {
      type: event.type,
      created_at: event.created_at,
      payload: {
        ...(visitorPass ? { visitor_pass: visitorPass } : {}),
        source: stringPayload(event.payload.source)
      }
    };
  }
  if (event.type === "audit.log.created") {
    const log = isRecord(event.payload.log) ? event.payload.log : event.payload;
    const action = stringPayload(log.action);
    const targetEntity = stringPayload(log.target_entity);
    if (!action.startsWith("visitor_pass.") && targetEntity.toLowerCase() !== "visitorpass") return null;
    return {
      type: event.type,
      created_at: event.created_at,
      payload: {
        log: {
          action,
          target_entity: targetEntity,
          target_id: stringPayload(log.target_id)
        }
      }
    };
  }
  if (event.type === "access_event.finalized" || event.type === "leaderboard_overtake") {
    return { type: event.type, created_at: event.created_at, payload: {} };
  }
  if (event.type === "access_device.status") {
    const payload = isRecord(event.payload.status) ? { status: event.payload.status } : event.payload;
    return { type: event.type, created_at: event.created_at, payload };
  }
  if (event.type === "icloud_calendar.accounts_changed") {
    return {
      type: event.type,
      created_at: event.created_at,
      payload: Array.isArray(event.payload.accounts) ? { accounts: event.payload.accounts } : {}
    };
  }
  if (event.type === "icloud_calendar.sync_completed") {
    return {
      type: event.type,
      created_at: event.created_at,
      payload: isRecord(event.payload.sync) ? { sync: event.payload.sync } : {}
    };
  }
  return null;
}
function compactVisitorPassRealtimePayload(candidate: Record<string, unknown>) {
  return {
    id: stringPayload(candidate.id),
    visitor_name: stringPayload(candidate.visitor_name),
    pass_type: stringPayload(candidate.pass_type),
    visitor_phone: stringPayload(candidate.visitor_phone),
    expected_time: stringPayload(candidate.expected_time),
    window_minutes: numberPayload(candidate.window_minutes),
    valid_from: stringPayload(candidate.valid_from),
    valid_until: stringPayload(candidate.valid_until),
    window_start: stringPayload(candidate.window_start),
    window_end: stringPayload(candidate.window_end),
    status: stringPayload(candidate.status),
    creation_source: stringPayload(candidate.creation_source),
    source_reference: stringPayload(candidate.source_reference),
    source_metadata: compactVisitorPassSourceMetadata(candidate.source_metadata),
    whatsapp_status: stringPayload(candidate.whatsapp_status),
    whatsapp_status_label: stringPayload(candidate.whatsapp_status_label),
    whatsapp_status_detail: stringPayload(candidate.whatsapp_status_detail),
    created_by_user_id: stringPayload(candidate.created_by_user_id),
    created_by: stringPayload(candidate.created_by),
    arrival_time: stringPayload(candidate.arrival_time),
    departure_time: stringPayload(candidate.departure_time),
    number_plate: stringPayload(candidate.number_plate),
    vehicle_make: stringPayload(candidate.vehicle_make),
    vehicle_colour: stringPayload(candidate.vehicle_colour),
    duration_on_site_seconds: typeof candidate.duration_on_site_seconds === "number" ? candidate.duration_on_site_seconds : null,
    duration_human: stringPayload(candidate.duration_human),
    arrival_event_id: stringPayload(candidate.arrival_event_id),
    departure_event_id: stringPayload(candidate.departure_event_id),
    telemetry_trace_id: stringPayload(candidate.telemetry_trace_id),
    created_at: stringPayload(candidate.created_at),
    updated_at: stringPayload(candidate.updated_at)
  };
}
function compactVisitorPassSourceMetadata(value: unknown) {
  if (!isRecord(value)) return null;
  return {
    whatsapp_abuse_muted_until: stringPayload(value.whatsapp_abuse_muted_until),
    whatsapp_abuse_muted_reason: stringPayload(value.whatsapp_abuse_muted_reason),
    whatsapp_last_error: stringPayload(value.whatsapp_last_error)
  };
}
function isNotificationSeverity(value: string): value is NotificationToast["severity"] {
  return ["info", "warning", "critical"].includes(value);
}
function isAccessDirection(value: string): value is AccessEvent["direction"] {
  return ["entry", "exit", "denied"].includes(value);
}
function isAccessDecision(value: string): value is AccessEvent["decision"] {
  return ["granted", "denied"].includes(value);
}
function doorStateKey(door: string) {
  const keys: Record<string, keyof IntegrationStatus> = {
    back_door: "back_door_state",
    front_door: "front_door_state",
    main_garage_door: "main_garage_door_state",
    mums_garage_door: "mums_garage_door_state"
  };
  return keys[door] ?? null;
}
function updateManagedCoverState(entities: HomeAssistantManagedCover[] | undefined, entityId: string, state: string) {
  if (!entities?.length || !entityId) return entities;
  return entities.map((entity) => entity.entity_id === entityId ? { ...entity, state } : entity);
}
function nullableNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
