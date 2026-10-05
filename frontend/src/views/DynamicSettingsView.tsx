import React from "react";

import { createActionConfirmation } from "../api/client";
import { coerceSettingsPayload, SettingField, stringifySetting, useSettings } from "../lib/settings";
import { Badge, CardHeader, Toolbar } from "../ui/primitives";
import type { MaintenanceStatus, UserAccount } from "../api/types";
import type { SettingFieldDefinition } from "../lib/settings";
import { useGateLprSmartZones, GateLprSmartZoneField } from "../features/settings/lprSmartZones";
import { AuthSecretSecurityPanel } from "../features/settings/AuthSecretSecurityPanel";
import { MaintenanceModeSettings } from "../features/settings/MaintenanceModeSettings";

export function DynamicSettingsView({
  category,
  title,
  icon: Icon,
  currentUser,
  maintenanceStatus,
  onMaintenanceStatusChanged,
  refreshToken
}: {
  category: "general" | "auth" | "lpr";
  title: string;
  icon: React.ElementType;
  currentUser?: UserAccount;
  maintenanceStatus?: MaintenanceStatus | null;
  onMaintenanceStatusChanged?: (status: MaintenanceStatus) => void;
  refreshToken: number;
}) {
  const { rows, values, loading, error, save, reload } = useSettings(category);
  const [form, setForm] = React.useState<Record<string, string>>({});
  const [saved, setSaved] = React.useState("");
  const [submitError, setSubmitError] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const savingRef = React.useRef(false);
  const dirtyFields = React.useRef(new Set<string>());
  const fields = settingsFields(category);
  const isAdmin = currentUser?.role === "admin";
  const gateLprSmartZones = useGateLprSmartZones(category === "lpr");
  const lastRefreshTokenRef = React.useRef(refreshToken);
  const configuredSecretKeys = React.useMemo(
    () => new Set(rows.filter((row) => row.is_secret && Boolean(row.value)).map((row) => row.key)),
    [rows]
  );
  const secretKeys = React.useMemo(
    () => new Set(rows.filter((row) => row.is_secret).map((row) => row.key)),
    [rows]
  );

  React.useEffect(() => {
    const next: Record<string, string> = {};
    for (const field of fields) {
      next[field.key] = secretKeys.has(field.key) ? "" : stringifySetting(values[field.key]);
    }
    setForm((current) => ({ ...next, ...Object.fromEntries([...dirtyFields.current].map((key) => [key, current[key] ?? ""])) }));
  }, [values, category, secretKeys]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    reload().catch(() => undefined);
  }, [refreshToken, reload]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (savingRef.current || loading || error || !rows.length) return;
    setSaved("");
    setSubmitError("");
    if (!isAdmin) {
      setSubmitError("Administrator access is required to save settings.");
      return;
    }
    savingRef.current = true;
    setSaving(true);
    try {
      const updates = coerceSettingsPayload(form);
      const confirmationPayload = { values: updates };
      const confirmation = await createActionConfirmation("settings.update", confirmationPayload, {
        target_entity: "SystemSetting",
        target_label: title,
        reason: "Update dynamic settings"
      });
      await save(updates, { confirmationToken: confirmation.confirmation_token });
      dirtyFields.current.clear();
      setSaved("Settings saved.");
    } catch (saveError) {
      setSubmitError(saveError instanceof Error ? saveError.message : "Unable to save settings.");
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return (
    <section className="view-stack settings-page">
      <Toolbar title={title} count={fields.length} icon={Icon} />
      <form className="dashboard-grid settings-grid" onSubmit={submit}>
        <div className="card span-2">
          <CardHeader icon={Icon} title={title} action={<Badge tone={loading ? "gray" : error ? "amber" : rows.length ? "green" : "gray"}>{loading ? "Loading" : error ? "Unavailable" : rows.length ? "Loaded" : "No settings"}</Badge>} />
          {category === "general" ? (
            <MaintenanceModeSettings
              currentUser={currentUser}
              status={maintenanceStatus ?? null}
              onStatusChanged={onMaintenanceStatusChanged}
            />
          ) : null}
          <fieldset className="settings-form-grid settings-form-fieldset" disabled={saving || loading || Boolean(error) || !isAdmin}>
            {fields.map((field) => {
              const onChange = (value: string) => { dirtyFields.current.add(field.key); setForm((current) => ({ ...current, [field.key]: value })); setSaved(""); };
              if (category === "lpr" && field.key === "lpr_allowed_smart_zones") {
                return (
                  <GateLprSmartZoneField
                    field={field}
                    key={field.key}
                    state={gateLprSmartZones}
                    value={form[field.key] ?? ""}
                    onChange={onChange}
                  />
                );
              }
              return (
                <SettingField
                  field={field}
                  isConfiguredSecret={configuredSecretKeys.has(field.key)}
                  key={field.key}
                  value={form[field.key] ?? ""}
                  onChange={onChange}
                />
              );
            })}
          </fieldset>
          {submitError || error ? <div className="auth-error inline-error">{submitError || error}</div> : null}
          {!loading && !error && !rows.length ? <p className="integration-state-note">No settings were returned for this section. Saving is unavailable until configuration loads.</p> : null}
          {saved ? <div className="success-note">{saved}</div> : null}
          <div className="modal-actions">
            <button className="primary-button" disabled={!isAdmin || saving || loading || Boolean(error) || !rows.length} type="submit">{saving ? "Saving…" : "Save Settings"}</button>
          </div>
        </div>
        {category === "auth" && isAdmin ? <AuthSecretSecurityPanel refreshToken={refreshToken} /> : null}
      </form>
    </section>
  );
}

function settingsFields(category: "general" | "auth" | "lpr"): SettingFieldDefinition[] {
  if (category === "general") {
    return [
      { key: "app_name", label: "App name" },
      { key: "site_timezone", label: "Timezone" },
      { key: "log_level", label: "Log level", type: "select", options: ["DEBUG", "INFO", "WARNING", "ERROR"] }
    ];
  }
  if (category === "auth") {
    return [
      { key: "auth_cookie_name", label: "Cookie name" },
      { key: "auth_access_token_minutes", label: "Access token minutes", type: "number", min: 5, step: 5 },
      { key: "auth_remember_days", label: "Remember-me days", type: "number", min: 1, step: 1 },
      { key: "auth_cookie_secure", label: "Secure cookie", type: "select", options: ["true", "false"] }
    ];
  }
  return [
    { key: "lpr_debounce_quiet_seconds", label: "Debounce quiet seconds", type: "number", min: 0.5, step: 0.1 },
    { key: "lpr_debounce_max_seconds", label: "Debounce max seconds", type: "number", min: 1, step: 0.1 },
    { key: "lpr_vehicle_session_idle_seconds", label: "Vehicle session idle seconds", type: "number", min: 10, step: 5 },
    { key: "lpr_similarity_threshold", label: "Similarity threshold", type: "number", min: 0, max: 1, step: 0.01 },
  ];
}
