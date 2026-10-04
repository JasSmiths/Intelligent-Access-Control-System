import React from "react";
import { createActionConfirmation, isAbortError } from "../../api/client";
import { integrationsApi, type RecoveryTrackerDiscovery } from "../../api/integrations";
import { missedExitRecoveryApi } from "../../api/missedExitRecovery";
import type { Person } from "../../api/types";
import { stringifySetting, useSettings } from "../../lib/settings";
import { recoveryTrackerMessage } from "./model";

type OwnerConfig = { enabled: boolean; tracker: string };
type OwnerDraft = { config: OwnerConfig; dirty: boolean; trackerTouched: boolean; saved: OwnerConfig | null; autoTarget: string | null };
const ownerValues = (person?: Person): OwnerConfig => ({ enabled: person?.missed_exit_recovery_enabled ?? false, tracker: person?.missed_exit_recovery_tracker_entity_id ?? "" });
const sameOwnerValues = (a: OwnerConfig, b: OwnerConfig) => a.enabled === b.enabled && a.tracker === b.tracker;
const newOwnerDraft = (person?: Person): OwnerDraft => ({ config: ownerValues(person), dirty: false, trackerTouched: false, saved: null, autoTarget: null });

export function RecoveryConfiguration({ people, refreshToken, refresh }: { people: Person[]; refreshToken: number; refresh: () => Promise<void> }) {
  const settings = useSettings("missed_exit_recovery", refreshToken);
  const [config, setConfig] = React.useState({ enabled: false, latitude: "", longitude: "" });
  const [globalDirty, setGlobalDirty] = React.useState(false);
  const [settingsKnown, setSettingsKnown] = React.useState(false);
  const [ownerId, setOwnerId] = React.useState("");
  const owner = people.find((person) => person.id === ownerId);
  const [ownerDraft, setOwnerDraft] = React.useState<OwnerDraft>(() => newOwnerDraft());
  const ownerConfig = ownerDraft.config;
  const ownerDrafts = React.useRef(new Map<string, OwnerDraft>());
  const [discovery, setDiscovery] = React.useState<RecoveryTrackerDiscovery | null>(null);
  const [discoveryLoading, setDiscoveryLoading] = React.useState(false);
  const [discoveryError, setDiscoveryError] = React.useState("");
  const discoveryAbort = React.useRef<AbortController | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [saved, setSaved] = React.useState("");
  React.useEffect(() => {
    if (settings.loading || settings.error) return;
    setSettingsKnown(true);
    if (globalDirty || saving) return;
    setConfig({ enabled: settings.values.missed_exit_recovery_enabled === true,
      latitude: stringifySetting(settings.values.missed_exit_recovery_gate_latitude ?? ""),
      longitude: stringifySetting(settings.values.missed_exit_recovery_gate_longitude ?? "") });
  }, [settings.values, settings.loading, settings.error, globalDirty, saving]);
  React.useEffect(() => {
    if (!owner || ownerDraft.dirty || saving) return;
    const current = ownerValues(owner);
    // A directory response started before the save must not undo its result.
    if (ownerDraft.saved && !sameOwnerValues(ownerDraft.saved, current)) return;
    if (!sameOwnerValues(ownerDraft.config, current) || ownerDraft.saved) setOwnerDraft({ ...ownerDraft, config: current, saved: null });
  }, [owner, ownerDraft, saving]);
  React.useEffect(() => { if (ownerId) ownerDrafts.current.set(ownerId, ownerDraft); }, [ownerId, ownerDraft]);
  const loadDiscovery = React.useCallback(async () => {
    discoveryAbort.current?.abort();
    const controller = new AbortController(); discoveryAbort.current = controller;
    setDiscoveryLoading(true); setDiscoveryError(""); setDiscovery(null);
    try {
      const result = await integrationsApi.getRecoveryTrackers({ signal: controller.signal });
      if (!controller.signal.aborted) setDiscovery(result);
    } catch (cause) {
      if (!controller.signal.aborted && !isAbortError(cause)) setDiscoveryError("Unable to discover phone trackers. You can enter the entity manually.");
    } finally { if (!controller.signal.aborted) setDiscoveryLoading(false); }
  }, []);
  React.useEffect(() => { void loadDiscovery(); return () => discoveryAbort.current?.abort(); }, [loadDiscovery]);
  const mapping = discovery?.mappings?.find((item) => item.person_id === ownerId && item.notify_service_id === owner?.home_assistant_mobile_app_notify_service);
  const phoneOptions = (discovery?.trackers ?? []).filter((item) => item.eligible);
  const selectedTracker = discovery?.trackers?.find((item) => item.entity_id === ownerConfig.tracker);
  const discoveryHint = discoveryError || (ownerDraft.autoTarget ? "A phone tracker was matched to this owner's saved notification destination. Save owner configuration to apply it." : "") || recoveryTrackerMessage(mapping?.reason) || recoveryTrackerMessage(selectedTracker?.reason) || recoveryTrackerMessage(discovery?.reason) || "Choose a discovered tracker or enter one manually. Automatic selection requires an exact association with the owner's saved notification destination.";
  React.useEffect(() => {
    if (!owner || saving || ownerDraft.trackerTouched) return;
    const target = owner.home_assistant_mobile_app_notify_service ?? null;
    if (ownerDraft.autoTarget && ownerDraft.autoTarget !== target) {
      setOwnerDraft({ ...ownerDraft, config: { ...ownerConfig, tracker: "" }, autoTarget: null });
      return;
    }
    if (ownerConfig.tracker || owner.missed_exit_recovery_tracker_entity_id || mapping?.status !== "matched" || !target || !mapping.suggested_tracker_entity_id) return;
    const suggested = discovery?.trackers?.find((item) => item.entity_id === mapping.suggested_tracker_entity_id);
    if (suggested?.eligible && suggested.available) setOwnerDraft({ ...ownerDraft, config: { ...ownerConfig, tracker: suggested.entity_id }, dirty: true, autoTarget: target });
  }, [owner, ownerDraft, ownerConfig, mapping, discovery, saving]);
  function selectOwner(id: string) {
    if (ownerId) ownerDrafts.current.set(ownerId, ownerDraft);
    setOwnerDraft(ownerDrafts.current.get(id) ?? newOwnerDraft(people.find((person) => person.id === id)));
    setOwnerId(id); setError(""); setSaved("");
  }
  function editOwner(updates: Partial<OwnerConfig>, trackerTouched = false) {
    setOwnerDraft({ ...ownerDraft, config: { ...ownerConfig, ...updates }, dirty: true, trackerTouched: ownerDraft.trackerTouched || trackerTouched, autoTarget: trackerTouched ? null : ownerDraft.autoTarget });
  }
  async function saveGlobal(event: React.FormEvent) {
    event.preventDefault(); if (saving) return;
    setSaving(true); setError(""); setSaved("");
    try {
      const values = { missed_exit_recovery_enabled: config.enabled,
        missed_exit_recovery_gate_latitude: config.latitude.trim() ? Number(config.latitude) : null,
        missed_exit_recovery_gate_longitude: config.longitude.trim() ? Number(config.longitude) : null };
      if (config.enabled && (values.missed_exit_recovery_gate_latitude === null || values.missed_exit_recovery_gate_longitude === null)) throw new Error("Gate latitude and longitude are required before enabling recovery.");
      const confirmation = await createActionConfirmation("settings.update", { values }, {
        target_entity: "SystemSetting", target_label: "Missed Exit Recovery", reason: "Update missed exit recovery settings"
      });
      await settings.save(values, { confirmationToken: confirmation.confirmation_token });
      setGlobalDirty(false);
      setSaved("Global recovery settings saved.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to save recovery settings"); }
    finally { setSaving(false); }
  }
  async function saveOwner(event: React.FormEvent) {
    event.preventDefault(); if (!owner || saving) return;
    setSaving(true); setError(""); setSaved("");
    try {
      const result = await missedExitRecoveryApi.saveOwner(owner, { missed_exit_recovery_enabled: ownerConfig.enabled, missed_exit_recovery_tracker_entity_id: ownerConfig.tracker.trim() || null });
      const values = ownerValues(result);
      setOwnerDraft({ ...ownerDraft, config: values, dirty: false, saved: values, autoTarget: null });
      setSaved("Owner recovery settings saved.");
      try { await refresh(); } catch { setError("Owner settings were saved, but the directory could not be refreshed."); }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to save owner settings"); }
    finally { setSaving(false); }
  }
  return <>
    <section className="card"><h2>Global configuration</h2><form onSubmit={saveGlobal}>
      <fieldset disabled={saving || !settingsKnown} className="recovery-fields">
        <label className="field"><span>Global recovery</span><select value={String(config.enabled)} onChange={(event) => { setGlobalDirty(true); setConfig({ ...config, enabled: event.target.value === "true" }); }}><option value="false">Disabled</option><option value="true">Enabled</option></select></label>
        <label className="field"><span>Gate latitude</span><input type="number" min={-90} max={90} step="any" value={config.latitude} onChange={(event) => { setGlobalDirty(true); setConfig({ ...config, latitude: event.target.value }); }} /></label>
        <label className="field"><span>Gate longitude</span><input type="number" min={-180} max={180} step="any" value={config.longitude} onChange={(event) => { setGlobalDirty(true); setConfig({ ...config, longitude: event.target.value }); }} /></label>
        <button className="primary-button" type="submit">Save global configuration</button>
      </fieldset></form>{settings.error ? <div className="auth-error" role="alert">{settings.error}</div> : null}
    </section>
    <section className="card"><h2>Owner configuration</h2><form onSubmit={saveOwner}>
      <fieldset disabled={saving} className="recovery-fields">
        <label className="field"><span>Configure owner</span><select value={ownerId} onChange={(event) => selectOwner(event.target.value)}><option value="">Select owner</option>{people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}</select></label>
        <label className="field"><span>Owner recovery opt-in</span><select disabled={!owner} value={String(ownerConfig.enabled)} onChange={(event) => editOwner({ enabled: event.target.value === "true" })}><option value="false">Disabled</option><option value="true">Enabled</option></select></label>
        <label className="field"><span>Discovered phone tracker</span><select disabled={!owner} value={ownerConfig.tracker} onChange={(event) => editOwner({ tracker: event.target.value }, true)}><option value="">Select tracker or enter manually</option>{ownerConfig.tracker && !phoneOptions.some((item) => item.entity_id === ownerConfig.tracker) ? <option value={ownerConfig.tracker}>{ownerConfig.tracker} (configured/manual)</option> : null}{phoneOptions.map((item) => <option key={item.entity_id} value={item.entity_id}>{item.name || item.entity_id} · {item.entity_id}{!item.available ? " (Unavailable)" : ""}</option>)}</select></label>
        <label className="field"><span>Home Assistant phone tracker entity</span><input disabled={!owner} value={ownerConfig.tracker} placeholder="device_tracker.phone" pattern="device_tracker\.[a-z0-9_]+" onChange={(event) => editOwner({ tracker: event.target.value }, true)} /></label>
        <button className="secondary-button" type="button" disabled={discoveryLoading} onClick={() => void loadDiscovery()}>Refresh phone trackers</button>
        <button className="primary-button" disabled={!owner} type="submit">Save owner configuration</button>
      </fieldset></form><p className="field-hint">{discoveryHint}</p><p className="field-hint">Notification service is configured on the person. Saving configuration does not send notifications or commands.</p>
    </section>
    {error ? <div className="auth-error" role="alert">{error}</div> : null}{saved ? <div className="success-note" role="status">{saved}</div> : null}
  </>;
}
