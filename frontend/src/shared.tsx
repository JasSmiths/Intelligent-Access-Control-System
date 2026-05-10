import React from "react";
import { createPortal } from "react-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Camera,
  CalendarDays,
  Car,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Command,
  ClipboardPaste,
  Construction,
  Copy,
  Database,
  DoorClosed,
  DoorOpen,
  Download,
  File as FileIcon,
  FileImage,
  FileText,
  Gauge,
  GitBranch,
  HardHat,
  Home,
  Key,
  LayoutDashboard,
  Lock,
  LogIn,
  LogOut,
  Loader2,
  MessageCircle,
  Menu,
  Moon,
  Monitor,
  MoreHorizontal,
  Play,
  PlugZap,
  Plus,
  Paperclip,
  Pencil,
  RefreshCcw,
  RefreshCw,
  Search,
  Send,
  Smile,
  Smartphone,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Save,
  Split,
  Sparkles,
  Sun,
  Terminal,
  Ticket,
  Trash2,
  Trophy,
  Type,
  Unlock,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  X,
  Zap
} from "lucide-react";



export type ScheduleTimeBlock = {
  start: string;
  end: string;
};

export type ScheduleTimeBlocks = Record<string, ScheduleTimeBlock[]>;

export type Schedule = {
  id: string;
  name: string;
  description: string | null;
  time_blocks: ScheduleTimeBlocks;
  created_at: string;
  updated_at: string;
};

export type Presence = {
  person_id: string;
  display_name: string;
  state: "present" | "exited" | "unknown";
  last_changed_at: string | null;
};

export type ExpectedPresencePerson = {
  person_id: string;
  display_name: string;
  confidence: number;
  evidence_days: number;
  observed_weekdays: number;
  typical_arrival: string | null;
  typical_departure: string | null;
};

export type ExpectedPresenceSummary = {
  date: string;
  timezone: string;
  generated_at: string;
  count: number;
  learning: boolean;
  coverage: {
    regular_candidates: number;
    learned_candidates: number;
    learning_population: number;
    ratio: number;
  };
  people: ExpectedPresencePerson[];
};

export type AccessEvent = {
  id: string;
  registration_number: string;
  direction: "entry" | "exit" | "denied";
  decision: "granted" | "denied";
  confidence: number;
  source: string;
  occurred_at: string;
  timing_classification: string;
  anomaly_count: number;
  visitor_pass_id: string | null;
  visitor_name: string | null;
  visitor_pass_mode: string | null;
  snapshot_url: string | null;
  snapshot_captured_at: string | null;
  snapshot_bytes: number | null;
  snapshot_width: number | null;
  snapshot_height: number | null;
  snapshot_camera: string | null;
};

export type AlertSeverity = "info" | "warning" | "critical";

export type AlertStatus = "open" | "resolved";

export type AlertResolver = {
  id: string;
  username: string;
  display_name: string;
};

export type Anomaly = {
  id: string;
  alert_ids: string[];
  grouped: boolean;
  event_id?: string | null;
  type: string;
  severity: AlertSeverity;
  status: AlertStatus;
  message: string;
  registration_number: string;
  count: number;
  local_date: string | null;
  created_at: string;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  resolved_by_user_id: string | null;
  resolved_by: AlertResolver | null;
  resolution_note: string | null;
  snapshot_url?: string | null;
  snapshot_captured_at?: string | null;
  snapshot_bytes?: number | null;
};

export type Person = {
  id: string;
  first_name: string;
  last_name: string;
  display_name: string;
  pronouns: "he/him" | "she/her" | null;
  profile_photo_data_url: string | null;
  group_id: string | null;
  group: string | null;
  category: string | null;
  schedule_id: string | null;
  schedule: string | null;
  is_active: boolean;
  notes: string | null;
  garage_door_entity_ids: string[];
  home_assistant_mobile_app_notify_service: string | null;
  vehicles: Vehicle[];
};

