import {
CalendarDays,
Camera,
CircleDot,
Construction,
Database,
Home,
Key,
Loader2,
MessageCircle,
PlugZap,
Plus,
RefreshCw,
ShieldCheck,
SlidersHorizontal,
Smartphone,
Trash2,
UserPlus,
UserRound,
Users,
X,
Zap
} from "lucide-react";
import React from "react";

import {
AccessDevice,
api,
Badge,
CardHeader,
coerceSettingsPayload,
createActionConfirmation,
displayUserName,
fileToDataUrl,
formatDate,
Group,
MaintenanceStatus,
mediaSource,
PanelHeader,
Person,
Schedule,
SettingField,
SettingFieldDefinition,
stringifySetting,
Toolbar,
UnifiProtectCamera,
UserAccount,
UserAvatar,
UserRole,
useSettings,
Vehicle
} from "../shared";



export function SettingsView({
  currentUser,
  groups,
  schedules,
  vehicles
}: {
  currentUser: UserAccount;
  groups: Group[];
  schedules: Schedule[];
  vehicles: Vehicle[];
}) {
  const activeVehicles = vehicles.filter((vehicle) => vehicle.is_active !== false).length;
  return (
    <section className="dashboard-grid settings-grid">
      <div className="card span-2">
        <CardHeader icon={SlidersHorizontal} title="Appearance" />
        <div className="settings-list">
          <SettingRow label="Default mode" value="System" />
          <SettingRow label="Status palette" value="Blue, green, gray, amber, red" />
          <SettingRow label="Card radius" value="8px" />
        </div>
      </div>
      <div className="card">
        <CardHeader icon={Users} title="User Accounts" />
        <div className="compact-row">
          <UserAvatar user={currentUser} />
          <div>
            <strong>{displayUserName(currentUser)}</strong>
            <span>{currentUser.role === "admin" ? "Administrator" : "Standard access"}</span>
          </div>
          <Badge tone="green">protected</Badge>
        </div>
      </div>
      <div className="card span-3">
        <CardHeader icon={Database} title="Operational Data" action={<Badge tone="blue">current</Badge>} />
        <div className="settings-list">
          <SettingRow label="Access schedules" value={String(schedules.length)} />
          <SettingRow label="Access groups" value={String(groups.length)} />
          <SettingRow label="Active vehicles" value={`${activeVehicles}/${vehicles.length}`} />
        </div>
      </div>
    </section>
  );
}

export const GATE_LPR_CAMERA_NAME = "gate lpr";

export const GATE_LPR_CAMERA_DEVICE = "942A6FD09D64";

export type GateLprSmartZonesState = {
  loading: boolean;
  error: string;
  camera: UnifiProtectCamera | null;
  zones: UnifiProtectCamera["smart_detect_zones"];
};

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

export function useGateLprSmartZones(enabled: boolean): GateLprSmartZonesState {
  const [state, setState] = React.useState<GateLprSmartZonesState>({
    loading: false,
    error: "",
    camera: null,
    zones: []
  });

  React.useEffect(() => {
    if (!enabled) {
      setState({ loading: false, error: "", camera: null, zones: [] });
      return;
    }
    let active = true;
    setState((current) => ({ ...current, loading: true, error: "" }));
    api.get<{ cameras: UnifiProtectCamera[] }>("/api/v1/integrations/unifi-protect/cameras")
      .then((payload) => {
        if (!active) return;
        const camera = findGateLprCamera(payload.cameras);
        setState({
          loading: false,
          error: "",
          camera,
          zones: camera?.smart_detect_zones ?? []
        });
      })
      .catch((loadError) => {
        if (!active) return;
        setState({
          loading: false,
          error: loadError instanceof Error ? loadError.message : "Unable to load UniFi Protect cameras.",
          camera: null,
          zones: []
        });
      });
    return () => {
      active = false;
    };
  }, [enabled]);

  return state;
}

export function findGateLprCamera(cameras: UnifiProtectCamera[]) {
  return cameras.find((camera) => normalizeCameraIdentifier(camera.name) === GATE_LPR_CAMERA_NAME)
    ?? cameras.find((camera) => normalizeCameraIdentifier(camera.mac) === normalizeCameraIdentifier(GATE_LPR_CAMERA_DEVICE))
    ?? cameras.find((camera) => {
      const label = normalizeCameraIdentifier(camera.name);
      return label.includes("gate") && label.includes("lpr");
    })
    ?? null;
}

