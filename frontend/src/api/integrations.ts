import { api, createActionConfirmation } from "./client";
import type { ActionConfirmationOptions, HomeAssistantDiscovery, IntegrationStatus, UnifiProtectCamera, UserAccount } from "./types";
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
export type DiscordStatus = {
  configured: boolean; connected: boolean; library_available: boolean; guild_count: number; channel_count: number;
  default_notification_channel_id: string; allow_direct_messages: boolean; require_mention: boolean;
  last_error: string | null; ready_at: string | null;
};
export type DiscordChannel = { id: string; guild_id: string; name: string; label: string };
export type DiscordIdentity = {
  id: string; provider_user_id: string; provider_display_name: string;
  user_id: string | null; user_label: string | null; person_id: string | null;
  person_label: string | null; last_seen_at: string | null;
};
export type WhatsAppStatus = {
  enabled: boolean; configured: boolean; webhook_configured: boolean; signature_configured: boolean;
  phone_number_id: string; business_account_id: string; graph_api_version: string;
  visitor_pass_template_name: string; visitor_pass_template_language: string;
  admin_target_count: number; last_error: string | null;
};
export type UnifiProtectStatus = {
  configured: boolean; connected: boolean; last_error: string | null; camera_count: number;
  host: string; port: number; verify_ssl: boolean; snapshot_width: number; snapshot_height: number;
};
export type UnifiProtectEvent = {
  id: string; type: string; camera_id: string; camera_name: string; start: string | null; end: string | null;
  score: number; smart_detect_types: string[]; thumbnail_url: string; video_url: string | null;
};
export type UnifiProtectAnalysis = { camera_id: string; provider: string; text: string; snapshot_retained: boolean };
export type UnifiProtectUpdateStatus = {
  package: string; current_version: string; latest_version: string; update_available: boolean;
  active_package: { mode: string; version?: string | null; path?: string | null; installed_at?: string | null };
  installed_overlays: Array<{ version: string; path: string }>; latest_summary?: Record<string, unknown>;
};
export type UnifiProtectReleaseNotes = { source: string; title: string; body: string; published_at?: string | null; html_url?: string | null };
export type UnifiProtectUpdateAnalysis = {
  package: string; current_version: string; target_version: string; latest_version: string;
  update_available: boolean; provider: string; analysis: string; release_notes: UnifiProtectReleaseNotes;
};
export type UnifiProtectBackup = {
  id: string; created_at: string; reason: string; package_version: string;
  settings_count: number; size_bytes: number; download_url: string;
  active_package?: { mode: string; version?: string | null };
};
export type UnifiProtectUpdateApplyResult = {
  ok: boolean; previous_version: string; current_version: string; target_version: string;
  backup: UnifiProtectBackup; verification: { package_version?: string; camera_count?: number; snapshot_bytes?: number };
};
export type DependencyRiskStatus = "safe" | "warning" | "breaking" | "unknown";
export type DependencyPackage = {
  id: string; ecosystem: string; package_name: string; normalized_name: string;
  current_version: string | null; latest_version: string | null; dependant_area: string;
  manifest_path: string | null; manifest_section: string | null; requirement_spec: string | null;
  is_direct: boolean; is_enabled: boolean; update_available: boolean;
  risk_status: DependencyRiskStatus | string; last_checked_at: string | null;
  metadata: Record<string, unknown>; latest_analysis: DependencyAnalysis | null;
};
export type DependencyAnalysis = {
  id: string; dependency_id: string; target_version: string; provider: string; model: string | null;
  verdict: DependencyRiskStatus | string; summary_markdown: string; changelog_source: string | null;
  changelog_markdown: string | null; usage_summary: { reference_count?: number; references?: Array<{ path: string; line: number; text: string }> };
  breaking_changes: Array<Record<string, unknown>>; verification_steps: string[]; suggested_diff: string | null; created_at: string;
};
export type DependencyBackup = {
  id: string; dependency_id: string | null; package_name: string; ecosystem: string;
  version: string | null; reason: string; archive_path: string; storage_root: string;
  checksum_sha256: string; size_bytes: number; created_at: string; restored_at: string | null;
  metadata: Record<string, unknown>;
};
export type DependencyJob = {
  id: string; dependency_id: string | null; kind: string; status: string; phase: string | null;
  actor: string; target_version: string | null; backup_id: string | null; stdout_log_path: string | null;
  started_at: string | null; ended_at: string | null; result: Record<string, unknown>; error: string | null; trace_id: string | null;
};
export type DependencyCheckAllResult = {
  ok: boolean; checked: number; failed: number; updates: number; direct_only: boolean;
  errors: Array<{ dependency_id: string; error: string }>; packages?: DependencyPackage[];
};
export type DependencyStorageStatus = {
  mode: "local" | "nfs" | "samba" | string; mount_source: string; mount_options: string;
  mount_options_configured: boolean; mount_options_redacted: boolean; config_status: "active" | "pending_reboot" | "error" | string;
  backup_root: string; exists: boolean; writable: boolean; free_bytes: number; min_free_bytes: number;
  retention_days?: string; ok: boolean; detail: string;
};
export type DependencyFailureDiagnosis = {
  category: string; title: string; summary: string; safe_state: string; retry_recommendation: string;
  actions: string[]; affected_packages: string[]; command?: string; technical_detail?: string;
};
export type DependencyJobEvent = {
  type: string; job_id?: string; created_at?: string; phase?: string;
  message?: string; diagnosis?: DependencyFailureDiagnosis; result?: Record<string, unknown>;
};
export const DEPENDENCY_JOB_EVENT_LIMIT = 200;
export const DEPENDENCY_JOB_MESSAGE_LIMIT = 2000;
export function compactDependencyJobEvent(event: DependencyJobEvent): DependencyJobEvent {
  if (typeof event.message !== "string" || event.message.length <= DEPENDENCY_JOB_MESSAGE_LIMIT) return event;
  return { ...event, message: `${event.message.slice(0, DEPENDENCY_JOB_MESSAGE_LIMIT)}... [truncated]` };
}
export type DependencyConfirmAction = { kind: "apply" } | { kind: "restore"; backup: DependencyBackup };
export type DiscordBundle = { status: DiscordStatus; channels: DiscordChannel[]; identities: DiscordIdentity[] };
export type DependencyBundle = { packages: DependencyPackage[]; storage: DependencyStorageStatus };
export const integrationsApi = {
  getHomeAssistantStatus: () => api.get<IntegrationStatus>("/api/v1/integrations/home-assistant/status"),
  getHomeAssistantDiscovery: () => api.get<HomeAssistantDiscovery>("/api/v1/integrations/home-assistant/entities"),
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
  getProtectUpdateStatus: () => api.get<UnifiProtectUpdateStatus>("/api/v1/integrations/unifi-protect/update/status"),
  getProtectUpdateData: async () => {
    const [status, backupResult] = await Promise.all([
      api.get<UnifiProtectUpdateStatus>("/api/v1/integrations/unifi-protect/update/status"),
      api.get<{ backups: UnifiProtectBackup[] }>("/api/v1/integrations/unifi-protect/backups")
    ]);
    return { status, backups: backupResult.backups };
  },
  analyzeProtectUpdate: (targetVersion?: string) => api.post<UnifiProtectUpdateAnalysis>("/api/v1/integrations/unifi-protect/update/analyze", { target_version: targetVersion || undefined }),
  getICloudCalendar: () => api.get<ICloudCalendarPayload>("/api/v1/integrations/icloud-calendar/accounts"),
  startICloudAuth: (appleId: string, password: string) => api.post<ICloudAuthStartResponse>("/api/v1/integrations/icloud-calendar/accounts/auth/start", { apple_id: appleId, password }),
  verifyICloudAuth: (handshakeId: string, code: string) => api.post<ICloudAuthVerifyResponse>("/api/v1/integrations/icloud-calendar/accounts/auth/verify", { handshake_id: handshakeId, code }),
  syncICloudCalendar: () => api.post<ICloudCalendarSyncRun>("/api/v1/integrations/icloud-calendar/sync"),
  removeICloudAccount: (accountId: string) => api.delete<ICloudCalendarAccount>(`/api/v1/integrations/icloud-calendar/accounts/${accountId}`),
  getDiscordBundle: async (): Promise<DiscordBundle> => {
    const [status, channelResult, identityResult] = await Promise.all([
      api.get<DiscordStatus>("/api/v1/integrations/discord/status"),
      api.get<{ channels: DiscordChannel[] }>("/api/v1/integrations/discord/channels"),
      api.get<{ identities: DiscordIdentity[] }>("/api/v1/integrations/discord/identities")
    ]);
    return { status, channels: channelResult.channels, identities: identityResult.identities };
  },
  updateDiscordIdentity: (identityId: string, body: { user_id: string | null; person_id: string | null }) => api.patch<DiscordIdentity>(`/api/v1/integrations/discord/identities/${identityId}`, body),
  getWhatsAppStatus: () => api.get<WhatsAppStatus>("/api/v1/integrations/whatsapp/status"),
  getDependencyUpdates: async (): Promise<DependencyBundle> => {
    const [packagesResult, storage] = await Promise.all([
      api.get<{ packages: DependencyPackage[] }>("/api/v1/dependency-updates/packages"),
      api.get<DependencyStorageStatus>("/api/v1/dependency-updates/storage/status")
    ]);
    return { packages: packagesResult.packages, storage };
  },
  syncDependencies: () => api.post("/api/v1/dependency-updates/sync", {}),
  checkDependencies: () => api.post<DependencyCheckAllResult>("/api/v1/dependency-updates/check", { direct_only: false }),
  getDependencyBackups: async (dependencyId: string) => {
    const result = await api.get<{ backups: DependencyBackup[] }>(`/api/v1/dependency-updates/packages/${dependencyId}/backups`);
    return result.backups;
  },
  getDependencyPackages: async () => {
    const result = await api.get<{ packages: DependencyPackage[] }>("/api/v1/dependency-updates/packages");
    return result.packages;
  },
  getDependencyJob: (jobId: string) => api.get<DependencyJob>(`/api/v1/dependency-updates/jobs/${jobId}`),
  checkDependency: (dependencyId: string) => api.post<DependencyPackage>(`/api/v1/dependency-updates/packages/${dependencyId}/check`, {}),
  analyzeDependency: (dependencyId: string, targetVersion?: string | null) => api.post<DependencyAnalysis>(`/api/v1/dependency-updates/packages/${dependencyId}/analyze`, { target_version: targetVersion || undefined }),
  applyDependency: (dependencyId: string, targetVersion?: string | null) => {
    const body = { target_version: targetVersion || undefined };
    return confirmedPost<DependencyJob>(
      `/api/v1/dependency-updates/packages/${dependencyId}/apply`,
      "dependency_update.apply",
      { dependency_id: dependencyId, ...body },
      { target_entity: "ExternalDependency", target_id: dependencyId, target_label: "Dependency update", reason: "Apply dependency update" },
      body
    );
  },
  restoreDependencyBackup: (backupId: string) =>
    confirmedPost<DependencyJob>(
      `/api/v1/dependency-updates/backups/${backupId}/restore`,
      "dependency_update.restore",
      { backup_id: backupId },
      { target_entity: "DependencyUpdateBackup", target_id: backupId, target_label: "Dependency backup", reason: "Restore dependency backup" },
      {}
    ),
  saveDependencyStorage: (payload: Record<string, unknown>) =>
    confirmedPost(
      "/api/v1/dependency-updates/storage/config",
      "dependency_update.storage.configure",
      payload,
      { target_entity: "DependencyUpdateStorage", target_label: "Update backup storage", reason: "Configure dependency backup storage" }
    ),
  validateDependencyStorage: () =>
    confirmedPost(
      "/api/v1/dependency-updates/storage/validate",
      "dependency_update.storage.validate",
      {},
      { target_entity: "DependencyUpdateStorage", target_label: "Update backup storage", reason: "Validate dependency backup storage" }
    ),
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
export function sendDiscordTestNotification(channelId: string | undefined) {
  const payload = {
    channel_id: channelId,
    message: "This is a test Discord notification from API & Integrations."
  };
  return confirmedPost("/api/v1/integrations/discord/test", "discord.test_notification", payload, {
    target_entity: "Discord",
    target_id: payload.channel_id,
    target_label: "Discord test notification",
    reason: "Send Discord test notification"
  });
}
export function sendWhatsAppTestMessage(values: Record<string, unknown>) {
  const payload = {
    message: "This is a test WhatsApp notification from API & Integrations.",
    values
  };
  return confirmedPost("/api/v1/integrations/whatsapp/test", "whatsapp.test_message", payload, {
    target_entity: "WhatsApp",
    target_label: "WhatsApp test message",
    reason: "Send WhatsApp test message"
  });
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
export function createProtectBackup() {
  return confirmedPost<UnifiProtectBackup>("/api/v1/integrations/unifi-protect/backups", "unifi_protect.backup.create", {}, {
    target_entity: "UniFiProtect",
    target_label: "UniFi Protect settings backup",
    reason: "Create UniFi Protect backup"
  }, {});
}
export function applyProtectUpdate(targetVersion: string) {
  const payload = { target_version: targetVersion };
  return confirmedPost<UnifiProtectUpdateApplyResult>("/api/v1/integrations/unifi-protect/update/apply", "unifi_protect.update.apply", payload, {
    target_entity: "UniFiProtect",
    target_label: targetVersion,
    reason: "Apply UniFi Protect package update"
  });
}
export function restoreProtectBackup(backup: UnifiProtectBackup) {
  const payload = { backup_id: backup.id };
  return confirmedPost(`/api/v1/integrations/unifi-protect/backups/${encodeURIComponent(backup.id)}/restore`, "unifi_protect.backup.restore", payload, {
    target_entity: "UniFiProtect",
    target_id: backup.id,
    target_label: backup.created_at || backup.id,
    reason: "Restore UniFi Protect backup"
  }, {});
}
export function deleteProtectBackup(backup: UnifiProtectBackup) {
  const payload = { backup_id: backup.id };
  return confirmedDelete(`/api/v1/integrations/unifi-protect/backups/${encodeURIComponent(backup.id)}`, "unifi_protect.backup.delete", payload, {
    target_entity: "UniFiProtect",
    target_id: backup.id,
    target_label: backup.created_at || backup.id,
    reason: "Delete UniFi Protect backup"
  });
}