export type Vehicle = {
  id: string;
  registration_number: string;
  vehicle_photo_data_url?: string | null;
  description: string | null;
  make: string | null;
  model: string | null;
  color?: string | null;
  fuel_type?: string | null;
  mot_status?: string | null;
  tax_status?: string | null;
  mot_expiry?: string | null;
  tax_expiry?: string | null;
  last_dvla_lookup_date?: string | null;
  person_id?: string | null;
  owner?: string | null;
  person_ids?: string[];
  owners?: string[];
  schedule_id?: string | null;
  schedule?: string | null;
  is_active?: boolean;
};

export type Group = {
  id: string;
  name: string;
  category: string;
  subtype: string | null;
  description: string | null;
  people_count: number;
};

export type IntegrationStatus = {
  configured: boolean;
  connected?: boolean;
  degraded?: boolean;
  last_error?: string | null;
  last_connected_at?: string | null;
  last_failure_at?: string | null;
  state_refreshed_at?: string | null;
  listener_running?: boolean;
  gate_entity_id: string | null;
  gate_entities?: HomeAssistantManagedCover[];
  garage_door_entities?: HomeAssistantManagedCover[];
  default_media_player: string | null;
  last_gate_state: string;
  current_gate_state?: string;
  front_door_state?: string;
  back_door_state?: string;
  main_garage_door_state?: string;
  mums_garage_door_state?: string;
};

export type MaintenanceStatus = {
  is_active: boolean;
  enabled_by: string | null;
  enabled_at: string | null;
  source: string | null;
  reason: string | null;
  duration_seconds: number;
  duration_label: string | null;
  ha_entity_id?: string;
};

export type ActionConfirmation = {
  confirmation_id: string;
  confirmation_token: string;
  action: string;
  expires_at: string;
};

export type ActionConfirmationOptions = {
  target_entity?: string;
  target_id?: string;
  target_label?: string;
  reason?: string;
  metadata?: Record<string, unknown>;
};

export type RealtimeMessage = {
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type AuditLog = {
  id: string;
  timestamp: string;
  category: string;
  action: string;
  actor: string;
  actor_user_id: string | null;
  target_entity: string | null;
  target_id: string | null;
  target_label: string | null;
  diff: Record<string, unknown>;
  metadata: Record<string, unknown>;
  outcome: string;
  level: string;
  trace_id: string | null;
  request_id: string | null;
};

export type UserRole = "admin" | "standard";

export type SystemSetting = {
  key: string;
  category: string;
  value: unknown;
  is_secret: boolean;
  description: string | null;
};

export type SettingsMap = Record<string, unknown>;

export type HomeAssistantEntity = {
  entity_id: string;
  name: string | null;
  state: string | null;
  device_class?: string | null;
};

export type HomeAssistantManagedCover = {
  entity_id: string;
  name: string;
  state?: string | null;
  enabled?: boolean;
  schedule_id?: string | null;
  open_service?: string;
  close_service?: string;
};

export type HomeAssistantMobileAppService = {
  service_id: string;
  name: string | null;
  description: string | null;
};

export type HomeAssistantMobileAppSuggestion = {
  person_id: string;
  first_name: string;
  last_name: string;
  display_name: string;
  suggested_service_id: string | null;
  suggested_name: string | null;
  confidence: number;
};

export type HomeAssistantDiscovery = {
  cover_entities: HomeAssistantEntity[];
  gate_suggestions?: HomeAssistantManagedCover[];
  garage_door_suggestions?: HomeAssistantManagedCover[];
  media_player_entities: HomeAssistantEntity[];
  mobile_app_notification_services: HomeAssistantMobileAppService[];
  mobile_app_notification_mappings: HomeAssistantMobileAppSuggestion[];
};

export type NotificationChannelId = "mobile" | "in_app" | "voice" | "discord" | "whatsapp";

export type NotificationTriggerOption = {
  value: string;
  label: string;
  severity: "info" | "warning" | "critical";
  description: string;
};

export type TooltipPositionState = {
  left: number;
  placement: "top" | "bottom";
  top: number;
};

export type UnifiProtectCamera = {
  id: string;
  mac?: string | null;
  name: string;
  model: string | null;
  state: string | null;
  is_recording: boolean;
  is_recording_enabled: boolean;
  is_video_ready: boolean;
  is_motion_detected: boolean;
  is_smart_detected: boolean;
  last_motion_at: string | null;
  last_smart_detect_at: string | null;
  snapshot_url: string;
  channels: Array<{
    id: string;
    name: string;
    width: number | null;
    height: number | null;
    fps: number | null;
    bitrate: number | null;
    is_rtsp_enabled: boolean;
    is_package: boolean;
  }>;
  feature_flags: {
    has_smart_detect: boolean;
    has_package_camera: boolean;
    has_mic: boolean;
    smart_detect_types: string[];
    smart_detect_audio_types: string[];
  };
  detections: {
    active: string[];
  };
  smart_detect_zones: Array<{
    id: number | string | null;
    name: string;
    object_types: string[];
  }>;
};

export type UserAccount = {
  id: string;
  username: string;
  first_name: string;
  last_name: string;
  full_name: string;
  profile_photo_data_url: string | null;
  email: string | null;
  mobile_phone_number: string | null;
  role: UserRole;
  is_active: boolean;
  last_login_at: string | null;
  person_id: string | null;
  preferences: ProfilePreferences & Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ProfilePreferences = {
  sidebarCollapsed: boolean;
};

export type ViewKey =
  | "dashboard"
  | "people"
  | "groups"
  | "schedules"
  | "passes"
  | "vehicles"
  | "top_charts"
  | "events"
  | "alerts"
  | "reports"
  | "integrations"
  | "logs"
  | "settings"
  | "settings_general"
  | "settings_auth"
  | "alfred_training"
  | "settings_automations"
  | "settings_notifications"
  | "settings_lpr"
  | "users";

export type NavigateOptions = {
  replace?: boolean;
  search?: string;
  hash?: string;
};

export type NavigateToView = (nextView: ViewKey, options?: NavigateOptions) => void;

export const api = {
  async get<T>(path: string): Promise<T> {
    const response = await fetch(path, { credentials: "include" });
    if (!response.ok) throw await apiError(response);
    return response.json() as Promise<T>;
  },
  async post<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw await apiError(response);
    return response.json() as Promise<T>;
  },
  async patch<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: body ? JSON.stringify(body) : undefined
    });
    if (!response.ok) throw await apiError(response);
    return response.json() as Promise<T>;
  },
  async delete<T = void>(path: string): Promise<T> {
    const response = await fetch(path, { method: "DELETE", credentials: "include" });
    if (!response.ok) throw await apiError(response);
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }
};