export function normalizeCameraIdentifier(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

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
  const { values, loading, error, save, reload } = useSettings(category);
  const [form, setForm] = React.useState<Record<string, string>>({});
  const [saved, setSaved] = React.useState("");
  const fields = settingsFields(category);
  const gateLprSmartZones = useGateLprSmartZones(category === "lpr");
  const lastRefreshTokenRef = React.useRef(refreshToken);

  React.useEffect(() => {
    const next: Record<string, string> = {};
    for (const field of fields) {
      next[field.key] = stringifySetting(values[field.key]);
    }
    setForm(next);
  }, [values, category]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    reload().catch(() => undefined);
  }, [refreshToken, reload]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaved("");
    await save(coerceSettingsPayload(form));
    setSaved("Settings saved.");
  };

  return (
    <section className="view-stack settings-page">
      <Toolbar title={title} count={fields.length} icon={Icon} />
      <form className="dashboard-grid settings-grid" onSubmit={submit}>
        <div className="card span-2">
          <CardHeader icon={Icon} title={title} action={<Badge tone={loading ? "gray" : "green"}>{loading ? "loading" : "database"}</Badge>} />
          {category === "general" ? (
            <MaintenanceModeSettings
              currentUser={currentUser}
              status={maintenanceStatus ?? null}
              onStatusChanged={onMaintenanceStatusChanged}
            />
          ) : null}
          <div className="settings-form-grid">
            {fields.map((field) => {
              const onChange = (value: string) => setForm((current) => ({ ...current, [field.key]: value }));
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
                  key={field.key}
                  value={form[field.key] ?? ""}
                  onChange={onChange}
                />
              );
            })}
          </div>
          {error ? <div className="auth-error inline-error">{error}</div> : null}
          {saved ? <div className="success-note">{saved}</div> : null}
          <div className="modal-actions">
            <button className="primary-button" type="submit">Save Settings</button>
          </div>
        </div>
        {category === "auth" ? <AuthSecretSecurityPanel refreshToken={refreshToken} /> : null}
        <div className="card">
          <CardHeader icon={Database} title="Source" />
          <div className="settings-list">
            <SettingRow label="Storage" value="Database" />
            <SettingRow label="Secrets" value="Encrypted at rest" />
            <SettingRow label="Bootstrap" value="Secret file + env override" />
          </div>
        </div>
      </form>
    </section>
  );
}

type AccessDeviceKind = "gate" | "garage_door";
type AccessDeviceDiscoveryItem = {
  entity_id: string;
  name: string | null;
  state?: string | null;
  kind?: string;
  device_class?: string | null;
  metadata?: Record<string, unknown>;
};

