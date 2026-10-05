import { Activity, Clock3, Key, Loader2, PlugZap, Plus, RefreshCw, Settings, SlidersHorizontal, Trash2, Zap } from "lucide-react";
import React from "react";
import { formatDate } from "../../../lib/format";
import { Badge } from "../../../ui/primitives";
import type { AccessDeviceStreamDeviceStatus, IntegrationStatus } from "../../../api/types";
import type { BadgeTone } from "../../../ui/primitives";
import { addESPHomeDevice, ESPHomeDeviceSummary, integrationsApi, removeESPHomeDevice, testESPHomeDevice } from "../../../api/integrations";

function streamStatusForDevice(
  device: ESPHomeDeviceSummary,
  streamDevices: AccessDeviceStreamDeviceStatus[]
): AccessDeviceStreamDeviceStatus | null {
  const deviceId = device.id.trim().toLowerCase();
  return streamDevices.find((streamDevice) => {
    const streamDeviceId = String(streamDevice.device_id || "").trim().toLowerCase();
    return Boolean(deviceId && streamDeviceId === deviceId);
  }) ?? null;
}

export function ESPHomeSettingsFields({
  accessStatus,
  canManage,
  loading,
  devices,
  onAccessStatusChanged,
  onChanged,
  onError
}: {
  accessStatus: IntegrationStatus | null;
  canManage: boolean;
  loading: boolean;
  devices: ESPHomeDeviceSummary[];
  onAccessStatusChanged?: (status: IntegrationStatus) => void;
  onChanged: (devices: ESPHomeDeviceSummary[]) => Promise<void>;
  onError: (error: string) => void;
}) {
  const [adding, setAdding] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [testingId, setTestingId] = React.useState("");
  const [verifyingStreamDeviceId, setVerifyingStreamDeviceId] = React.useState("");
  const [currentAccessStatus, setCurrentAccessStatus] = React.useState<IntegrationStatus | null>(accessStatus);
  const [statusMessage, setStatusMessage] = React.useState("");
  const onAccessStatusChangedRef = React.useRef(onAccessStatusChanged);
  const onErrorRef = React.useRef(onError);
  const [form, setForm] = React.useState({
    name: "",
    host: "",
    port: "6053",
    encryption_key: "",
    timeout_seconds: "30"
  });
  React.useEffect(() => {
    onAccessStatusChangedRef.current = onAccessStatusChanged;
  }, [onAccessStatusChanged]);
  React.useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);
  React.useEffect(() => {
    setCurrentAccessStatus(accessStatus);
  }, [accessStatus]);
  const streamStatus = currentAccessStatus?.state_stream_status?.esphome ?? null;
  const streamDevices = streamStatus?.devices ?? [];
  const liveStreamCount = devices.filter((device) => {
    const deviceStatus = streamStatusForDevice(device, streamDevices);
    return Boolean(streamStatus?.running && deviceStatus?.connected);
  }).length;
  const enabledDeviceCount = devices.filter((device) => device.enabled).length;
  const streamSummary = enabledDeviceCount
    ? `${liveStreamCount} of ${enabledDeviceCount} ESPHome device${enabledDeviceCount === 1 ? "" : "s"} streaming live.`
    : "Add an ESPHome device to enable live native API streaming.";
  const updateForm = (key: keyof typeof form, value: string) => setForm((current) => ({ ...current, [key]: value }));
  const resetForm = () => {
    setForm({
      name: "",
      host: "",
      port: "6053",
      encryption_key: "",
      timeout_seconds: "30"
    });
  };
  const refreshStreamStatus = React.useCallback(async (reportErrors = false): Promise<IntegrationStatus | null> => {
    try {
      const nextStatus = await integrationsApi.getAccessDeviceStatus();
      setCurrentAccessStatus(nextStatus);
      onAccessStatusChangedRef.current?.(nextStatus);
      return nextStatus;
    } catch (error) {
      if (reportErrors) {
        onErrorRef.current(error instanceof Error ? error.message : "Unable to verify ESPHome stream status.");
      }
      return null;
    }
  }, []);
  const streamDeviceRefreshKey = devices.map((device) => `${device.id}:${device.enabled ? "1" : "0"}`).join("|");
  React.useEffect(() => {
    if (!devices.some((device) => device.enabled)) return;
    let cancelled = false;
    const refresh = () => {
      if (cancelled) return;
      refreshStreamStatus(false).catch(() => undefined);
    };
    const firstRefresh = window.setTimeout(refresh, 750);
    return () => {
      cancelled = true;
      window.clearTimeout(firstRefresh);
    };
  }, [refreshStreamStatus, streamDeviceRefreshKey]);
  const verifyStream = async (device: ESPHomeDeviceSummary) => {
    setVerifyingStreamDeviceId(device.id);
    setStatusMessage("");
    try {
      const nextStatus = await refreshStreamStatus(true);
      if (!nextStatus) return;
      const nextStream = nextStatus.state_stream_status?.esphome;
      const nextDeviceStatus = streamStatusForDevice(device, nextStream?.devices ?? []);
      if (nextStream?.running && nextDeviceStatus?.connected) {
        setStatusMessage(`${device.name} native stream is live.`);
      } else {
        setStatusMessage(
          nextDeviceStatus?.last_error
            || nextStream?.last_error
            || `${device.name} is using polling mode; native stream is not confirmed live.`
        );
      }
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to verify ESPHome stream status.");
    } finally {
      setVerifyingStreamDeviceId("");
    }
  };
  const addDevice = async () => {
    if (!canManage) {
      onError("Administrator access is required to manage ESPHome devices.");
      return;
    }
    setSubmitting(true);
    setStatusMessage("");
    try {
      const payload = {
        name: form.name.trim(),
        host: form.host.trim(),
        port: Number(form.port || 6053),
        encryption_key: form.encryption_key,
        timeout_seconds: Number(form.timeout_seconds || 30),
        enabled: true
      };
      const devices = await addESPHomeDevice(payload);
      resetForm();
      setAdding(false);
      await onChanged(devices);
      setStatusMessage("ESPHome device added. Waiting for native stream...");
      refreshStreamStatus(false).catch(() => undefined);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to add ESPHome device.");
    } finally {
      setSubmitting(false);
    }
  };
  const removeDevice = async (device: ESPHomeDeviceSummary) => {
    if (!canManage) {
      onError("Administrator access is required to manage ESPHome devices.");
      return;
    }
    if (!window.confirm(`Remove ESPHome device ${device.name}?`)) return;
    setSubmitting(true);
    setStatusMessage("");
    try {
      await onChanged(await removeESPHomeDevice(device));
      setStatusMessage("ESPHome device removed.");
      refreshStreamStatus(false).catch(() => undefined);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to remove ESPHome device.");
    } finally {
      setSubmitting(false);
    }
  };
  const testDevice = async (device: ESPHomeDeviceSummary) => {
    if (!canManage) {
      onError("Administrator access is required to test ESPHome devices.");
      return;
    }
    setTestingId(device.id);
    setStatusMessage("");
    try {
      const result = await testESPHomeDevice(device);
      setStatusMessage(`${device.name} live stream verified. ${result.cover_count} cover${result.cover_count === 1 ? "" : "s"} available.`);
      refreshStreamStatus(false).catch(() => undefined);
    } catch (error) {
      onError(error instanceof Error ? error.message : `Unable to verify the live stream for ${device.name}.`);
    } finally {
      setTestingId("");
    }
  };
  return (
    <div className="apprise-manager esphome-manager">
      <div className="apprise-manager-header">
        <div>
          <strong>ESPHome Devices</strong>
          <span>Add one native API controller per gate or garage-door device. Cover mappings live under Settings Gates and Garage Doors.</span>
        </div>
        <div className="esphome-header-actions">
          <button className="primary-button" disabled={!canManage} onClick={() => setAdding((current) => !current)} type="button">
            <Plus size={15} /> Add New ESPHome Device
          </button>
        </div>
      </div>
      <div className={liveStreamCount && liveStreamCount === enabledDeviceCount ? "esphome-stream-note live" : "esphome-stream-note"}>
        {streamSummary}
      </div>
      {adding ? (
        <div className="apprise-add-row esphome-add-row">
          <div className="settings-form-grid">
            <label className="field">
              <span>Name</span>
              <div className="field-control">
                <Zap size={16} />
                <input autoFocus value={form.name} onChange={(event) => updateForm("name", event.target.value)} placeholder="Top Gate" />
              </div>
            </label>
            <label className="field">
              <span>Host or IP</span>
              <div className="field-control">
                <PlugZap size={16} />
                <input value={form.host} onChange={(event) => updateForm("host", event.target.value)} placeholder="10.0.107.22" />
              </div>
            </label>
            <label className="field">
              <span>Native API port</span>
              <div className="field-control">
                <SlidersHorizontal size={16} />
                <input min={1} max={65535} step={1} type="number" value={form.port} onChange={(event) => updateForm("port", event.target.value)} />
              </div>
            </label>
            <label className="field">
              <span>Timeout seconds</span>
              <div className="field-control">
                <Clock3 size={16} />
                <input min={5} step={1} type="number" value={form.timeout_seconds} onChange={(event) => updateForm("timeout_seconds", event.target.value)} />
              </div>
            </label>
            <label className="field">
              <span>Encryption key</span>
              <div className="field-control">
                <Key size={16} />
                <input type="password" value={form.encryption_key} onChange={(event) => updateForm("encryption_key", event.target.value)} placeholder="Blank if encryption is disabled" />
              </div>
            </label>
          </div>
          <div className="apprise-add-actions">
            <button className="secondary-button" onClick={() => setAdding(false)} type="button">Cancel</button>
            <button className="primary-button" disabled={!canManage || submitting || !form.name.trim() || !form.host.trim()} onClick={addDevice} type="button">
              {submitting ? "Adding..." : "Add Device"}
            </button>
          </div>
        </div>
      ) : null}
      {statusMessage ? <div className="success-note">{statusMessage}</div> : null}
      <div className="apprise-url-table esphome-device-table">
        <div className="apprise-url-head esphome-device-head">
          <span>Device</span>
          <span>Connection</span>
          <span>Stream</span>
          <span>Secrets</span>
          <span />
        </div>
        {loading ? (
          <div className="apprise-empty">Loading ESPHome devices</div>
        ) : devices.length ? (
          devices.map((device) => {
            const deviceStreamStatus = streamStatusForDevice(device, streamDevices);
            const deviceStreamLive = Boolean(streamStatus?.running && deviceStreamStatus?.connected);
            const deviceStreamChecking = verifyingStreamDeviceId === device.id;
            const deviceStreamLabel = deviceStreamLive ? "Live" : device.enabled ? "Polling" : "Disabled";
            const deviceStreamTone: BadgeTone = deviceStreamLive ? "green" : device.enabled ? "amber" : "gray";
            const deviceStreamUpdatedAt = deviceStreamStatus?.updated_at ? formatDate(deviceStreamStatus.updated_at) : "";
            const deviceStreamDetail = deviceStreamLive
              ? `${device.name} native state stream is connected${deviceStreamUpdatedAt ? `; checked ${deviceStreamUpdatedAt}` : ""}.`
              : device.enabled
              ? deviceStreamStatus?.last_error || streamStatus?.last_error || `${device.name} native stream is not confirmed; status is using polling mode.`
              : `${device.name} is disabled.`;
            return (
              <div className="apprise-url-row esphome-device-row" key={device.id}>
                <div>
                  <strong>{device.name}</strong>
                  <span>{device.id}</span>
                </div>
                <div>
                  <strong>{device.host}:{device.port}</strong>
                  <span>{device.enabled ? `Timeout ${device.timeout_seconds}s` : "Disabled"}</span>
                </div>
                <div className="esphome-device-stream">
                  <button
                    className={deviceStreamLive ? "esphome-stream-pill live" : device.enabled ? "esphome-stream-pill polling" : "esphome-stream-pill"}
                    disabled={!canManage || deviceStreamChecking}
                    onClick={() => verifyStream(device)}
                    title={deviceStreamDetail}
                    type="button"
                  >
                    {deviceStreamChecking ? <Loader2 className="spin" size={14} /> : deviceStreamLive ? <Activity size={14} /> : <RefreshCw size={14} />}
                    <Badge tone={deviceStreamTone}>{deviceStreamChecking ? "Checking" : deviceStreamLabel}</Badge>
                  </button>
                  <span>{deviceStreamDetail}</span>
                </div>
                <div>
                  <Badge tone={device.encryption_key_configured ? "green" : "gray"}>{device.encryption_key_configured ? "Key saved" : "No key"}</Badge>
                </div>
                <div className="esphome-device-actions">
                  <button className="secondary-button" onClick={() => testDevice(device)} disabled={!canManage || Boolean(testingId) || submitting} type="button">
                    {testingId === device.id ? <Loader2 className="spin" size={14} /> : <Activity size={14} />}
                    {testingId === device.id ? "Testing" : "Test"}
                  </button>
                  <button className="icon-button danger" onClick={() => removeDevice(device)} disabled={!canManage || submitting || testingId === device.id} type="button" aria-label={`Remove ${device.name}`}>
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            );
          })
        ) : (
          <div className="apprise-empty">No ESPHome devices configured</div>
        )}
      </div>
    </div>
  );
}