export async function createActionConfirmation(
  action: string,
  payload: Record<string, unknown>,
  options: ActionConfirmationOptions = {}
): Promise<ActionConfirmation> {
  return api.post<ActionConfirmation>("/api/v1/action-confirmations", {
    action,
    payload,
    ...options
  });
}

export async function apiError(response: Response) {
  const statusLabel = `${response.status} ${response.statusText || "Request failed"}`;
  let detail: string | null = null;
  const body = await response.text().catch(() => "");

  if (body.trim()) {
    try {
      const payload = JSON.parse(body) as unknown;
      if (payload && typeof payload === "object" && !Array.isArray(payload)) {
        const record = payload as Record<string, unknown>;
        detail =
          describeApiErrorDetail(record.detail) ||
          describeApiErrorDetail(record.message) ||
          describeApiErrorDetail(record.error);
      } else {
        detail = describeApiErrorDetail(payload);
      }
    } catch {
      detail = body.trim();
    }
  }

  return new Error(detail && detail !== statusLabel ? `${statusLabel}: ${detail}` : statusLabel);
}

export function describeApiErrorDetail(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const parts = value.map(describeApiErrorDetail).filter((part): part is string => Boolean(part));
    return parts.length ? parts.join("; ") : null;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    const message =
      describeApiErrorDetail(record.msg) ||
      describeApiErrorDetail(record.message) ||
      describeApiErrorDetail(record.detail);
    const location = Array.isArray(record.loc)
      ? record.loc.filter(Boolean).join(".")
      : typeof record.loc === "string"
        ? record.loc
        : null;
    if (message && location) return `${location}: ${message}`;
    if (message) return message;
    return JSON.stringify(record);
  }
  return null;
}

export function wsUrl(path: string) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${path}`;
}

export function formatFileSize(size: number) {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${size} B`;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function stringPayload(value: unknown) {
  return typeof value === "string" ? value : "";
}

export function nullableString(value: unknown) {
  return typeof value === "string" && value ? value : null;
}

