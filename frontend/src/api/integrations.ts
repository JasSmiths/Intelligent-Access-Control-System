import { api, createActionConfirmation, type ApiRequestOptions } from "./client";
import type { AccessDevice, ActionConfirmationOptions, HomeAssistantDiscovery, IntegrationStatus, UnifiProtectCamera, UserAccount } from "./types";
export type AccessDeviceEligibility = AccessDevice & { commandable: boolean; admission_eligible: boolean };
export type RecoveryTrackerDiscovery = {
  trackers: Array<{ entity_id: string; name: string; available: boolean; eligible: boolean; reason: string | null }>;
  mappings: Array<{ person_id: string; notify_service_id: string | null; suggested_tracker_entity_id: string | null; status: "matched" | "ambiguous" | "not_found" | "unavailable"; reason: string | null }>;
  status: "complete" | "unavailable";
  reason: string | null;
};
// These describe persisted command receipts, not live device state.
export type CommandDelivery = "accepted" | "rejected" | "not_sent" | "unknown";
export type GateCommandDelivery = CommandDelivery | "partial";
export type DeviceCommandReceipt = {
  command_id: string;
  target_device_id: string;
  device_key: string;
  action: string;
  status: string;
  accepted: boolean;
  delivery: CommandDelivery;
  state: string;
  verified: boolean;
  requires_reconciliation: boolean;
  detail: string | null;
  verification_observation_id?: string | null;
  provider_receipts?: Array<{ provider: string; delivery: CommandDelivery; acceptance_basis?: string | null }>;
};
export type GateCommandReceipt = {
  command_id?: string | null;
  intent_id?: string | null;
  accepted: boolean;
  delivery: GateCommandDelivery;
  state: string;
  mechanically_confirmed: boolean;
  admission_verified: boolean;
  requires_reconciliation: boolean;
  target_receipts: DeviceCommandReceipt[];
  detail?: string | null;
  command_status?: string;
};
// Historical gate rows may predate per-target receipts. Keep their identity available
// for inspection, without manufacturing a delivery or physical-state conclusion.
export type GateCommandHistoryRecord = Partial<GateCommandReceipt> & { command_id: string; started_at?: string | null };
export type GateCommandPage = { items: GateCommandHistoryRecord[]; next_cursor: string | null };
export type CoverCommandPage = { items: DeviceCommandReceipt[]; next_cursor: string | null };
export type CoverCommandResponse = {
  accepted: boolean;
  delivery: CommandDelivery;
  state: string;
  verified: boolean;
  requires_reconciliation: boolean;
  command_id: string | null;
  target_receipt: DeviceCommandReceipt | null;
  detail: string;
};
export type GateOpenPayload = { reason: string; target_device_key?: string };
export type CoverCommandPayload = { entity_id: string; action: "open" | "close"; reason: string };
export const gateCommandReceiptUrl = (intentId: string) => `/api/v1/integrations/gate/commands?intent_id=${encodeURIComponent(intentId)}`;
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function commandDelivery(value: unknown): value is CommandDelivery {
  return value === "accepted" || value === "rejected" || value === "not_sent" || value === "unknown";
}
export function isDeviceCommandReceipt(value: unknown): value is DeviceCommandReceipt {
  return record(value) && typeof value.command_id === "string" && typeof value.target_device_id === "string"
    && typeof value.device_key === "string" && typeof value.action === "string" && typeof value.status === "string"
    && typeof value.accepted === "boolean" && commandDelivery(value.delivery) && typeof value.state === "string"
    && typeof value.verified === "boolean" && typeof value.requires_reconciliation === "boolean"
    && (value.detail === null || typeof value.detail === "string")
    && (value.provider_receipts === undefined || (Array.isArray(value.provider_receipts) && value.provider_receipts.every((provider) =>
      record(provider) && typeof provider.provider === "string" && commandDelivery(provider.delivery)
      && (provider.acceptance_basis == null || typeof provider.acceptance_basis === "string"))));
}
export function isGateCommandReceipt(value: unknown): value is GateCommandReceipt {
  return record(value) && typeof value.accepted === "boolean" && (commandDelivery(value.delivery) || value.delivery === "partial")
    && typeof value.state === "string" && typeof value.mechanically_confirmed === "boolean"
    && typeof value.admission_verified === "boolean" && typeof value.requires_reconciliation === "boolean"
    && Array.isArray(value.target_receipts) && value.target_receipts.every(isDeviceCommandReceipt);
}
export function coverTargetReceipt(value: unknown): DeviceCommandReceipt | null {
  return record(value) && isDeviceCommandReceipt(value.target_receipt) ? value.target_receipt : null;
}
export type ICloudCalendarAccount = {
  id: string; apple_id: string; display_name: string; status: string; is_active: boolean;
  last_auth_at: string | null; last_sync_at: string | null; last_sync_status: string | null;
  last_sync_summary: Record<string, unknown> | null; last_error: string | null;
  created_by_user_id: string | null; created_at: string | null; updated_at: string | null;
};
export type ICloudCalendarSyncRun = {
  id: string; started_at: string | null; finished_at: string | null; status: string;
  trigger_source: string; triggered_by_user_id: string | null; account_count: number;
  events_scanned: number; events_matched: number; passes_created: number; passes_updated: number;
  passes_cancelled: number; passes_skipped: number; account_results: Record<string, unknown>[]; error: string | null;
};
export type ICloudCalendarPayload = { accounts: ICloudCalendarAccount[]; recent_sync_runs: ICloudCalendarSyncRun[] };
export type ICloudAuthStartResponse = {
  status: "connected" | "requires_2fa"; requires_2fa?: boolean; handshake_id?: string;
  apple_id?: string; detail?: string; account?: ICloudCalendarAccount;
};
export type ICloudAuthVerifyResponse = { status: "connected"; account: ICloudCalendarAccount };
export type AppriseUrlSummary = { id?: string; index: number; type: string; scheme: string; preview: string };
export type ESPHomeDeviceSummary = {
  id: string; name: string; host: string; port: number; timeout_seconds: number;
  enabled: boolean; encryption_key_configured: boolean;
};
export type UnifiProtectStatus = {
  configured: boolean; connected: boolean; last_error: string | null; camera_count: number;
  realtime_connected: boolean; realtime_error: string | null;
  websocket_states: Record<"private" | "events" | "devices", string>;
  host: string; port: number; verify_ssl: boolean; snapshot_width: number; snapshot_height: number;
};
export type UnifiProtectEvent = {
  id: string; type: string; camera_id: string; camera_name: string; start: string | null; end: string | null;
  score: number; smart_detect_types: string[]; thumbnail_url: string; video_url: string | null;
};
export type UnifiProtectAnalysis = { camera_id: string; provider: string; text: string; snapshot_retained: boolean };
export const integrationsApi = {
  getAccessDevices: (kind: AccessDevice["kind"], options: ApiRequestOptions = {}) =>
    api.get<AccessDeviceEligibility[]>(`/api/v1/access-devices?kind=${kind}`, options),
  confirmGateOpen: (payload: GateOpenPayload, label: string) => createActionConfirmation("gate.open", payload, {
    target_entity: "Gate", target_id: payload.target_device_key, target_label: label, reason: payload.reason
  }),
  openGate: (payload: GateOpenPayload, confirmationToken: string, options: ApiRequestOptions = {}) =>
    api.post<GateCommandReceipt>("/api/v1/integrations/gate/open", { ...payload, confirmation_token: confirmationToken }, options),
  confirmCoverCommand: (payload: CoverCommandPayload, label: string) => createActionConfirmation(`cover.${payload.action}`, payload, {
    target_entity: "Cover", target_id: payload.entity_id, target_label: label, reason: payload.reason
  }),
  commandCover: (payload: CoverCommandPayload, confirmationToken: string, options: ApiRequestOptions = {}) =>
    api.post<CoverCommandResponse>("/api/v1/integrations/cover/command", { ...payload, confirmation_token: confirmationToken }, options),
  getGateCommandByIntent: (intentId: string, options: ApiRequestOptions = {}) =>
    api.get<GateCommandReceipt>(gateCommandReceiptUrl(intentId), options),
  getCoverCommandByIntent: (intentId: string, options: ApiRequestOptions = {}) =>
    api.get<DeviceCommandReceipt>(`/api/v1/integrations/cover/commands?intent_id=${encodeURIComponent(intentId)}`, options),
  getGateCommands: (beforeId?: string, options: ApiRequestOptions = {}) => {
    const query = new URLSearchParams({ limit: "25" });
    if (beforeId) query.set("before_id", beforeId);
    return api.get<GateCommandPage>(`/api/v1/integrations/gate/commands?${query}`, options);
  },
  getCoverCommands: (beforeId?: string, options: ApiRequestOptions = {}) => {
    const query = new URLSearchParams({ limit: "25" });
    if (beforeId) query.set("before_id", beforeId);
    return api.get<CoverCommandPage>(`/api/v1/integrations/cover/commands?${query}`, options);
  },
  getGateCommand: (commandId: string, options: ApiRequestOptions = {}) =>
    api.get<GateCommandHistoryRecord>(`/api/v1/integrations/gate/commands/${encodeURIComponent(commandId)}`, options),
  getCoverCommand: (commandId: string, options: ApiRequestOptions = {}) =>
    api.get<DeviceCommandReceipt>(`/api/v1/integrations/cover/commands/${encodeURIComponent(commandId)}`, options),
  getHomeAssistantStatus: () => api.get<IntegrationStatus>("/api/v1/integrations/home-assistant/status"),
  getHomeAssistantDiscovery: () => api.get<HomeAssistantDiscovery>("/api/v1/integrations/home-assistant/entities"),
  getRecoveryTrackers: (options: ApiRequestOptions = {}) => api.get<RecoveryTrackerDiscovery>("/api/v1/integrations/home-assistant/recovery-trackers", options),
  getAccessDeviceStatus: () => api.get<IntegrationStatus>("/api/v1/integrations/gate/status"),
  getProtectStatus: () => api.get<UnifiProtectStatus>("/api/v1/integrations/unifi-protect/status"),
  getProtectCameras: async (forceRefresh = false) => {
    const refreshSuffix = forceRefresh ? "?refresh=true" : "";
    const result = await api.get<{ cameras: UnifiProtectCamera[] }>(`/api/v1/integrations/unifi-protect/cameras${refreshSuffix}`);
    return result.cameras;
  },
  getProtectEvents: async (cameraId: string) => {
    const result = await api.get<{ events: UnifiProtectEvent[] }>(`/api/v1/integrations/unifi-protect/events?camera_id=${encodeURIComponent(cameraId)}&limit=5`);
    return result.events;
  },
  analyzeProtectSnapshot: (cameraId: string, prompt: string) => api.post<UnifiProtectAnalysis>(`/api/v1/integrations/unifi-protect/cameras/${encodeURIComponent(cameraId)}/analyze`, { prompt }),
  getICloudCalendar: () => api.get<ICloudCalendarPayload>("/api/v1/integrations/icloud-calendar/accounts"),
  startICloudAuth: (appleId: string, password: string) => api.post<ICloudAuthStartResponse>("/api/v1/integrations/icloud-calendar/accounts/auth/start", { apple_id: appleId, password }),
  verifyICloudAuth: (handshakeId: string, code: string) => api.post<ICloudAuthVerifyResponse>("/api/v1/integrations/icloud-calendar/accounts/auth/verify", { handshake_id: handshakeId, code }),
  syncICloudCalendar: () => api.post<ICloudCalendarSyncRun>("/api/v1/integrations/icloud-calendar/sync"),
  removeICloudAccount: (accountId: string) => api.delete<ICloudCalendarAccount>(`/api/v1/integrations/icloud-calendar/accounts/${accountId}`),
  getAppriseUrls: async () => {
    const result = await api.get<{ urls: AppriseUrlSummary[] }>("/api/v1/integrations/apprise/urls");
    return result.urls;
  },
  getESPHomeDevices: async () => {
    const result = await api.get<{ devices: ESPHomeDeviceSummary[] }>("/api/v1/integrations/esphome/devices");
    return result.devices;
  },
  getUsers: () => api.get<UserAccount[]>("/api/v1/users")
};
export async function confirmIntegrationAction(action: string, payload: Record<string, unknown>, options: ActionConfirmationOptions) {
  return createActionConfirmation(action, payload, options);
}
export async function confirmedPost<T>(path: string, action: string, payload: Record<string, unknown>, options: ActionConfirmationOptions, body: Record<string, unknown> = payload) {
  const confirmation = await confirmIntegrationAction(action, payload, options);
  return api.post<T>(path, { ...body, confirmation_token: confirmation.confirmation_token });
}
export async function confirmedDelete<T = void>(path: string, action: string, payload: Record<string, unknown>, options: ActionConfirmationOptions, body: Record<string, unknown> = {}) {
  const confirmation = await confirmIntegrationAction(action, payload, options);
  return api.delete<T>(path, { ...body, confirmation_token: confirmation.confirmation_token });
}
export function testIntegrationSettings(payload: { integration: string; values: Record<string, unknown> }, label = payload.integration) {
  return confirmedPost<{ ok: boolean; message: string }>("/api/v1/settings/test", "integration.test", payload, {
    target_entity: "Integration",
    target_id: payload.integration,
    target_label: label,
    reason: "Run integration connection test"
  });
}
export async function addAppriseUrl(url: string) {
  const payload = { url };
  const result = await confirmedPost<{ urls: AppriseUrlSummary[] }>("/api/v1/integrations/apprise/urls", "apprise.url.create", payload, {
    target_entity: "AppriseURL",
    target_label: "Notification URL",
    reason: "Add notification URL"
  });
  return result.urls;
}
export async function removeAppriseUrl(url: AppriseUrlSummary) {
  const payload = { index: url.index };
  await confirmedDelete(`/api/v1/integrations/apprise/urls/${url.index}`, "apprise.url.delete", payload, {
    target_entity: "AppriseURL",
    target_id: String(url.index),
    target_label: url.preview || "Notification URL",
    reason: "Remove notification URL"
  });
  return integrationsApi.getAppriseUrls();
}
export function sendAppriseTestNotification() {
  const payload = {
    subject: "IACS test notification",
    severity: "info",
    message: "This is a test notification from API & Integrations."
  };
  return confirmedPost("/api/v1/integrations/notifications/test", "notification.test", payload, {
    target_entity: "Notification",
    target_label: payload.subject,
    reason: "Send Apprise test notification"
  });
}
export async function addESPHomeDevice(payload: {
  name: string;
  host: string;
  port: number;
  encryption_key: string;
  timeout_seconds: number;
  enabled: boolean;
}) {
  const result = await confirmedPost<{ devices: ESPHomeDeviceSummary[] }>("/api/v1/integrations/esphome/devices", "esphome.device.create", payload, {
    target_entity: "ESPHomeDevice",
    target_label: payload.name,
    reason: "Add ESPHome access device"
  });
  return result.devices;
}
export async function removeESPHomeDevice(device: ESPHomeDeviceSummary) {
  const payload = { device_id: device.id };
  const result = await confirmedDelete<{ devices: ESPHomeDeviceSummary[] }>(`/api/v1/integrations/esphome/devices/${encodeURIComponent(device.id)}`, "esphome.device.delete", payload, {
    target_entity: "ESPHomeDevice",
    target_id: device.id,
    target_label: device.name,
    reason: "Remove ESPHome access device"
  });
  return result.devices;
}
export function testESPHomeDevice(device: ESPHomeDeviceSummary) {
  const payload = { device_id: device.id };
  return confirmedPost<{ ok: boolean; cover_count: number; stream?: string }>(`/api/v1/integrations/esphome/devices/${encodeURIComponent(device.id)}/test`, "esphome.device.test", payload, {
    target_entity: "ESPHomeDevice",
    target_id: device.id,
    target_label: device.name,
    reason: "Test ESPHome access device"
  }, {});
}
