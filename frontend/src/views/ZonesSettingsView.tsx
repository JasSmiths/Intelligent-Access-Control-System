import { Loader2, RefreshCw } from "lucide-react";
import React from "react";

import { api, createActionConfirmation } from "../api/client";
import { formatDate } from "../lib/format";
import { stringifySetting, useSettings } from "../lib/settings";
import { Badge, Toolbar } from "../ui/primitives";
import type { UserAccount } from "../api/types";

export type LprZoneShadowObservation = {
  id: string;
  access_event_id: string | null;
  registration_number: string;
  detected_registration_number: string | null;
  source: string;
  protect_event_id: string | null;
  camera_id: string | null;
  camera_name: string | null;
  camera_identifier: string | null;
  observed_at: string;
  time_of_day: "day" | "night" | "unknown" | string;
  time_of_day_source: string;
  zone_id: string | null;
  zone_name: string | null;
  zone_status: string | null;
  zone_level: number | null;
  actual_decision: string | null;
  actual_direction: string | null;
  actual_outcome: string | null;
  shadow_decision: string;
  shadow_reason: string;
  would_suppress: boolean;
  details: Record<string, unknown>;
  created_at: string | null;
};

export function ZonesSettingsView({
  icon: Icon,
  refreshToken,
  currentUser
}: {
  icon: React.ElementType;
  refreshToken: number;
  currentUser: UserAccount;
}) {
  const [observations, setObservations] = React.useState<LprZoneShadowObservation[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [modeMessage, setModeMessage] = React.useState("");
  const [modeError, setModeError] = React.useState("");
  const [savingMode, setSavingMode] = React.useState(false);
  const lprSettings = useSettings("lpr");
  const isAdmin = currentUser?.role === "admin";
  const zoneFilterMode = normalizeZoneFilterMode(stringifySetting(lprSettings.values.lpr_zone_filter_mode || "shadow"));
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const loadObservations = React.useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError("");
    try {
      const payload = await api.get<{ observations: LprZoneShadowObservation[] }>("/api/v1/diagnostics/lpr-zone-shadow?limit=300");
      setObservations(payload.observations ?? []);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load zone shadow observations.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    loadObservations().catch(() => undefined);
    const interval = window.setInterval(() => {
      loadObservations(false).catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(interval);
  }, [loadObservations]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    loadObservations(false).catch(() => undefined);
  }, [loadObservations, refreshToken]);

  const saveMode = async (mode: "shadow" | "live") => {
    if (!isAdmin || mode === zoneFilterMode || savingMode) return;
    setModeMessage("");
    setModeError("");
    setSavingMode(true);
    try {
      const values = { lpr_zone_filter_mode: mode };
      const confirmation = await createActionConfirmation("settings.update", { values }, {
        target_entity: "SystemSetting",
        target_id: "lpr_zone_filter_mode",
        target_label: "LPR zone filter mode",
        reason: "Update LPR zone filter mode"
      });
      await lprSettings.save(values, { confirmationToken: confirmation.confirmation_token });
      setModeMessage(`Zone filter mode saved as ${mode}.`);
    } catch (saveError) {
      setModeError(saveError instanceof Error ? saveError.message : "Unable to save zone filter mode.");
    } finally {
      setSavingMode(false);
    }
  };

  return (
    <section className="view-stack settings-page">
      <Toolbar
        title="Zones"
        badge={<Badge tone={zoneFilterMode === "live" ? "red" : "blue"}>{zoneFilterMode}</Badge>}
        icon={Icon}
      >
        <div className="zone-toolbar-actions">
          <div className="zone-mode-toggle" role="group" aria-label="LPR zone filter mode">
            {(["shadow", "live"] as const).map((mode) => (
              <button
                className={mode === zoneFilterMode ? "active" : ""}
                disabled={!isAdmin || savingMode || lprSettings.loading}
                key={mode}
                onClick={() => saveMode(mode)}
                title={!isAdmin ? "Administrator access is required to change zone filter mode" : undefined}
                type="button"
              >
                {zoneTitle(mode)}
              </button>
            ))}
          </div>
          <Badge tone="gray">{observations.length}</Badge>
          <button className="icon-button" type="button" onClick={() => loadObservations()} disabled={loading} title="Refresh zone shadow observations">
            {loading ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
          </button>
        </div>
      </Toolbar>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {modeError || lprSettings.error ? <div className="auth-error inline-error">{modeError || lprSettings.error}</div> : null}
      {modeMessage ? <div className="success-note">{modeMessage}</div> : null}
      <p className="zone-filter-explainer">Zone filter results describe only the camera zone check. Final access decisions are shown separately with the event time; passing this filter does not grant entry.</p>
      <div className="table-card zone-shadow-table-card">
        <table className="zone-shadow-table">
          <thead>
            <tr>
              <th>Plate Detected</th>
              <th>Day/Night</th>
              <th>Zone</th>
              <th>Status</th>
              <th>Level</th>
              <th>Zone filter result</th>
              <th>Observed / access decision</th>
            </tr>
          </thead>
          <tbody>
            {loading && !observations.length ? (
              <tr>
                <td colSpan={7}><span className="table-muted-line">Loading zone shadow observations</span></td>
              </tr>
            ) : observations.length ? observations.map((item) => (
              <tr key={item.id}>
                <td>
                  <strong>{item.registration_number}</strong>
                  {item.detected_registration_number && item.detected_registration_number !== item.registration_number ? (
                    <span className="table-muted-line">Detected {item.detected_registration_number}</span>
                  ) : <span className="table-muted-line">{item.source}</span>}
                </td>
                <td>
                  <Badge tone={timeOfDayTone(item.time_of_day)}>{zoneTitle(item.time_of_day)}</Badge>
                  <span className="table-muted-line">{item.time_of_day_source}</span>
                </td>
                <td>
                  <strong>{item.zone_name || item.zone_id || "Unknown"}</strong>
                  {item.zone_id && item.zone_id !== item.zone_name ? <span className="table-muted-line">Zone {item.zone_id}</span> : null}
                </td>
                <td><Badge tone={zoneStatusTone(item.zone_status)}>{zoneTitle(item.zone_status)}</Badge></td>
                <td>{typeof item.zone_level === "number" ? Math.round(item.zone_level) : <span className="table-muted-line">--</span>}</td>
                <td className="zone-shadow-decision-cell">
                  <Badge tone={zoneDecisionTone(item)}>{zoneDecisionLabel(item.shadow_decision)}</Badge>
                  <span className="table-muted-line">{item.shadow_reason}</span>
                </td>
                <td>
                  {formatDate(item.observed_at)}
                  {item.actual_decision ? <span className="table-muted-line">{zoneTitle(item.actual_decision)} {zoneTitle(item.actual_direction)}</span> : null}
                </td>
              </tr>
            )) : (
              <tr>
                <td colSpan={7}><span className="table-muted-line">{error ? "Zone observations unavailable" : "No zone shadow observations yet"}</span></td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function zoneTitle(value: string | null | undefined) {
  return String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function timeOfDayTone(value: string): "green" | "gray" | "amber" | "red" | "blue" | "purple" {
  if (value === "night") return "purple";
  if (value === "day") return "amber";
  return "gray";
}

function zoneStatusTone(value: string | null): "green" | "gray" | "amber" | "red" | "blue" | "purple" {
  if (value === "enter") return "green";
  if (value === "moving") return "amber";
  if (value) return "blue";
  return "gray";
}

function zoneDecisionTone(item: LprZoneShadowObservation): "green" | "gray" | "amber" | "red" | "blue" | "purple" {
  if (item.shadow_decision === "shadow_only") return "blue";
  if (item.shadow_decision === "skipped_missing_zone_status") return "gray";
  if (item.shadow_decision === "suppressed") return "red";
  if (item.shadow_decision === "allowed") return "green";
  if (item.would_suppress || item.shadow_decision === "would_suppress") return "blue";
  if (item.shadow_decision === "would_review") return "amber";
  if (item.shadow_decision === "would_allow") return "green";
  return "gray";
}

function zoneDecisionLabel(value: string) {
  if (value === "allowed") return "Zone filter passed";
  if (value === "suppressed") return "Zone filter suppressed";
  if (value === "skipped_missing_zone_status") return "Skipped: missing zone/status";
  if (value === "shadow_only") return "Shadow only";
  if (value === "would_allow") return "Would pass zone filter";
  if (value === "would_suppress") return "Shadow only";
  if (value === "would_review") return "Shadow only";
  return zoneTitle(value);
}

function normalizeZoneFilterMode(value: string): "shadow" | "live" {
  return value.trim().toLowerCase() === "live" ? "live" : "shadow";
}