export function numberPayload(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function PanelHeader({ title, action, actionKind, onAction }: { title: string; action?: string; actionKind?: "link" | "select"; onAction?: () => void }) {
  return (
    <div className="panel-header">
      <h2>{title}</h2>
      {action ? (
        actionKind === "select" ? (
          <button className="panel-select" type="button">
            {action}
            <ChevronDown size={14} />
          </button>
        ) : (
          <button className="panel-link" onClick={onAction} type="button">{action}</button>
        )
      ) : null}
    </div>
  );
}

export function activeManagedCovers(entities: HomeAssistantManagedCover[] | undefined) {
  return (entities ?? []).filter((entity) => entity.enabled !== false);
}

export function visitorEventDisplayName(event: Pick<AccessEvent, "visitor_name">) {
  const name = (event.visitor_name || "").trim();
  if (!name) return "";
  const parts = name.split(":").map((part) => part.trim()).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : name;
}

export function isActionableAlert(alert: Anomaly) {
  return alert.status === "open" && (alert.severity === "warning" || alert.severity === "critical");
}

export function alertSeverityTone(severity: AlertSeverity): BadgeTone {
  if (severity === "critical") return "red";
  if (severity === "warning") return "amber";
  return "blue";
}

export function alertSeverityLabel(severity: AlertSeverity) {
  if (severity === "info") return "Informational";
  return titleCase(severity);
}

export function MetricCard({ icon: Icon, label, value, detail, tone }: { icon: React.ElementType; label: string; value: string; detail: string; tone: BadgeTone }) {
  return (
    <div className="card metric-card">
      <div className={`metric-icon ${tone}`}>
        <Icon size={20} />
      </div>
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
      <span className="metric-detail">{detail}</span>
    </div>
  );
}

export function CardHeader({ icon: Icon, title, action }: { icon: React.ElementType; title: string; action?: React.ReactNode }) {
  return (
    <div className="card-header">
      <div className="card-title">
        <Icon size={17} />
        <h2>{title}</h2>
      </div>
      {action}
    </div>
  );
}

export const scheduleDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function scheduleDefaultPolicyDisplay(value: unknown) {
  return String(value ?? "allow").trim().toLowerCase() === "deny" ? "Never Allow" : "Always Allow";
}

export function useScheduleDefaultPolicyOptionLabel() {
  const accessSettings = useSettings("access");
  return `Default Policy - ${scheduleDefaultPolicyDisplay(accessSettings.values.schedule_default_policy)}`;
}

export const llmProviderDefinitions = [
  { key: "local", label: "Local fallback", agentCapable: false },
  { key: "openai", label: "OpenAI", agentCapable: true },
  { key: "gemini", label: "Gemini", agentCapable: true },
  { key: "anthropic", label: "Claude", agentCapable: true },
  { key: "ollama", label: "Ollama", agentCapable: true }
] as const;

export type LlmProviderKey = typeof llmProviderDefinitions[number]["key"];

export function normalizeLlmProvider(value: unknown): LlmProviderKey {
  const provider = String(value || "local").toLowerCase();
  if (provider === "claude") return "anthropic";
  return llmProviderDefinitions.some((option) => option.key === provider) ? provider as LlmProviderKey : "local";
}

export function isLlmProviderConfigured(key: LlmProviderKey, values: SettingsMap): boolean {
  if (key === "local") return true;
  if (key === "openai") return Boolean(values.openai_api_key);
  if (key === "gemini") return Boolean(values.gemini_api_key);
  if (key === "anthropic") return Boolean(values.anthropic_api_key);
  if (key === "ollama") return Boolean(values.ollama_base_url);
  return false;
}

export function levelTone(level: string | null | undefined): BadgeTone {
  const normalized = String(level || "").toLowerCase();
  if (normalized === "error" || normalized === "critical") return "red";
  if (normalized === "warning" || normalized === "warn") return "amber";
  if (normalized === "purple") return "purple";
  if (normalized === "success" || normalized === "ok") return "green";
  return "blue";
}

export const notificationChannelMeta: Record<NotificationChannelId, {
  label: string;
  icon: React.ElementType;
  tone: BadgeTone;
  description: string;
}> = {
  mobile: {
    label: "Mobile Notification",
    icon: Smartphone,
    tone: "blue",
    description: "Apprise or Home Assistant mobile app delivery."
  },
  in_app: {
    label: "In-App Notification",
    icon: Monitor,
    tone: "green",
    description: "Realtime dashboard alert for signed-in users."
  },
  voice: {
    label: "Voice Notification",
    icon: Volume2,
    tone: "amber",
    description: "Home Assistant TTS announcement to media players."
  },
  discord: {
    label: "Discord Notification",
    icon: MessageCircle,
    tone: "purple",
    description: "Discord embed delivery to selected channels."
  },
  whatsapp: {
    label: "WhatsApp Message",
    icon: MessageCircle,
    tone: "green",
    description: "WhatsApp Cloud API delivery to Admin users or dynamic phone-number variables."
  }
};

export function toDateTimeLocal(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

export function fromDateTimeLocal(value: string) {
  return value ? new Date(value).toISOString() : "";
}

export function notificationEventLabel(value: string, triggerByValue?: Map<string, NotificationTriggerOption>) {
  return triggerByValue?.get(value)?.label ?? titleCase(value);
}

export function Toolbar({
  title,
  count,
  badge,
  icon: Icon,
  children
}: {
  title: string;
  count?: number;
  badge?: React.ReactNode;
  icon: React.ElementType;
  children?: React.ReactNode;
}) {
  const badgeContent = badge ?? (typeof count === "number" ? count : null);
  return (
    <div className="toolbar">
      <div className="card-title">
        <Icon size={18} />
        <h2>{title}</h2>
        {badgeContent !== null ? <Badge tone="gray">{badgeContent}</Badge> : null}
      </div>
      {children}
    </div>
  );
}

export function EmptyState({ icon: Icon, label }: { icon: React.ElementType; label: string }) {
  return (
    <div className="empty-state">
      <Icon size={22} />
      <span>{label}</span>
    </div>
  );
}

export type BadgeTone = "green" | "gray" | "amber" | "red" | "blue" | "purple";

export type SettingFieldDefinition = {
  key: string;
  label: string;
  type?: "text" | "password" | "number" | "textarea" | "select";
  options?: string[];
  min?: number;
  max?: number;
  step?: number;
  href?: string;
  help?: string;
};

export const secretSettingKeys = new Set([
  "home_assistant_token",
  "apprise_urls",
  "discord_bot_token",
  "whatsapp_access_token",
  "whatsapp_webhook_verify_token",
  "whatsapp_app_secret",
  "dvla_api_key",
  "unifi_protect_username",
  "unifi_protect_password",
  "unifi_protect_api_key",
  "openai_api_key",
  "gemini_api_key",
  "anthropic_api_key"
]);

export const discordListSettingKeys = new Set([
  "discord_guild_allowlist",
  "discord_channel_allowlist",
  "discord_user_allowlist",
  "discord_role_allowlist",
  "discord_admin_role_ids"
]);

export const listSettingKeys = new Set([
  ...discordListSettingKeys,
  "lpr_allowed_smart_zones"
]);

export function SettingField({
  field,
  isConfiguredSecret = false,
  value,
  onChange
}: {
  field: SettingFieldDefinition;
  isConfiguredSecret?: boolean;
  value: string;
  onChange: (value: string) => void;
}) {
  const secretPlaceholder = isConfiguredSecret ? "Configured. Paste a new value to replace it." : undefined;
  return (
    <label className="field">
      <span>
        {field.label}
        {field.href ? <a href={field.href} rel="noreferrer" target="_blank">Get key</a> : null}
      </span>
      {field.type === "textarea" ? (
        <textarea value={value} onChange={(event) => onChange(event.target.value)} placeholder={secretPlaceholder} rows={4} />
      ) : field.type === "select" ? (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          {field.options?.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      ) : (
        <div className="field-control">
          {field.type === "password" ? <Key size={17} /> : <SlidersHorizontal size={17} />}
          <input
            min={field.min}
            max={field.max}
            step={field.step}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={field.type === "password" ? secretPlaceholder ?? "Leave blank to keep existing secret" : undefined}
            type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
          />
        </div>
      )}
      {field.help ? <small className="field-hint">{field.help}</small> : null}
      {isConfiguredSecret ? <small className="field-hint">A value is saved securely. Leave this blank to keep the current configuration.</small> : null}
    </label>
  );
}

export function useSettings(category?: string) {
  const [settingsRows, setSettingsRows] = React.useState<SystemSetting[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const values = React.useMemo(() => {
    return settingsRows.reduce<SettingsMap>((acc, row) => {
      acc[row.key] = row.value;
      return acc;
    }, {});
  }, [settingsRows]);

  const load = React.useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const suffix = category ? `?category=${encodeURIComponent(category)}` : "";
      setSettingsRows(await api.get<SystemSetting[]>(`/api/v1/settings${suffix}`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load settings");
    } finally {
      setLoading(false);
    }
  }, [category]);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const save = React.useCallback(async (updates: Record<string, unknown>) => {
    await api.patch<SystemSetting[]>("/api/v1/settings", { values: updates });
    await load();
  }, [load]);

  return {
    rows: settingsRows,
    values,
    loading,
    error,
    save,
    reload: load
  };
}

export function stringifySetting(value: unknown) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.map((item) => String(item)).join("\n");
  if (typeof value === "object" && value !== null) return JSON.stringify(value, null, 2);
  return value == null ? "" : String(value);
}

export function titleFromEntityId(entityId: string) {
  return entityId.split(".", 2).pop()?.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()) || entityId;
}

export function coerceSettingsPayload(form: Record<string, string>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(form)) {
    if (
      secretSettingKeys.has(key)
    ) {
      if (!value.trim()) continue;
    }
    if (key === "home_assistant_gate_entities" || key === "home_assistant_garage_door_entities") {
      try {
        const parsed = value.trim() ? JSON.parse(value) : [];
        payload[key] = Array.isArray(parsed) ? parsed : [];
      } catch {
        payload[key] = [];
      }
    } else if (listSettingKeys.has(key)) {
      payload[key] = value.replace(/,/g, "\n").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    } else if (["auth_cookie_secure", "unifi_protect_verify_ssl", "discord_allow_direct_messages", "discord_require_mention", "whatsapp_enabled"].includes(key)) {
      payload[key] = value === "true";
    } else if ([
      "auth_access_token_minutes",
      "auth_remember_days",
      "lpr_debounce_quiet_seconds",
      "lpr_debounce_max_seconds",
      "lpr_vehicle_session_idle_seconds",
      "lpr_similarity_threshold",
      "llm_timeout_seconds",
      "dvla_timeout_seconds",
      "unifi_protect_port",
      "unifi_protect_snapshot_width",
      "unifi_protect_snapshot_height"
    ].includes(key)) {
      payload[key] = Number(value);
    } else {
      payload[key] = value;
    }
  }
  return payload;
}

