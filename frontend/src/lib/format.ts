import type { AccessEvent, AlertSeverity, Anomaly, HomeAssistantManagedCover, MovementSagaSummary, NotificationTriggerOption, SettingsMap, UserAccount } from "../api/types";
import type { BadgeTone } from "../ui/primitives";
import { useSettings } from "./settings";
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
export const scheduleDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function scheduleDefaultPolicyDisplay(value: unknown) {
  return String(value ?? "allow").trim().toLowerCase() === "deny" ? "Never Allow" : "Always Allow";
}
export function useScheduleDefaultPolicyOptionLabel() {
  const accessSettings = useSettings("access");
  return `Default Policy - ${scheduleDefaultPolicyDisplay(accessSettings.values.schedule_default_policy)}`;
}
export const llmProviderDefinitions = [
  { key: "local", label: "Local diagnostics", agentCapable: false },
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
export function movementSagaDisplay(summary: MovementSagaSummary | null | undefined): { label: string; tone: BadgeTone } | null {
  if (!summary) return null;
  const state = summary.state || "";
  if (summary.reconciliation_required || state === "reconciliation_required") {
    return { label: "Needs Reconciliation", tone: "amber" };
  }
  if (state === "failed") return { label: "Failed", tone: "red" };
  if (state === "suppressed") return { label: "Suppressed", tone: "gray" };
  if (["observed", "direction_resolved", "physical_command_pending", "physical_command_accepted"].includes(state)) {
    return { label: "Pending", tone: "blue" };
  }
  if (state === "completed" || state === "presence_committed" || summary.presence_committed) {
    return { label: "Confirmed", tone: "green" };
  }
  return { label: titleCase(state), tone: "gray" };
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
export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short"
  }).format(new Date(value));
}
