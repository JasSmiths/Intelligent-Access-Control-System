import { Key, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import React from "react";

import { api, createActionConfirmation } from "../../api/client";
import { Badge, CardHeader } from "../../ui/primitives";
import { SettingRow } from "./SettingRow";

export type AuthSecretStatus = {
  source: string;
  environment: string;
  file_path: string;
  env_configured: boolean;
  env_default_configured: boolean;
  rotation_required: boolean;
  ui_rotation_available: boolean;
  detail: string;
  rotated?: boolean;
  settings_reencrypted?: number;
  icloud_accounts_reencrypted?: number;
  action_contexts_invalidated?: number;
};

export function AuthSecretSecurityPanel({ refreshToken }: { refreshToken: number }) {
  const [status, setStatus] = React.useState<AuthSecretStatus | null>(null);
  const [customSecret, setCustomSecret] = React.useState("");
  const [confirmed, setConfirmed] = React.useState(false);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState("");
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setStatus(await api.get<AuthSecretStatus>("/api/v1/settings/security/auth-secret"));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load auth secret status.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    load().catch(() => undefined);
  }, [load, refreshToken]);

  const rotate = async () => {
    if (!confirmed || saving) return;
    setSaving(true);
    setSaved("");
    setError("");
    try {
      const newSecret = customSecret.trim() || undefined;
      const confirmation = await createActionConfirmation(
        "auth_secret.rotate",
        { new_secret_provided: Boolean(newSecret) },
        {
          target_entity: "AuthSecret",
          target_label: "Authentication root secret",
          reason: "Rotate auth root secret"
        }
      );
      const payload = {
        confirmation_token: confirmation.confirmation_token,
        new_secret: newSecret
      };
      const next = await api.post<AuthSecretStatus>("/api/v1/settings/security/auth-secret/rotate", payload);
      setStatus(next);
      setCustomSecret("");
      setConfirmed(false);
      setSaved("Auth secret rotated. Existing sessions and pending action links were invalidated.");
    } catch (rotateError) {
      setError(rotateError instanceof Error ? rotateError.message : "Unable to rotate auth secret.");
    } finally {
      setSaving(false);
    }
  };

  const sourceLabel = status?.source === "env"
    ? "Environment override"
    : status?.source === "generated"
      ? "Generated file"
      : "Secret file";

  return (
    <div className="card auth-secret-panel">
      <CardHeader icon={ShieldCheck} title="Auth Secret" action={<Badge tone={loading ? "gray" : error || !status ? "amber" : status.rotation_required ? "amber" : "green"}>{loading ? "Checking" : error || !status ? "Unavailable" : status.rotation_required ? "Rotation needed" : "Ready"}</Badge>} />
      {loading ? (
        <div className="compact-row"><Loader2 size={16} /> Loading security status...</div>
      ) : status ? (
        <div className="settings-list">
          <SettingRow label="Source" value={sourceLabel} />
          <SettingRow label="Environment" value={status.environment} />
          <SettingRow label="UI rotation" value={status.ui_rotation_available ? "Available" : "Env managed"} />
        </div>
      ) : null}
      {status?.ui_rotation_available ? (
        <div className="auth-secret-rotate">
          <label className="field">
            <span>Custom secret</span>
            <div className="field-control">
              <Key size={17} />
              <input
                value={customSecret}
                onChange={(event) => setCustomSecret(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.preventDefault();
                }}
                placeholder="Leave blank to generate a secure secret"
                type="password"
              />
            </div>
            <small className="field-hint">Use at least 32 characters. Blank rotation generates a new random value.</small>
          </label>
          <label className="maintenance-switch auth-secret-confirm">
            <input checked={confirmed} disabled={saving} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />
            <span>Confirm rotation</span>
          </label>
          <button className="primary-button" disabled={!confirmed || saving} onClick={rotate} type="button">
            {saving ? <Loader2 size={15} /> : <RefreshCw size={15} />}
            {saving ? "Rotating..." : "Rotate Secret"}
          </button>
        </div>
      ) : (
        <p className="dependency-storage-note">{status?.detail || "Auth secret rotation is managed outside the UI."}</p>
      )}
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {saved ? <div className="success-note">{saved}</div> : null}
    </div>
  );
}
