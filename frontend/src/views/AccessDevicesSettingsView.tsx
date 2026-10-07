import { CircleDot, Home, Plus, SlidersHorizontal, Zap } from "lucide-react";
import React from "react";

import { api, createActionConfirmation } from "../api/client";
import { integrationsApi } from "../api/integrations";
import { stringifySetting, useSettings } from "../lib/settings";
import { Badge } from "../ui/primitives";
import type { AccessDevice, Schedule, UserAccount } from "../api/types";
import { coverMatchesKind, deviceHasProviderBinding, deviceHasAnyProviderBinding, providerLabel, bindingConfigForDiscovery } from "../features/settings/accessDeviceModel";
import type { AccessDeviceKind, AccessDeviceDiscoveryItem } from "../features/settings/accessDeviceModel";
import { AccessDeviceEditor } from "../features/settings/AccessDeviceEditor";

export function AccessDevicesSettingsView({
  kind,
  title,
  icon: Icon,
  currentUser,
  refreshToken,
  schedules
}: {
  kind: AccessDeviceKind;
  title: string;
  icon: React.ElementType;
  currentUser: UserAccount;
  refreshToken: number;
  schedules: Schedule[];
}) {
  const [devices, setDevices] = React.useState<AccessDevice[]>([]);
  const [admissionChoices, setAdmissionChoices] = React.useState<Array<{ key: string; name: string }>>([]);
  const [devicesLoading, setDevicesLoading] = React.useState(true);
  const [discoveryLoading, setDiscoveryLoading] = React.useState(false);
  const [savingKey, setSavingKey] = React.useState("");
  const [providerSavingKey, setProviderSavingKey] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [error, setError] = React.useState("");
  const [homeAssistantCovers, setHomeAssistantCovers] = React.useState<AccessDeviceDiscoveryItem[]>([]);
  const [esphomeCovers, setEsphomeCovers] = React.useState<AccessDeviceDiscoveryItem[]>([]);
  const accessSettings = useSettings("access");
  const dirtyDeviceIds = React.useRef(new Set<string>());
  const savingKeyRef = React.useRef("");
  const lastRefreshTokenRef = React.useRef(refreshToken);
  const isAdmin = currentUser.role === "admin";

  const loadDevices = React.useCallback(async (showLoading = true) => {
    if (showLoading) setDevicesLoading(true);
    setError("");
    try {
      const saved = await integrationsApi.getAccessDevices(kind);
      setDevices((current) => saved.map((device) => dirtyDeviceIds.current.has(device.id) ? current.find((item) => item.id === device.id) ?? device : device));
      // Choices are server-validated saved devices, independent of unsaved editor drafts.
      setAdmissionChoices(saved.filter((device) => device.admission_eligible === true).map(({ key, name }) => ({ key, name })));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load access devices.");
    } finally {
      if (showLoading) setDevicesLoading(false);
    }
  }, [kind]);

  const loadDiscovery = React.useCallback(async () => {
    setDiscoveryLoading(true);
    try {
      const [haDiscovery, esphomeDiscovery] = await Promise.all([
        api.get<{ cover_entities: AccessDeviceDiscoveryItem[] }>("/api/v1/integrations/home-assistant/entities").catch(() => ({ cover_entities: [] })),
        api.get<{ cover_entities: AccessDeviceDiscoveryItem[] }>("/api/v1/integrations/esphome/entities").catch(() => ({ cover_entities: [] }))
      ]);
      setHomeAssistantCovers((haDiscovery.cover_entities ?? []).filter((cover) => coverMatchesKind(cover, kind)));
      setEsphomeCovers((esphomeDiscovery.cover_entities ?? []).filter((cover) => coverMatchesKind(cover, kind)));
    } finally {
      setDiscoveryLoading(false);
    }
  }, [kind]);

  React.useEffect(() => {
    loadDevices().catch(() => undefined);
    loadDiscovery().catch(() => undefined);
  }, [loadDevices, loadDiscovery]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    loadDevices().catch(() => undefined);
    loadDiscovery().catch(() => undefined);
  }, [loadDevices, loadDiscovery, refreshToken]);

  const enabledCount = React.useMemo(() => devices.filter((device) => device.enabled).length, [devices]);
  const haMappedCount = React.useMemo(() => devices.filter((device) => deviceHasProviderBinding(device, "home_assistant")).length, [devices]);
  const esphomeMappedCount = React.useMemo(() => devices.filter((device) => deviceHasProviderBinding(device, "esphome")).length, [devices]);
  const mappedCount = React.useMemo(() => devices.filter(deviceHasAnyProviderBinding).length, [devices]);
  const accessOpenCount = React.useMemo(() => devices.filter((device) => device.open_for_access).length, [devices]);
  const scheduleNameById = React.useMemo(() => new Map(schedules.map((schedule) => [schedule.id, schedule.name])), [schedules]);
  const primaryProvider = stringifySetting(accessSettings.values.gate_control_provider || "home_assistant");
  const failoverProvider = stringifySetting(accessSettings.values.gate_failover_provider || "none");
  const admissionDeviceKey = stringifySetting(accessSettings.values.gate_admission_device_key || "");
  const deviceNoun = kind === "gate" ? "gate" : "garage door";
  const deviceNounPlural = kind === "gate" ? "gates" : "garage doors";

  const updateDevice = (deviceId: string, patch: Partial<AccessDevice>) => {
    dirtyDeviceIds.current.add(deviceId);
    setDevices((current) => current.map((device) => device.id === deviceId ? { ...device, ...patch } : device));
  };

  const updateBinding = (deviceId: string, provider: string, selection: string) => {
    dirtyDeviceIds.current.add(deviceId);
    setDevices((current) => current.map((device) => {
      if (device.id !== deviceId) return device;
      const options = provider === "esphome" ? esphomeCovers : homeAssistantCovers;
      const match = options.find((option) => option.entity_id === selection);
      const externalId = String(match?.metadata?.external_id ?? selection);
      return {
        ...device,
        bindings: [
          ...device.bindings.filter((binding) => binding.provider !== provider),
          {
            provider,
            external_id: externalId,
            enabled: Boolean(externalId),
            config: bindingConfigForDiscovery(provider, selection, options)
          }
        ]
      };
    }));
  };

  const addDevice = async () => {
    if (!isAdmin) {
      setError("Administrator access is required to add access devices.");
      return;
    }
    setError("");
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
      const created = await api.post<AccessDevice>("/api/v1/access-devices", {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      setDevices((current) => [...current, created]);
      setMessage("Device added.");
    } catch (addError) {
      setError(addError instanceof Error ? addError.message : "Unable to add access device.");
    }
  };

  const saveDevice = async (device: AccessDevice) => {
    if (savingKeyRef.current) return;
    if (!isAdmin) {
      setError("Administrator access is required to save access devices.");
      return;
    }
    savingKeyRef.current = device.id;
    setSavingKey(device.id);
    setMessage("");
    setError("");
    try {
      const payload = {
        key: device.key,
        kind: device.kind,
        name: device.name,
        enabled: device.enabled,
        // An empty string explicitly clears the assignment and remains bound
        // to confirmation; null is omitted by the existing confirmation protocol.
        schedule_id: device.schedule_id || "",
        open_for_access: device.open_for_access,
        sort_order: device.sort_order
      };
      const confirmation = await createActionConfirmation("access_device.update", { device_id: device.id, ...payload }, {
        target_entity: "AccessDevice",
        target_id: device.id,
        target_label: device.name,
        reason: "Update access device"
      });
      let saved = await api.patch<AccessDevice>(`/api/v1/access-devices/${encodeURIComponent(device.id)}`, {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      for (const provider of ["home_assistant", "esphome"]) {
        const binding = device.bindings.find((item) => item.provider === provider);
        const bindingPayload = {
          external_id: binding?.external_id ?? "",
          enabled: Boolean(binding?.external_id),
          config: binding?.config ?? {}
        };
        const bindingConfirmation = await createActionConfirmation("access_device.binding.update", {
          device_id: saved.id,
          provider,
          ...bindingPayload
        }, {
          target_entity: "AccessDevice",
          target_id: saved.id,
          target_label: saved.name,
          reason: `Update ${providerLabel(provider)} binding`
        });
        saved = await api.put<AccessDevice>(`/api/v1/access-devices/${encodeURIComponent(saved.id)}/bindings/${provider}`, {
          ...bindingPayload,
          confirmation_token: bindingConfirmation.confirmation_token
        });
      }
      setDevices((current) => current.map((item) => item.id === saved.id ? saved : item));
      dirtyDeviceIds.current.delete(saved.id);
      setMessage("Device saved.");
      await loadDevices(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save access device.");
    } finally {
      savingKeyRef.current = "";
      setSavingKey("");
    }
  };

  const saveProviderSetting = async (key: "gate_control_provider" | "gate_failover_provider" | "gate_admission_device_key", value: string) => {
    const isAdmissionSelection = key === "gate_admission_device_key";
    if (!isAdmin) {
      setError("Administrator access is required to save provider preferences.");
      return;
    }
    setProviderSavingKey(key);
    setError("");
    setMessage("");
    try {
      const updates = { [key]: value };
      const confirmation = await createActionConfirmation("settings.update", { values: updates }, {
        target_entity: "SystemSetting",
        target_id: key,
        target_label: isAdmissionSelection ? admissionChoices.find((device) => device.key === value)?.name || "No entry gate selected" : providerLabel(value),
        reason: isAdmissionSelection ? "Designate the automatic-admission entry gate" : "Update access-device provider preference"
      });
      await accessSettings.save(updates, { confirmationToken: confirmation.confirmation_token });
      setMessage(isAdmissionSelection ? "Entry gate selection saved." : "Provider preference saved.");
    } catch (providerError) {
      setError(providerError instanceof Error ? providerError.message : isAdmissionSelection ? "Unable to save entry gate selection." : "Unable to save provider preference.");
    } finally {
      setProviderSavingKey("");
    }
  };

  const deleteDevice = async (device: AccessDevice) => {
    if (!isAdmin) {
      setError("Administrator access is required to remove access devices.");
      return;
    }
    if (!window.confirm(`Remove ${device.name}?`)) return;
    setError("");
    try {
      const payload = { device_id: device.id };
      const confirmation = await createActionConfirmation("access_device.delete", payload, {
        target_entity: "AccessDevice",
        target_id: device.id,
        target_label: device.name,
        reason: "Remove access device"
      });
      await api.delete(`/api/v1/access-devices/${encodeURIComponent(device.id)}`, {
        confirmation_token: confirmation.confirmation_token
      });
      setDevices((current) => current.filter((item) => item.id !== device.id));
      setMessage("Device removed.");
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to remove access device.");
    }
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
              <AccessDeviceSummaryStat label="HA mapped" value={`${haMappedCount}/${devices.length || 0}`} />
              <AccessDeviceSummaryStat label="ESPHome mapped" value={`${esphomeMappedCount}/${devices.length || 0}`} />
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
                <p>{providerLabel(primaryProvider)} sends {deviceNoun} commands first{failoverProvider === "none" ? "." : `, then ${providerLabel(failoverProvider)} only before a command may have been sent.`}</p>
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
              <button className="secondary-button" onClick={addDevice} disabled={devicesLoading} type="button"><Plus size={15} /> Add {kind === "gate" ? "Gate" : "Door"}</button>
            ) : null}
          </div>
          {(devicesLoading || discoveryLoading) ? <AccessDeviceLoadingBar label={devicesLoading ? "Loading access devices" : "Refreshing provider discovery"} /> : null}
          {error ? <div className="auth-error inline-error">{error}</div> : null}
          {accessSettings.error ? <div className="auth-error inline-error">{accessSettings.error}</div> : null}
          {message ? <div className="success-note">{message}</div> : null}
          <div className="access-device-list">
            {devices.length ? devices.map((device) => (
              <AccessDeviceEditor
                device={device}
                deviceIcon={Icon}
                homeAssistantCovers={homeAssistantCovers}
                esphomeCovers={esphomeCovers}
                key={device.id}
                schedules={schedules}
                scheduleLabel={device.schedule_id ? scheduleNameById.get(device.schedule_id) ?? "Custom schedule" : "Default policy"}
                saving={savingKey === device.id}
                disabled={!isAdmin}
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
  const Icon = value === "esphome" ? Zap : value === "home_assistant" ? Home : CircleDot;
  return (
    <label className="access-provider-choice">
      <span className="access-provider-choice-label">{label}</span>
      <span className="access-provider-choice-control">
        <Icon size={16} />
        <select disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)}>
          {allowNone ? <option value="none">None</option> : null}
          <option value="home_assistant">Home Assistant</option>
          <option value="esphome">ESPHome</option>
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
