import { useCoordinatedRead } from "../lib/useCoordinatedRead";
import { auditResource } from "../lib/realtime";
import type { RealtimeMessage } from "../api/types";
const devicesEventMatches = (event: RealtimeMessage) => ["access_device", "esphome", "home_assistant", "settings", "schedule"].includes(auditResource(event));
import { useSchedules } from "../features/schedules/reads";
import { CircleDot, Plus, SlidersHorizontal } from "lucide-react";
import React from "react";

import { api, createActionConfirmation } from "../api/client";
import { integrationsApi } from "../api/integrations";
import { stringifySetting, useSettings } from "../lib/settings";
import { Badge } from "../ui/primitives";
import type { AccessDevice, UserAccount } from "../api/types";
import { deviceHasProviderBinding, deviceHasAnyProviderBinding } from "../features/settings/accessDeviceModel";
import { accessDeviceProviders, accessDeviceProviderLabel } from "../features/settings/accessDeviceProviders";
import { useAccessDeviceBindings } from "../features/settings/useAccessDeviceBindings";
import { ReadSignalsContext } from "../lib/readSignals";
import type { AccessDeviceKind } from "../features/settings/accessDeviceModel";
import { AccessDeviceEditor } from "../features/settings/AccessDeviceEditor";
import { useConfirm } from "../ui/useConfirm";

