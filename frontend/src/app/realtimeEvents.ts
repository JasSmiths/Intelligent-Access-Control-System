import type React from "react";
import type { HomeAssistantManagedCover, IntegrationStatus, MaintenanceStatus, RealtimeMessage } from "../api/types";
import { isRecord, nullableString, numberPayload, stringPayload, titleCase } from "../lib/format";
export const REALTIME_RECONNECT_DELAY_MS = 1500;
export const REALTIME_RESUME_RECONNECT_AFTER_MS = 30000;
export const REALTIME_CLIENT_PING_INTERVAL_MS = 25000;
export const REALTIME_PROBE_TIMEOUT_MS = 5000;
export const REALTIME_DEFER_PARSE_BYTES = 256 * 1024;
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
  live: "Live",
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
function isNotificationSeverity(value: string): value is NotificationToast["severity"] {
  return ["info", "warning", "critical"].includes(value);
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
