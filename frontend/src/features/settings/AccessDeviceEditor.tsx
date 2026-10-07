import { CalendarDays, Key, PlugZap, Trash2 } from "lucide-react";
import React from "react";

import { SettingField } from "../../lib/settings";
import { Badge } from "../../ui/primitives";
import type { AccessDevice, Schedule } from "../../api/types";
import { accessDeviceProviders, type AccessDeviceProviderDescriptor } from "./accessDeviceProviders";
import type { AccessDeviceDiscovery } from "./useAccessDeviceDiscovery";
import type { AccessDeviceDiscoveryItem } from "./accessDeviceModel";

export function AccessDeviceEditor({
  device,
  deviceIcon: DeviceIcon,
  disabled,
  discovery,
  providers = accessDeviceProviders,
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
  disabled: boolean;
  discovery: AccessDeviceDiscovery;
  providers?: readonly AccessDeviceProviderDescriptor[];
  scheduleLabel: string;
  schedules: Schedule[];
  saving: boolean;
  onDelete: () => void;
  onSave: () => void;
  onUpdate: (patch: Partial<AccessDevice>) => void;
  onUpdateBinding: (provider: string, externalId: string) => void;
}) {
  const mapped = device.bindings.some((binding) => Boolean(binding.external_id));
  return (
    <article className={device.enabled ? "access-device-editor" : "access-device-editor disabled"}>
      <header className="access-device-editor-head">
        <div className="access-device-title-row">
          <span className="access-device-symbol"><DeviceIcon size={18} /></span>
          <div>
            <h3>{device.name || "Unnamed device"}</h3>
            <details className="access-device-technical"><summary>Technical identifier</summary><span>{device.key || "No internal key set"}</span></details>
          </div>
        </div>
        <div className="access-device-badges">
          <Badge tone={device.enabled ? "green" : "gray"}>{device.enabled ? "Enabled" : "Disabled"}</Badge>
          <Badge tone="gray">{scheduleLabel}</Badge>
          {device.kind === "gate" ? <Badge tone={device.open_for_access ? "blue" : "gray"}>{device.open_for_access ? "Access opens" : "Manual only"}</Badge> : null}
          <Badge tone={mapped ? "blue" : "amber"}>{mapped ? "Mapped" : "Needs mapping"}</Badge>
        </div>
      </header>
      <div className="access-device-editor-body">
        <div className="access-device-block identity">
          <div className="access-device-block-title">
            <Key size={15} />
            <h4>Identity</h4>
          </div>
          <div className="settings-form-grid access-device-fields">
            <SettingField disabled={disabled || saving} field={{ key: "name", label: "Display name" }} value={device.name} onChange={(value) => onUpdate({ name: value })} />
            <details className="access-device-technical"><summary>Edit technical identifier</summary><SettingField disabled={disabled || saving} field={{ key: "key", label: "Internal key" }} value={device.key} onChange={(value) => onUpdate({ key: value })} /></details>
          </div>
        </div>

        <div className="access-device-block policy">
          <div className="access-device-block-title">
            <CalendarDays size={15} />
            <h4>Policy</h4>
          </div>
          <div className="access-device-policy-grid">
            <AccessDeviceSwitch checked={device.enabled} disabled={disabled} label="Device enabled" onChange={(checked) => onUpdate({ enabled: checked })} />
            {device.kind === "gate" ? (
              <AccessDeviceSwitch checked={device.open_for_access} disabled={disabled} label="Open for access events" onChange={(checked) => onUpdate({ open_for_access: checked })} />
            ) : null}
            <label className="field">
              <span>Schedule</span>
              <select disabled={disabled} value={device.schedule_id ?? ""} onChange={(event) => onUpdate({ schedule_id: event.target.value || null })}>
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
            {providers.map((provider) => {
              const binding = device.bindings.find((item) => item.provider === provider.key);
              const state = discovery[provider.key] ?? { options: [], error: "" };
              return <ProviderBindingField key={provider.key} disabled={disabled} label={provider.bindingLabel} icon={provider.icon} options={state.options} error={state.error} value={provider.selection(binding, state.options)} onChange={(value) => onUpdateBinding(provider.key, value)} />;
            })}
          </div>
        </div>
      </div>
      <div className="access-device-actions">
        <button className="secondary-button danger" disabled={disabled} onClick={onDelete} type="button"><Trash2 size={15} /> Remove</button>
        <button className="primary-button" disabled={disabled || saving} onClick={onSave} type="button">{saving ? "Saving..." : "Save Device"}</button>
      </div>
    </article>
  );
}

function AccessDeviceSwitch({ checked, disabled, label, onChange }: { checked: boolean; disabled?: boolean; label: string; onChange: (checked: boolean) => void }) {
  return (
    <label className="access-device-switch">
      <input checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
      <span aria-hidden="true" />
      <strong>{label}</strong>
    </label>
  );
}

function ProviderBindingField({ disabled, label, options, icon: Icon, error, value, onChange }: { disabled?: boolean; label: string; options: AccessDeviceDiscoveryItem[]; icon: React.ElementType; error: string; value: string; onChange: (value: string) => void }) {
  const listId = React.useId();
  const mapped = Boolean(value.trim());
  return (
    <label className={mapped ? "field access-binding-field mapped" : "field access-binding-field"}>
      <span className="field-label-row">
        <span>{label}</span>
        <span className="access-binding-state">{mapped ? "Mapped" : "Not mapped"}</span>
      </span>
      <span className="field-control access-binding-control">
        <Icon size={16} />
        <input disabled={disabled} list={listId} value={value} onChange={(event) => onChange(event.target.value)} placeholder="Select or enter an external ID" />
      </span>
      <datalist id={listId}>
        {options.map((option) => <option key={option.entity_id} value={option.entity_id}>{option.name || option.entity_id}</option>)}
      </datalist>
      {error ? <small className="field-hint" role="alert">{error}</small> : null}
      <small className="field-hint">{options.length ? `${options.length} discovered ${options.length === 1 ? "cover" : "covers"}` : "No discovered covers for this provider."}</small>
    </label>
  );
}
