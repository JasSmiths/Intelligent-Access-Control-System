import { Key, SlidersHorizontal } from "lucide-react";
import React from "react";
import { api, isAbortError } from "../api/client";
import type { SettingsMap, SystemSetting } from "../api/types";
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
  "esphome_devices",
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
  "anthropic_api_key",
  "lpr_webhook_token"
]);
export const discordListSettingKeys = new Set([
  "discord_guild_allowlist",
  "discord_channel_allowlist",
  "discord_user_allowlist",
  "discord_role_allowlist",
  "discord_admin_role_ids"
]);
const listSettingKeys = new Set([
  ...discordListSettingKeys,
  "lpr_allowed_smart_zones",
  "lpr_webhook_allowed_source_ips"
]);
export function SettingField({
  action,
  field,
  isConfiguredSecret = false,
  revealPasswordValue = false,
  value,
  onChange
}: {
  action?: React.ReactNode;
  field: SettingFieldDefinition;
  isConfiguredSecret?: boolean;
  revealPasswordValue?: boolean;
  value: string;
  onChange: (value: string) => void;
}) {
  const secretPlaceholder = isConfiguredSecret ? "Configured. Paste a new value to replace it." : undefined;
  return (
    <label className="field">
      {action ? (
        <span className="field-label-row">
          <span>{field.label}</span>
          {action}
        </span>
      ) : (
        <span>
          {field.label}
          {field.href ? <a href={field.href} rel="noreferrer" target="_blank">Get key</a> : null}
        </span>
      )}
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
            type={field.type === "password" && !revealPasswordValue ? "password" : field.type === "number" ? "number" : "text"}
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
  const loadSequenceRef = React.useRef(0);
  const loadAbortRef = React.useRef<AbortController | null>(null);
  const values = React.useMemo(() => {
    return settingsRows.reduce<SettingsMap>((acc, row) => {
      acc[row.key] = row.value;
      return acc;
    }, {});
  }, [settingsRows]);
  const load = React.useCallback(async () => {
    const sequence = loadSequenceRef.current + 1;
    loadSequenceRef.current = sequence;
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    setError("");
    setLoading(true);
    try {
      const suffix = category ? `?category=${encodeURIComponent(category)}` : "";
      const rows = await api.get<SystemSetting[]>(`/api/v1/settings${suffix}`, { signal: controller.signal });
      if (loadSequenceRef.current === sequence && !controller.signal.aborted) {
        setSettingsRows(rows);
      }
    } catch (loadError) {
      if (!isAbortError(loadError) && loadSequenceRef.current === sequence) {
        setError(loadError instanceof Error ? loadError.message : "Unable to load settings");
      }
    } finally {
      if (loadSequenceRef.current === sequence) {
        setLoading(false);
        if (loadAbortRef.current === controller) loadAbortRef.current = null;
      }
    }
  }, [category]);
  React.useEffect(() => {
    load().catch(() => undefined);
    return () => loadAbortRef.current?.abort();
  }, [load]);
  const save = React.useCallback(async (updates: Record<string, unknown>, options: { confirmationToken?: string } = {}) => {
    await api.patch<SystemSetting[]>("/api/v1/settings", {
      values: updates,
      ...(options.confirmationToken ? { confirmation_token: options.confirmationToken } : {})
    });
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
      let parsed: unknown;
      try {
        parsed = value.trim() ? JSON.parse(value) : [];
      } catch {
        throw new Error(`${key} must be valid JSON array syntax.`);
      }
      if (!Array.isArray(parsed)) {
        throw new Error(`${key} must be a JSON array.`);
      }
      payload[key] = parsed;
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
      const text = value.trim();
      const parsed = Number(text);
      if (!text || !Number.isFinite(parsed)) {
        throw new Error(`${key} must be a finite number.`);
      }
      payload[key] = parsed;
    } else {
      payload[key] = value;
    }
  }
  return payload;
}