export function AccessDevicesSettingsView({
  kind,
  title,
  icon: Icon,
  currentUser,
}: {
  kind: AccessDeviceKind;
  title: string;
  icon: React.ElementType;
  currentUser: UserAccount;
}) {
  const scheduleCatalog = useSchedules(); const schedules = scheduleCatalog.items;
  const [devices, setDevices] = React.useState<AccessDevice[]>([]);
  const [admissionChoices, setAdmissionChoices] = React.useState<Array<{ key: string; name: string }>>([]);
  const [devicesLoading, setDevicesLoading] = React.useState(true);
  const [savingKey, setSavingKey] = React.useState("");
  const [removingKey, setRemovingKey] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const [providerSavingKey, setProviderSavingKey] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [error, setError] = React.useState("");
  const signals = React.useContext(ReadSignalsContext);
  const identity = React.useMemo(() => ({ kind, signals, user: currentUser.id, role: currentUser.role }), [kind, signals, currentUser.id, currentUser.role]);
  const bindings = useAccessDeviceBindings(kind, devicesEventMatches, identity);
  const lifetime = bindings.lifetime;
  const accessSettings = useSettings("access", devicesEventMatches);
  const dirtyDeviceIds = React.useRef(new Set<string>());
  const savingKeyRef = React.useRef("");
  const creatingRef = React.useRef(false);
  const providerPendingRef = React.useRef(false);
  const isAdmin = currentUser.role === "admin";

  const readDevices = React.useCallback(async (signal: AbortSignal) => {
    setDevicesLoading(true);
    setError("");
    try {
      const saved = await integrationsApi.getAccessDevices(kind, { signal });
      if (signal.aborted) return;
      setDevices((current) => saved.map((device) => dirtyDeviceIds.current.has(device.id) ? current.find((item) => item.id === device.id) ?? device : device));
      // Choices are server-validated saved devices, independent of unsaved editor drafts.
      setAdmissionChoices(saved.filter((device) => device.admission_eligible === true).map(({ key, name }) => ({ key, name })));
    } catch (loadError) {
      if (!signal.aborted) setError(loadError instanceof Error ? loadError.message : "Unable to load access devices.");
      throw loadError;
    } finally {
      if (!signal.aborted) setDevicesLoading(false);
    }
  }, [kind]);

  const loadDevices = useCoordinatedRead(readDevices, devicesEventMatches);
  React.useLayoutEffect(() => {
    savingKeyRef.current = ""; creatingRef.current = false; providerPendingRef.current = false; dirtyDeviceIds.current.clear();
    setSavingKey(""); setRemovingKey(""); setCreating(false); setProviderSavingKey(""); setDevices([]); setMessage(""); setError("");
  }, [lifetime]);

  const enabledCount = React.useMemo(() => devices.filter((device) => device.enabled).length, [devices]);
  const mappedCount = React.useMemo(() => devices.filter(deviceHasAnyProviderBinding).length, [devices]);
  const accessOpenCount = React.useMemo(() => devices.filter((device) => device.open_for_access).length, [devices]);
  const scheduleNameById = React.useMemo(() => new Map(schedules.map((schedule) => [schedule.id, schedule.name])), [schedules]);
  const primaryProvider = stringifySetting(accessSettings.values.gate_control_provider || "home_assistant");
  const failoverProvider = stringifySetting(accessSettings.values.gate_failover_provider || "none");
  const admissionDeviceKey = stringifySetting(accessSettings.values.gate_admission_device_key || "");
  const deviceNoun = kind === "gate" ? "gate" : "garage door";
  const deviceNounPlural = kind === "gate" ? "gates" : "garage doors";

  const updateDevice = (deviceId: string, patch: Partial<AccessDevice>) => {
    if (savingKeyRef.current === deviceId) return;
    dirtyDeviceIds.current.add(deviceId);
    setDevices((current) => current.map((device) => device.id === deviceId ? { ...device, ...patch } : device));
  };

  const updateBinding = (deviceId: string, providerKey: string, selection: string) => {
    if (savingKeyRef.current === deviceId) return;
    dirtyDeviceIds.current.add(deviceId);
    setDevices((current) => current.map((device) => device.id === deviceId ? bindings.update(device, providerKey, selection) : device));
  };

  const addDevice = async () => {
    if (creatingRef.current || !lifetime.isCurrent()) return;
    if (!isAdmin) {
      setError("Administrator access is required to add access devices.");
      return;
    }
    creatingRef.current = true; setCreating(true);
    setError(""); setMessage("");
    const suffix = devices.length + 1;
    const baseKey = kind === "gate" ? `gate_${suffix}` : `garage_door_${suffix}`;
    const payload = {
      key: baseKey,
      kind,
      name: kind === "gate" ? `Gate ${suffix}` : `Garage Door ${suffix}`,
      enabled: true,
      schedule_id: null,
      open_for_access: kind === "gate",
      sort_order: devices.length
    };
    try {
      const confirmation = await createActionConfirmation("access_device.create", payload, {
        target_entity: "AccessDevice",
        target_id: baseKey,
        target_label: payload.name,
        reason: "Create access device"
      });
      if (!lifetime.isCurrent()) return;
      const created = await api.post<AccessDevice>("/api/v1/access-devices", {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      if (!lifetime.isCurrent()) return;
      setDevices((current) => [...current, created]);
      setMessage("Device added.");
      try { await loadDevices(); } catch { if (lifetime.isCurrent()) setError("Device added, but device data could not be refreshed. Refresh to retry the read."); }
    } catch (addError) {
      if (!lifetime.isCurrent()) return;
      setError(addError instanceof Error ? addError.message : "Unable to add access device.");
    } finally { if (lifetime.isCurrent()) { creatingRef.current = false; setCreating(false); } }
  };

  const saveDevice = async (device: AccessDevice) => {
    if (savingKeyRef.current || !lifetime.isCurrent()) return;
    if (!isAdmin) {
      setError("Administrator access is required to save access devices.");
      return;
    }
    savingKeyRef.current = device.id;
    setSavingKey(device.id);
    setMessage("");
    setError("");
    try {
      const receipt = await bindings.save(device, (accepted) => {
        setDevices((current) => current.map((item) => item.id === accepted.id ? { ...accepted, bindings: item.bindings } : item));
      });
      if (!lifetime.isCurrent()) return;
      if (receipt.accepted.length) setMessage(`${receipt.accepted.join(", ")} saved.`);
      if (receipt.failedPhase) {
        setError(`${receipt.failedPhase} could not be saved: ${receipt.error}`);
        return;
      }
      if (receipt.stopped || !receipt.saved) return;
      const saved = receipt.saved;
      setDevices((current) => current.map((item) => item.id === saved.id ? saved : item));
      dirtyDeviceIds.current.delete(saved.id);
      setMessage("Device saved.");
      try { await loadDevices(); } catch { if (lifetime.isCurrent()) setError(`Device saved, but device data could not be refreshed. Refresh to retry the read. Accepted: ${receipt.accepted.join("; ")}.`); }
    } finally {
      if (lifetime.isCurrent()) { savingKeyRef.current = ""; setSavingKey(""); }
    }
  };

  const saveProviderSetting = async (key: "gate_control_provider" | "gate_failover_provider" | "gate_admission_device_key", value: string) => {
    if (providerPendingRef.current || !lifetime.isCurrent()) return;
    const isAdmissionSelection = key === "gate_admission_device_key";
    if (!isAdmin) {
      setError("Administrator access is required to save provider preferences.");
      return;
    }
    providerPendingRef.current = true;
    setProviderSavingKey(key);
    setError("");
    setMessage("");
    try {
      const updates = { [key]: value };
      const confirmation = await createActionConfirmation("settings.update", { values: updates }, {
        target_entity: "SystemSetting",
        target_id: key,
        target_label: isAdmissionSelection ? admissionChoices.find((device) => device.key === value)?.name || "No entry gate selected" : accessDeviceProviderLabel(value),
        reason: isAdmissionSelection ? "Designate the automatic-admission entry gate" : "Update access-device provider preference"
      });
      if (!lifetime.isCurrent()) return;
      await accessSettings.save(updates, { confirmationToken: confirmation.confirmation_token });
      if (!lifetime.isCurrent()) return;
      setMessage(isAdmissionSelection ? "Entry gate selection saved." : "Provider preference saved.");
    } catch (providerError) {
      if (!lifetime.isCurrent()) return;
      setError(providerError instanceof Error ? providerError.message : isAdmissionSelection ? "Unable to save entry gate selection." : "Unable to save provider preference.");
    } finally {
      if (lifetime.isCurrent()) { providerPendingRef.current = false; setProviderSavingKey(""); }
    }
  };

  const askConfirm = useConfirm();
  const removePromptRef = React.useRef(false);
  const deleteDevice = async (device: AccessDevice) => {
    if (savingKeyRef.current || !lifetime.isCurrent()) return;
    if (!isAdmin) {
      setError("Administrator access is required to remove access devices.");
      return;
    }
    if (removePromptRef.current) return;
    removePromptRef.current = true;
    let confirmed = false;
    try { confirmed = await askConfirm({ title: `Remove ${device.name}?`, message: "This access device will be removed.", confirmLabel: "Remove", tone: "danger" }); }
    finally { removePromptRef.current = false; }
    // The prompt is asynchronous: re-check the single-operation fence and account lifetime.
    if (!confirmed || savingKeyRef.current || !lifetime.isCurrent()) return;
    savingKeyRef.current = device.id; setSavingKey(device.id); setRemovingKey(device.id);
    setError(""); setMessage("");
    try {
      const payload = { device_id: device.id };
      const confirmation = await createActionConfirmation("access_device.delete", payload, {
        target_entity: "AccessDevice",
        target_id: device.id,
        target_label: device.name,
        reason: "Remove access device"
      });
      if (!lifetime.isCurrent()) return;
      await api.delete(`/api/v1/access-devices/${encodeURIComponent(device.id)}`, {
        confirmation_token: confirmation.confirmation_token
      });
      if (!lifetime.isCurrent()) return;
      setDevices((current) => current.filter((item) => item.id !== device.id));
      dirtyDeviceIds.current.delete(device.id);
      setAdmissionChoices((current) => current.filter((item) => item.key !== device.key));
      setMessage("Device removed.");
      try { await loadDevices(); } catch { if (lifetime.isCurrent()) setError("Device removed, but device data could not be refreshed. Refresh to retry the read."); }
    } catch (deleteError) {
      if (!lifetime.isCurrent()) return;
      setError(deleteError instanceof Error ? deleteError.message : "Unable to remove access device.");
    } finally { if (lifetime.isCurrent()) { savingKeyRef.current = ""; setSavingKey(""); setRemovingKey(""); } }
  };

  return (
    <section className="view-stack settings-page access-device-settings-page">
      <div className="access-device-hero">
        <div className="access-device-hero-main">
          <span className="access-device-hero-icon"><Icon size={22} /></span>
          <div>
            <h1>{title}</h1>
            <p>{kind === "gate" ? "Manage physical gate controllers, routing, and access behavior." : "Manage garage door command routing, schedules, and cover mappings."}</p>
          </div>
        </div>
        <div className="access-device-hero-metrics" aria-label={`${title} summary`}>
          <AccessDeviceSummaryStat label="Configured" value={String(devices.length)} />
          <AccessDeviceSummaryStat label="Enabled" value={String(enabledCount)} tone={enabledCount === devices.length && devices.length ? "green" : "gray"} />
          {kind === "gate" ? (
            <>
              <AccessDeviceSummaryStat label="Access opens" value={String(accessOpenCount)} tone={accessOpenCount ? "green" : "gray"} />
              <AccessDeviceSummaryStat label="Mapped" value={`${mappedCount}/${devices.length || 0}`} />
            </>
          ) : (
            <>
              {accessDeviceProviders.map((provider) => <AccessDeviceSummaryStat key={provider.key} label={provider.metricLabel} value={`${devices.filter((device) => deviceHasProviderBinding(device, provider.key)).length}/${devices.length || 0}`} />)}
            </>
          )}
        </div>
      </div>

      <div className="access-device-settings-grid">
        <section className="access-settings-panel access-provider-panel">
          <div className="access-section-head">
            <div className="access-section-title">
              <span className="access-section-icon"><SlidersHorizontal size={17} /></span>
              <div>
                <h2>Command route</h2>
                <p>{accessDeviceProviderLabel(primaryProvider)} sends {deviceNoun} commands first{failoverProvider === "none" ? "." : `, then ${accessDeviceProviderLabel(failoverProvider)} only before a command may have been sent.`}</p>
              </div>
            </div>
            <Badge tone={accessSettings.loading || providerSavingKey ? "gray" : "blue"}>{providerSavingKey ? "Saving" : "Global"}</Badge>
          </div>
          <div className="access-provider-route">
            <AccessProviderChoice
              disabled={!isAdmin || accessSettings.loading || Boolean(providerSavingKey)}
              helper="Used for every normal open or close command."
              label="Primary"
              value={primaryProvider}
              onChange={(value) => saveProviderSetting("gate_control_provider", value)}
            />
            <div className="access-provider-route-join" aria-hidden="true">then</div>
            <AccessProviderChoice
              allowNone
              disabled={!isAdmin || accessSettings.loading || Boolean(providerSavingKey)}
              helper="Only used when the primary cannot send. Uncertain commands require reconciliation."
              label="Failover"
              value={failoverProvider}
              onChange={(value) => saveProviderSetting("gate_failover_provider", value)}
            />
          </div>
          {kind === "gate" ? (
            <div className="field">
              <label htmlFor="admission-gate">Automatic-admission entry gate</label>
              <select
                id="admission-gate"
                value={admissionDeviceKey}
                disabled={!isAdmin || devicesLoading || accessSettings.loading || Boolean(providerSavingKey)}
                onChange={(event) => saveProviderSetting("gate_admission_device_key", event.target.value)}
              >
                <option value="">No entry gate selected</option>
                {admissionDeviceKey && !admissionChoices.some((device) => device.key === admissionDeviceKey) ? (
                  <option value={admissionDeviceKey}>{admissionDeviceKey} (not eligible or unverified)</option>
                ) : null}
                {admissionChoices.map((device) => (
                  <option key={device.key} value={device.key}>{device.name}</option>
                ))}
              </select>
              {admissionDeviceKey && !admissionChoices.some((device) => device.key === admissionDeviceKey) ? <p role="alert">The selected entry gate is not confirmed eligible. Automatic admission is blocked; select an eligible saved gate or correct its configuration.</p> : null}
              <p>The server checks that the saved gate is enabled, commandable, and set to open for access events. Its physical opening verifies entry admission; it does not prove vehicle passage. Automatic admission stays blocked until an eligible gate is selected.</p>
            </div>
          ) : null}
        </section>

        <section className="access-settings-panel access-device-source-panel">
          <div className="access-section-head access-device-source-head">
            <div className="access-section-title">
              <span className="access-section-icon"><Icon size={17} /></span>
              <div>
                <h2>{title}</h2>
                <p>One saved device per physical {deviceNoun}. Bind either integration, or both for resilience.</p>
              </div>
            </div>
            {isAdmin ? (
              <button className="secondary-button" onClick={addDevice} disabled={creating || devicesLoading || !scheduleCatalog.ready} type="button"><Plus size={15} /> {creating ? "Adding..." : `Add ${kind === "gate" ? "Gate" : "Door"}`}</button>
            ) : null}
          </div>
          {(devicesLoading || bindings.loading) ? <AccessDeviceLoadingBar label={devicesLoading ? "Loading access devices" : "Refreshing provider discovery"} /> : null}
          {scheduleCatalog.error ? <p role="alert">{scheduleCatalog.error}</p> : null}
      {error ? <div className="auth-error inline-error">{error}</div> : null}
          {accessSettings.error ? <div className="auth-error inline-error">{accessSettings.error}</div> : null}
          {message ? <div className="success-note">{message}</div> : null}
          <div className="access-device-list">
            {devices.length ? devices.map((device) => (
              <AccessDeviceEditor
                device={device}
                deviceIcon={Icon}
                discovery={bindings.state}
                key={device.id}
                schedules={schedules}
                scheduleLabel={device.schedule_id ? scheduleNameById.get(device.schedule_id) ?? "Custom schedule" : "Default policy"}
                saving={savingKey === device.id && removingKey !== device.id}
                disabled={!isAdmin || savingKey === device.id}
                onDelete={() => deleteDevice(device)}
                onSave={() => saveDevice(device)}
                onUpdate={(patch) => updateDevice(device.id, patch)}
                onUpdateBinding={(provider, externalId) => updateBinding(device.id, provider, externalId)}
              />
            )) : !devicesLoading ? (
              <div className="empty-state compact">No {deviceNounPlural} configured</div>
            ) : null}
          </div>
        </section>
      </div>
    </section>
  );
}

function AccessDeviceSummaryStat({ label, value, tone = "blue" }: { label: string; value: string; tone?: "blue" | "green" | "gray" }) {
  return (
    <div className={`access-device-summary-stat ${tone}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function AccessProviderChoice({
  allowNone = false,
  disabled,
  helper,
  label,
  value,
  onChange
}: {
  allowNone?: boolean;
  disabled: boolean;
  helper: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const Icon = accessDeviceProviders.find((provider) => provider.key === value)?.icon ?? CircleDot;
  return (
    <label className="access-provider-choice">
      <span className="access-provider-choice-label">{label}</span>
      <span className="access-provider-choice-control">
        <Icon size={16} />
        <select disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)}>
          {allowNone ? <option value="none">None</option> : null}
          {accessDeviceProviders.map((provider) => <option key={provider.key} value={provider.key}>{provider.label}</option>)}
        </select>
      </span>
      <small>{helper}</small>
    </label>
  );
}

function AccessDeviceLoadingBar({ label }: { label: string }) {
  return (
    <div className="access-device-loading-bar" role="status" aria-live="polite">
      <div className="access-device-loading-track"><span /></div>
      <div className="access-device-loading-meta">
        <span>{label}</span>
      </div>
    </div>
  );
}
