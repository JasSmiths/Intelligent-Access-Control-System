import { Construction } from "lucide-react";
import React from "react";

import { api, createActionConfirmation } from "../../api/client";
import type { MaintenanceStatus, UserAccount } from "../../api/types";

export function MaintenanceModeSettings({
  currentUser,
  status,
  onStatusChanged
}: {
  currentUser?: UserAccount;
  status: MaintenanceStatus | null;
  onStatusChanged?: (status: MaintenanceStatus) => void;
}) {
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const active = status?.is_active === true;
  const isAdmin = currentUser?.role === "admin";
  const toggle = async () => {
    if (saving) return;
    if (!isAdmin) {
      setError("Admin access is required to update Maintenance Mode.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const path = active ? "/api/v1/maintenance/disable" : "/api/v1/maintenance/enable";
      const action = active ? "maintenance_mode.disable" : "maintenance_mode.enable";
      const payload = {
        reason: active ? "Disabled from Settings General" : "Enabled from Settings General"
      };
      const confirmation = await createActionConfirmation(action, payload, {
        target_entity: "MaintenanceMode",
        target_label: "Maintenance Mode",
        reason: payload.reason
      });
      const next = await api.post<MaintenanceStatus>(path, {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      onStatusChanged?.(next);
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : "Unable to update Maintenance Mode.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className={active ? "maintenance-settings active" : "maintenance-settings"}>
      <div className="maintenance-settings-copy">
        <span className="maintenance-settings-icon">
          <Construction size={20} strokeWidth={1} />
        </span>
        <div>
          <strong>Maintenance Mode</strong>
          <span>{active ? "All automated actions are disabled" : "Automated actions are available"}</span>
          {active && status?.enabled_by ? <small>Enabled by {status.enabled_by}{status.duration_label ? ` for ${status.duration_label}` : ""}</small> : null}
        </div>
      </div>
      <label className={active ? "maintenance-switch active" : "maintenance-switch"}>
        <input checked={active} disabled={saving || !status || !isAdmin} onChange={toggle} type="checkbox" />
        <span>{saving ? "Updating" : active ? "Enabled" : "Disabled"}</span>
      </label>
      {error ? <div className="auth-error inline-error maintenance-settings-error">{error}</div> : null}
    </div>
  );
}