export function Badge({ children, tone }: { children: React.ReactNode; tone: BadgeTone }) {
  return <span className={`badge ${tone}`}><span className="badge-label">{children}</span></span>;
}

export function matches(value: string, query: string) {
  return !query.trim() || value.toLowerCase().includes(query.trim().toLowerCase());
}

export function titleCase(value: string | null | undefined) {
  return String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function initials(value: string) {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  return (parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "");
}

export function displayUserName(user: Pick<UserAccount, "first_name" | "last_name" | "full_name">) {
  return `${user.first_name || ""} ${user.last_name || ""}`.trim() || user.full_name;
}

export function userInitials(user: Pick<UserAccount, "first_name" | "last_name" | "full_name">) {
  const first = user.first_name?.trim()[0] ?? "";
  const last = user.last_name?.trim()[0] ?? "";
  return (first + last || initials(user.full_name)).toUpperCase();
}

export function UserAvatar({ user, size = "normal" }: { user: UserAccount; size?: "normal" | "large" }) {
  return (
    <span className={size === "large" ? "profile-photo large" : "profile-photo"} aria-label={displayUserName(user)}>
      {user.profile_photo_data_url ? <img alt="" src={user.profile_photo_data_url} /> : userInitials(user)}
    </span>
  );
}

export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Unable to read profile image"));
    reader.readAsDataURL(file);
  });
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short"
  }).format(new Date(value));
}