export function AccessDevicesSettingsView({
  kind,
  title,
  icon: Icon,
  refreshToken,
  schedules
}: {
  kind: AccessDeviceKind;
  title: string;
  icon: React.ElementType;
  refreshToken: number;
  schedules: Schedule[];
}) {
  const [devices, setDevices] = React.useState<AccessDevice[]>([]);
  const [devicesLoading, setDevicesLoading] = React.useState(true);
  const [discoveryLoading, setDiscoveryLoading] = React.useState(false);
  const [savingKey, setSavingKey] = React.useState("");
  const [providerSavingKey, setProviderSavingKey] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [error, setError] = React.useState("");
  const [homeAssistantCovers, setHomeAssistantCovers] = React.useState<AccessDeviceDiscoveryItem[]>([]);
  const [esphomeCovers, setEsphomeCovers] = React.useState<AccessDeviceDiscoveryItem[]>([]);
  const accessSettings = useSettings("access");
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const loadDevices = React.useCallback(async (showLoading = true) => {
    if (showLoading) setDevicesLoading(true);
    setError("");
    try {
      setDevices(await api.get<AccessDevice[]>(`/api/v1/access-devices?kind=${kind}`));
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
  const deviceNoun = kind === "gate" ? "gate" : "garage door";
  const deviceNounPlural = kind === "gate" ? "gates" : "garage doors";

  const updateDevice = (deviceId: string, patch: Partial<AccessDevice>) => {
    setDevices((current) => current.map((device) => device.id === deviceId ? { ...device, ...patch } : device));
  };

  const updateBinding = (deviceId: string, provider: string, selection: string) => {
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
    setError("");
    const suffix = devices.length + 1;
    const baseKey = kind === "gate" ? `gate_${suffix}` : `garage_door_${suffix}`;
    try {
      const created = await api.post<AccessDevice>("/api/v1/access-devices", {
        key: baseKey,
        kind,
        name: kind === "gate" ? `Gate ${suffix}` : `Garage Door ${suffix}`,
        enabled: true,
        schedule_id: null,
        open_for_access: kind === "gate",
        sort_order: devices.length
      });
      setDevices((current) => [...current, created]);
      setMessage("Device added.");
    } catch (addError) {
      setError(addError instanceof Error ? addError.message : "Unable to add access device.");
    }
  };

  const saveDevice = async (device: AccessDevice) => {
    setSavingKey(device.id);
    setMessage("");
    setError("");
    try {
      let saved = await api.patch<AccessDevice>(`/api/v1/access-devices/${encodeURIComponent(device.id)}`, {
        key: device.key,
        kind: device.kind,
        name: device.name,
        enabled: device.enabled,
        schedule_id: device.schedule_id || null,
        open_for_access: device.open_for_access,
        sort_order: device.sort_order
      });
      for (const provider of ["home_assistant", "esphome"]) {
        const binding = device.bindings.find((item) => item.provider === provider);
        saved = await api.put<AccessDevice>(`/api/v1/access-devices/${encodeURIComponent(saved.id)}/bindings/${provider}`, {
          external_id: binding?.external_id ?? "",
          enabled: Boolean(binding?.external_id),
          config: binding?.config ?? {}
        });
      }
      setDevices((current) => current.map((item) => item.id === saved.id ? saved : item));
      setMessage("Device saved.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save access device.");
    } finally {
      setSavingKey("");
    }
  };

  const saveProviderSetting = async (key: "gate_control_provider" | "gate_failover_provider", value: string) => {
    setProviderSavingKey(key);
    setError("");
    setMessage("");
    try {
      await accessSettings.save({ [key]: value });
      setMessage("Provider preference saved.");
    } catch (providerError) {
      setError(providerError instanceof Error ? providerError.message : "Unable to save provider preference.");
    } finally {
      setProviderSavingKey("");
    }
  };

  const deleteDevice = async (device: AccessDevice) => {
    if (!window.confirm(`Remove ${device.name}?`)) return;
    setError("");
    try {
      await api.delete(`/api/v1/access-devices/${encodeURIComponent(device.id)}`);
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
                <p>{providerLabel(primaryProvider)} sends {deviceNoun} commands first{failoverProvider === "none" ? "." : `, then ${providerLabel(failoverProvider)} only if needed.`}</p>
              </div>
            </div>
            <Badge tone={accessSettings.loading || providerSavingKey ? "gray" : "blue"}>{providerSavingKey ? "Saving" : "Global"}</Badge>
          </div>
          <div className="access-provider-route">
            <AccessProviderChoice
              disabled={accessSettings.loading || Boolean(providerSavingKey)}
              helper="Used for every normal open or close command."
              label="Primary"
              value={primaryProvider}
              onChange={(value) => saveProviderSetting("gate_control_provider", value)}
            />
            <div className="access-provider-route-join" aria-hidden="true">then</div>
            <AccessProviderChoice
              allowNone
              disabled={accessSettings.loading || Boolean(providerSavingKey)}
              helper="Only used when the primary integration is unavailable."
              label="Failover"
              value={failoverProvider}
              onChange={(value) => saveProviderSetting("gate_failover_provider", value)}
            />
          </div>
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
            <button className="secondary-button" onClick={addDevice} disabled={devicesLoading} type="button"><Plus size={15} /> Add {kind === "gate" ? "Gate" : "Door"}</button>
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

function AccessDeviceEditor({
  device,
  deviceIcon: DeviceIcon,
  homeAssistantCovers,
  esphomeCovers,
  scheduleLabel,
  schedules,
  saving,
  onDelete,
  onSave,
  onUpdate,
  onUpdateBinding
}: {
  device: AccessDevice;
  deviceIcon: React.ElementType;
  homeAssistantCovers: AccessDeviceDiscoveryItem[];
  esphomeCovers: AccessDeviceDiscoveryItem[];
  scheduleLabel: string;
  schedules: Schedule[];
  saving: boolean;
  onDelete: () => void;
  onSave: () => void;
  onUpdate: (patch: Partial<AccessDevice>) => void;
  onUpdateBinding: (provider: string, externalId: string) => void;
}) {
  const haBinding = device.bindings.find((binding) => binding.provider === "home_assistant");
  const esphomeBinding = device.bindings.find((binding) => binding.provider === "esphome");
  const haMapped = Boolean(haBinding?.external_id);
  const esphomeMapped = Boolean(esphomeBinding?.external_id);
  return (
    <article className={device.enabled ? "access-device-editor" : "access-device-editor disabled"}>
      <header className="access-device-editor-head">
        <div className="access-device-title-row">
          <span className="access-device-symbol"><DeviceIcon size={18} /></span>
          <div>
            <h3>{device.name || "Unnamed device"}</h3>
            <span>{device.key || "No internal key set"}</span>
          </div>
        </div>
        <div className="access-device-badges">
          <Badge tone={device.enabled ? "green" : "gray"}>{device.enabled ? "Enabled" : "Disabled"}</Badge>
          <Badge tone="gray">{scheduleLabel}</Badge>
          {device.kind === "gate" ? <Badge tone={device.open_for_access ? "blue" : "gray"}>{device.open_for_access ? "Access opens" : "Manual only"}</Badge> : null}
          <Badge tone={haMapped || esphomeMapped ? "blue" : "amber"}>{haMapped || esphomeMapped ? "Mapped" : "Needs mapping"}</Badge>
        </div>
      </header>
      <div className="access-device-editor-body">
        <div className="access-device-block identity">
          <div className="access-device-block-title">
            <Key size={15} />
            <h4>Identity</h4>
          </div>
          <div className="settings-form-grid access-device-fields">
            <SettingField field={{ key: "name", label: "Display name" }} value={device.name} onChange={(value) => onUpdate({ name: value })} />
            <SettingField field={{ key: "key", label: "Internal key" }} value={device.key} onChange={(value) => onUpdate({ key: value })} />
          </div>
        </div>

        <div className="access-device-block policy">
          <div className="access-device-block-title">
            <CalendarDays size={15} />
            <h4>Policy</h4>
          </div>
          <div className="access-device-policy-grid">
            <AccessDeviceSwitch checked={device.enabled} label="Device enabled" onChange={(checked) => onUpdate({ enabled: checked })} />
            {device.kind === "gate" ? (
              <AccessDeviceSwitch checked={device.open_for_access} label="Open for access events" onChange={(checked) => onUpdate({ open_for_access: checked })} />
            ) : null}
            <label className="field">
              <span>Schedule</span>
              <select value={device.schedule_id ?? ""} onChange={(event) => onUpdate({ schedule_id: event.target.value || null })}>
                <option value="">Default policy</option>
                {schedules.map((schedule) => <option key={schedule.id} value={schedule.id}>{schedule.name}</option>)}
              </select>
            </label>
          </div>
        </div>

        <div className="access-device-block providers">
          <div className="access-device-block-title">
            <PlugZap size={15} />
            <h4>Provider bindings</h4>
          </div>
          <div className="settings-form-grid access-device-fields">
            <ProviderBindingField provider="home_assistant" label="Home Assistant cover" options={homeAssistantCovers} value={haBinding?.external_id ?? ""} onChange={(value) => onUpdateBinding("home_assistant", value)} />
            <ProviderBindingField provider="esphome" label="ESPHome cover" options={esphomeCovers} value={bindingSelectionValue(esphomeBinding, esphomeCovers)} onChange={(value) => onUpdateBinding("esphome", value)} />
          </div>
        </div>
      </div>
      <div className="access-device-actions">
        <button className="secondary-button danger" onClick={onDelete} type="button"><Trash2 size={15} /> Remove</button>
        <button className="primary-button" disabled={saving} onClick={onSave} type="button">{saving ? "Saving..." : "Save Device"}</button>
      </div>
    </article>
  );
}

function AccessDeviceSwitch({ checked, label, onChange }: { checked: boolean; label: string; onChange: (checked: boolean) => void }) {
  return (
    <label className="access-device-switch">
      <input checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
      <span aria-hidden="true" />
      <strong>{label}</strong>
    </label>
  );
}

function ProviderBindingField({ label, options, provider, value, onChange }: { label: string; options: AccessDeviceDiscoveryItem[]; provider: "home_assistant" | "esphome"; value: string; onChange: (value: string) => void }) {
  const listId = React.useId();
  const mapped = Boolean(value.trim());
  const Icon = provider === "esphome" ? Zap : Home;
  return (
    <label className={mapped ? "field access-binding-field mapped" : "field access-binding-field"}>
      <span className="field-label-row">
        <span>{label}</span>
        <span className="access-binding-state">{mapped ? "Mapped" : "Not mapped"}</span>
      </span>
      <span className="field-control access-binding-control">
        <Icon size={16} />
        <input list={listId} value={value} onChange={(event) => onChange(event.target.value)} placeholder="Select or enter an external ID" />
      </span>
      <datalist id={listId}>
        {options.map((option) => <option key={option.entity_id} value={option.entity_id}>{option.name || option.entity_id}</option>)}
      </datalist>
      <small className="field-hint">{options.length ? `${options.length} discovered ${options.length === 1 ? "cover" : "covers"}` : "No discovered covers for this provider."}</small>
    </label>
  );
}

function bindingSelectionValue(binding: AccessDevice["bindings"][number] | undefined, options: AccessDeviceDiscoveryItem[]) {
  if (!binding) return "";
  const deviceId = String(binding.config?.device_id ?? "");
  const match = options.find((option) => {
    const optionDeviceId = String(option.metadata?.device_id ?? "");
    const optionExternalId = String(option.metadata?.external_id ?? option.entity_id);
    return optionExternalId === binding.external_id && (!deviceId || optionDeviceId === deviceId);
  });
  return match?.entity_id ?? binding.external_id;
}

function coverMatchesKind(cover: AccessDeviceDiscoveryItem, kind: AccessDeviceKind) {
  if (cover.kind === kind) return true;
  const label = `${cover.entity_id} ${cover.name ?? ""} ${cover.device_class ?? ""}`.toLowerCase();
  return kind === "garage_door" ? label.includes("garage") || label.includes("door") : label.includes("gate");
}

function deviceHasProviderBinding(device: AccessDevice, provider: string) {
  return device.bindings.some((binding) => binding.provider === provider && Boolean(binding.external_id));
}

function deviceHasAnyProviderBinding(device: AccessDevice) {
  return device.bindings.some((binding) => Boolean(binding.external_id));
}

function providerLabel(provider: string) {
  if (provider === "esphome") return "ESPHome";
  if (provider === "home_assistant") return "Home Assistant";
  return "No failover";
}

function bindingConfigForDiscovery(provider: string, selection: string, options: AccessDeviceDiscoveryItem[]) {
  const match = options.find((option) => option.entity_id === selection);
  if (!match) return {};
  return provider === "esphome" ? { ...(match.metadata ?? {}) } : {};
}

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
      const payload = {
        confirmed: true,
        new_secret: customSecret.trim() || undefined
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
      <CardHeader icon={ShieldCheck} title="Auth Secret" action={<Badge tone={status?.rotation_required ? "amber" : "green"}>{status?.rotation_required ? "rotate" : "ready"}</Badge>} />
      {loading ? (
        <div className="compact-row"><Loader2 size={16} /> Loading security status...</div>
      ) : status ? (
        <div className="settings-list">
          <SettingRow label="Source" value={sourceLabel} />
          <SettingRow label="Environment" value={status.environment} />
          <SettingRow label="File" value={status.file_path} />
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

export function UsersView({
  currentUser,
  onCurrentUserUpdated,
  refreshToken
}: {
  currentUser: UserAccount;
  onCurrentUserUpdated: (user: UserAccount) => void;
  refreshToken: number;
}) {
  const [users, setUsers] = React.useState<UserAccount[]>([]);
  const [people, setPeople] = React.useState<Person[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [modal, setModal] = React.useState<"create" | "edit" | null>(null);
  const [selectedUser, setSelectedUser] = React.useState<UserAccount | null>(null);
  const [temporaryPassword, setTemporaryPassword] = React.useState<string | null>(null);
  const isAdmin = currentUser.role === "admin";
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const loadUsers = React.useCallback(async () => {
    setError("");
    try {
      const [nextUsers, nextPeople] = await Promise.all([
        api.get<UserAccount[]>("/api/v1/users"),
        api.get<Person[]>("/api/v1/people")
      ]);
      setUsers(nextUsers);
      setPeople(nextPeople);
    } catch (userError) {
      setError(userError instanceof Error ? userError.message : "Unable to load users");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    loadUsers().catch(() => undefined);
  }, [loadUsers]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    loadUsers().catch(() => undefined);
  }, [loadUsers, refreshToken]);

  const openCreate = () => {
    setTemporaryPassword(null);
    setSelectedUser(null);
    setModal("create");
  };

  const openEdit = (user: UserAccount) => {
    setTemporaryPassword(null);
    setSelectedUser(user);
    setModal("edit");
  };

  const closeModal = () => {
    setModal(null);
    setSelectedUser(null);
  };

  const deleteUser = async (user: UserAccount) => {
    if (!window.confirm(`Delete ${displayUserName(user)}?`)) return;
    setError("");
    try {
      await api.delete(`/api/v1/users/${user.id}`);
      await loadUsers();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete user");
    }
  };

  const toggleActive = async (user: UserAccount) => {
    setError("");
    try {
      const savedUser = await api.patch<UserAccount>(`/api/v1/users/${user.id}`, { is_active: !user.is_active });
      if (savedUser.id === currentUser.id) {
        onCurrentUserUpdated(savedUser);
      }
      await loadUsers();
    } catch (updateError) {
      setError(updateError instanceof Error ? updateError.message : "Unable to update user");
    }
  };

  const resetPassword = async (user: UserAccount) => {
    setError("");
    try {
      const result = await api.post<{ temporary_password: string }>(`/api/v1/users/${user.id}/reset-password`, {
        generate_password: true
      });
      setSelectedUser(user);
      setTemporaryPassword(result.temporary_password);
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : "Unable to reset password");
    }
  };

  return (
    <section className="view-stack users-page">
      <div className="users-hero card">
        <div>
          <span className="eyebrow">Settings</span>
          <h1>Users</h1>
          <p>Manage dashboard access for family members.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <UserPlus size={17} /> Add User
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {temporaryPassword ? (
        <div className="temporary-password-card card">
          <div>
            <strong>Temporary password for {selectedUser ? displayUserName(selectedUser) : "user"}</strong>
            <span>{temporaryPassword}</span>
          </div>
          <button className="secondary-button" onClick={() => navigator.clipboard?.writeText(temporaryPassword)} type="button">
            Copy
          </button>
        </div>
      ) : null}

      <div className="card users-card">
        <PanelHeader title="User Roster" action={`${users.length} accounts`} actionKind="select" />
        {loading ? (
          <div className="empty-state">Loading users</div>
        ) : (
          <div className="users-table">
            {users.map((user) => (
              <article className="user-row" key={user.id}>
                <UserAvatar user={user} />
                <div>
                  <strong>{displayUserName(user)}</strong>
                  <span>
                    @{user.username}{user.email ? ` • ${user.email}` : ""}
                    {user.mobile_phone_number ? ` • ${user.mobile_phone_number}` : ""}
                    {user.person_id ? ` • linked to ${people.find((person) => person.id === user.person_id)?.display_name ?? "directory person"}` : ""}
                  </span>
                </div>
                <Badge tone={user.role === "admin" ? "blue" : "gray"}>{user.role === "admin" ? "Admin" : "Standard"}</Badge>
                <Badge tone={user.is_active ? "green" : "amber"}>{user.is_active ? "Active" : "Inactive"}</Badge>
                <time>{user.last_login_at ? formatDate(user.last_login_at) : "Never signed in"}</time>
                {isAdmin ? (
                  <div className="user-actions">
                    <button className="secondary-button" onClick={() => openEdit(user)} type="button">Edit</button>
                    <button className="secondary-button" onClick={() => resetPassword(user)} type="button">Reset</button>
                    <button className="secondary-button" onClick={() => toggleActive(user)} type="button">{user.is_active ? "Deactivate" : "Activate"}</button>
                    <button className="icon-button danger" onClick={() => deleteUser(user)} type="button" aria-label={`Delete ${displayUserName(user)}`}>
                      <Trash2 size={16} />
                    </button>
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </div>

      {modal ? (
        <UserModal
          mode={modal}
          people={people}
          user={selectedUser}
          onClose={closeModal}
          onSaved={async (password, savedUser) => {
            setTemporaryPassword(password);
            if (savedUser?.id === currentUser.id) {
              onCurrentUserUpdated(savedUser);
            }
            await loadUsers();
            closeModal();
            setSelectedUser(savedUser ?? null);
          }}
        />
      ) : null}
    </section>
  );
}

export function UserModal({
  mode,
  people,
  user,
  onClose,
  onSaved
}: {
  mode: "create" | "edit";
  people: Person[];
  user: UserAccount | null;
  onClose: () => void;
  onSaved: (temporaryPassword: string | null, savedUser?: UserAccount) => Promise<void>;
}) {
  const [form, setForm] = React.useState({
    username: user?.username ?? "",
    first_name: user?.first_name ?? "",
    last_name: user?.last_name ?? "",
    email: user?.email ?? "",
    mobile_phone_number: user?.mobile_phone_number ?? "",
    profile_photo_data_url: user?.profile_photo_data_url ?? "",
    person_id: user?.person_id ?? "",
    role: user?.role ?? "standard",
    is_active: user?.is_active ?? true,
    temporary_password: "",
    generate_password: mode === "create"
  });
  const existingProfilePhotoSource = mediaSource(user?.profile_photo_url, user?.profile_photo_data_url);
  const [profilePhotoChanged, setProfilePhotoChanged] = React.useState(false);
  const profilePhotoPreview = form.profile_photo_data_url || (!profilePhotoChanged ? existingProfilePhotoSource : "");
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const update = (field: keyof typeof form, value: string | boolean) => setForm((current) => ({ ...current, [field]: value }));

  const uploadPhoto = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("Please choose an image file.");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      setError("Profile images must be 8 MB or smaller.");
      return;
    }
    setError("");
    setProfilePhotoChanged(true);
    update("profile_photo_data_url", await fileToDataUrl(file));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (mode === "create") {
        const payload: Record<string, unknown> = {
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          email: form.email || null,
          mobile_phone_number: form.mobile_phone_number || null,
          person_id: form.person_id || null,
          role: form.role,
          is_active: form.is_active,
          temporary_password: form.generate_password ? null : form.temporary_password,
          generate_password: form.generate_password
        };
        payload.profile_photo_data_url = form.profile_photo_data_url || null;
        const result = await api.post<{ user: UserAccount; temporary_password: string | null }>("/api/v1/users", payload);
        await onSaved(result.temporary_password, result.user);
      } else if (user) {
        const payload: Record<string, unknown> = {
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          email: form.email || null,
          mobile_phone_number: form.mobile_phone_number || null,
          person_id: form.person_id || null,
          role: form.role,
          is_active: form.is_active
        };
        if (profilePhotoChanged) {
          payload.profile_photo_data_url = form.profile_photo_data_url || null;
        }
        const savedUser = await api.patch<UserAccount>(`/api/v1/users/${user.id}`, payload);
        await onSaved(null, savedUser);
      }
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save user");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "create" ? "Add User" : "Edit User"}</h2>
            <p>{mode === "create" ? "Create a dashboard login." : "Update account access."}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
        <div className="profile-upload-row">
          <UserAvatar
            user={{
              id: user?.id ?? "preview",
              username: form.username,
              first_name: String(form.first_name),
              last_name: String(form.last_name),
              full_name: `${form.first_name} ${form.last_name}`.trim(),
              profile_photo_data_url: profilePhotoPreview.startsWith("data:") ? profilePhotoPreview : null,
              profile_photo_url: profilePhotoPreview && !profilePhotoPreview.startsWith("data:") ? profilePhotoPreview : null,
              email: form.email || null,
              mobile_phone_number: String(form.mobile_phone_number || "") || null,
              role: form.role as UserRole,
              is_active: Boolean(form.is_active),
              last_login_at: user?.last_login_at ?? null,
              person_id: String(form.person_id || "") || null,
              preferences: user?.preferences ?? { sidebarCollapsed: false },
              created_at: user?.created_at ?? new Date().toISOString(),
              updated_at: user?.updated_at ?? new Date().toISOString()
            }}
            size="large"
          />
          <label className="upload-button">
            <Camera size={16} />
            <span>{profilePhotoPreview ? "Change photo" : "Upload profile picture"}</span>
            <input accept="image/*" onChange={uploadPhoto} type="file" />
          </label>
          {profilePhotoPreview ? (
            <button
              className="secondary-button"
              onClick={() => {
                setProfilePhotoChanged(true);
                update("profile_photo_data_url", "");
              }}
              type="button"
            >
              Remove
            </button>
          ) : null}
        </div>
        <div className="field-grid">
          <label className="field">
            <span>First name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.first_name} onChange={(event) => update("first_name", event.target.value)} required />
            </div>
          </label>
          <label className="field">
            <span>Last name</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.last_name} onChange={(event) => update("last_name", event.target.value)} required />
            </div>
          </label>
        </div>
        <div className="field-grid">
          <label className="field">
            <span>Username</span>
            <div className="field-control">
              <UserRound size={17} />
              <input value={form.username} onChange={(event) => update("username", event.target.value)} required />
            </div>
          </label>
        </div>
        <label className="field">
          <span>Email</span>
          <div className="field-control">
            <MessageCircle size={17} />
            <input value={form.email} onChange={(event) => update("email", event.target.value)} type="email" />
          </div>
        </label>
        <label className="field">
          <span>Mobile phone</span>
          <div className="field-control">
            <Smartphone size={17} />
            <input value={form.mobile_phone_number} onChange={(event) => update("mobile_phone_number", event.target.value)} type="tel" />
          </div>
        </label>
        <label className="field">
          <span>Directory person</span>
          <select value={form.person_id} onChange={(event) => update("person_id", event.target.value)}>
            <option value="">No linked person</option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>{person.display_name}</option>
            ))}
          </select>
        </label>
        <div className="field-grid">
          <label className="field">
            <span>Role</span>
            <select value={form.role} onChange={(event) => update("role", event.target.value)}>
              <option value="standard">Standard User</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <label className="field">
            <span>Status</span>
            <select value={form.is_active ? "active" : "inactive"} onChange={(event) => update("is_active", event.target.value === "active")}>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
        </div>
        {mode === "create" ? (
          <>
            <label className="check-row">
              <input checked={form.generate_password} onChange={(event) => update("generate_password", event.target.checked)} type="checkbox" />
              <span>Generate a temporary password</span>
            </label>
            {!form.generate_password ? (
              <label className="field">
                <span>Temporary password</span>
                <div className="field-control">
                  <Key size={17} />
                  <input value={form.temporary_password} onChange={(event) => update("temporary_password", event.target.value)} type="password" minLength={10} required />
                </div>
              </label>
            ) : null}
          </>
        ) : null}
        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "Saving..." : "Save User"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function SettingRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="setting-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function GateLprSmartZoneField({
  field,
  state,
  value,
  onChange
}: {
  field: SettingFieldDefinition;
  state: GateLprSmartZonesState;
  value: string;
  onChange: (value: string) => void;
}) {
  const selected = firstSettingListValue(value);
  const zones = uniqueGateLprSmartZones(state.zones);
  const selectedZone = zones.find((zone) => normalizeSmartZoneName(zone.name) === normalizeSmartZoneName(selected));
  const selectValue = selectedZone?.name ?? selected;
  const disabled = state.loading || Boolean(state.error) || !state.camera || zones.length === 0;
  const status = gateLprSmartZoneStatus(state);
  return (
    <label className="field">
      <span>{field.label}</span>
      <select value={selectValue} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        <option value="">{state.loading ? "Loading zones..." : "Select smart zone"}</option>
        {selected && !selectedZone ? <option value={selected}>{selected}</option> : null}
        {zones.map((zone) => (
          <option key={`${zone.id ?? zone.name}:${zone.name}`} value={zone.name}>
            {zone.name}
          </option>
        ))}
      </select>
      <small className="field-hint">{status}</small>
    </label>
  );
}

export function firstSettingListValue(value: string) {
  return value.replace(/,/g, "\n").split(/\r?\n/).map((item) => item.trim()).filter(Boolean)[0] ?? "";
}

export function uniqueGateLprSmartZones(zones: UnifiProtectCamera["smart_detect_zones"]) {
  const seen = new Set<string>();
  return zones.filter((zone) => {
    const name = String(zone.name ?? "").trim();
    const normalized = normalizeSmartZoneName(name);
    if (!normalized || seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
}

export function normalizeSmartZoneName(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

export function gateLprSmartZoneStatus(state: GateLprSmartZonesState) {
  if (state.loading) return "Loading Gate LPR smart zones from UniFi Protect.";
  if (state.error) return `UniFi Protect zones unavailable: ${state.error}`;
  if (!state.camera) return "Gate LPR camera was not found.";
  if (state.zones.length === 0) return "Gate LPR camera has no smart detect zones.";
  return `Gate LPR camera: ${state.camera.name}.`;
}

export function settingsFields(category: "general" | "auth" | "lpr"): SettingFieldDefinition[] {
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
