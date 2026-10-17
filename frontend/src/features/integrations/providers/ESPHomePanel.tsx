import { settingsActivationWarning } from "../../../lib/settings";
import { Activity, Clock3, Key, Loader2, PlugZap, Plus, RefreshCw, SlidersHorizontal, Trash2, Zap } from "lucide-react";
import React from "react";
import { formatDate } from "../../../lib/format";
import { useCoordinatedRead } from "../../../lib/useCoordinatedRead";
import { Badge, type BadgeTone } from "../../../ui/primitives";
import { EditorDialog } from "../../../ui/EditorDialog";
import { useEditorLifetime } from "../../../ui/useEditorLifetime";
import { type LeaveDecision, useDiscardConfirmation, whenLeaveAllowed } from "../../../ui/useEditorDismiss";
import { useConfirm } from "../../../ui/useConfirm";
import type { AccessDeviceStreamDeviceStatus, IntegrationStatus } from "../../../api/types";
import { addESPHomeDevice, type ESPHomeDeviceSummary, integrationsApi, isMutationNotStarted, removeESPHomeDevice, testESPHomeDevice, testIntegrationSettings } from "../../../api/integrations";
import type { ProviderEditorProps } from "../editorTypes";
import type { IntegrationFeedback } from "../catalog";
import { IntegrationFeedbackPanel } from "../components";
import { integrationEventMatches } from "../invalidation";

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

const emptyForm = { name: "", host: "", port: "6053", encryption_key: "", timeout_seconds: "30" };
export function ESPHomeEditor(props: ProviderEditorProps) {
  const lifetime = useEditorLifetime();
  const canManage = props.currentUser.role === "admin";
  const [devices, setDevices] = React.useState<ESPHomeDeviceSummary[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [readError, setReadError] = React.useState("");
  const [streamError, setStreamError] = React.useState("");
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const [adding, setAdding] = React.useState(false);
  const [form, setForm] = React.useState(emptyForm);
  const [operation, setOperation] = React.useState<"add" | "remove" | "test" | "verify" | "connection" | null>(null);
  const [testingId, setTestingId] = React.useState("");
  const [verifyingStreamDeviceId, setVerifyingStreamDeviceId] = React.useState("");
  const [currentAccessStatus, setCurrentAccessStatus] = React.useState<IntegrationStatus | null>(null);
  const statusRef = React.useRef<IntegrationStatus | null>(null);
  const [statusMessage, setStatusMessage] = React.useState("");
  const pending = React.useRef(false);
  const busy = operation !== null;
  const submitting = operation === "add";
  const dirty = JSON.stringify(form) !== JSON.stringify(emptyForm);
  const askConfirm = useConfirm();
  const confirmDiscard = useDiscardConfirmation();
  const removePrompt = React.useRef(false);
  const discardDraft = (): LeaveDecision => pending.current ? false : !dirty || confirmDiscard("ESPHome device");
  const resetForm = () => setForm(emptyForm);
  const updateForm = (key: keyof typeof form, value: string) => { if (!pending.current) setForm((current) => ({ ...current, [key]: value })); };
  const read = React.useCallback(async (signal: AbortSignal) => {
    setLoading(true); setReadError("");
    try { const next = await integrationsApi.getESPHomeDevices({ signal }); if (!signal.aborted) setDevices(next); }
    catch (error) { if (!signal.aborted) setReadError(error instanceof Error ? error.message : "Unable to load ESPHome devices."); throw error; }
    finally { if (!signal.aborted) setLoading(false); }
  }, []);
  const reload = useCoordinatedRead(read, integrationEventMatches);
  const readStream = React.useCallback(async (signal: AbortSignal) => {
    setStreamError("");
    try { const next = await integrationsApi.getAccessDeviceStatus({ signal }); if (!signal.aborted) { statusRef.current = next; setCurrentAccessStatus(next); } }
    catch (error) { if (!signal.aborted) setStreamError(error instanceof Error ? error.message : "Unable to verify ESPHome stream status."); throw error; }
  }, []);
  const reloadStream = useCoordinatedRead(readStream, integrationEventMatches);
  const streamDeviceRefreshKey = devices.map((device) => `${device.id}:${device.enabled ? "1" : "0"}`).join("|");
  React.useEffect(() => {
    if (!devices.some((device) => device.enabled)) return;
    const timer = window.setTimeout(() => { void reloadStream().catch(() => undefined); }, 750);
    return () => window.clearTimeout(timer);
  }, [reloadStream, streamDeviceRefreshKey]);
  const streamStatus = currentAccessStatus?.state_stream_status?.esphome ?? null;
  const streamDevices = streamStatus?.devices ?? [];
  const liveStreamCount = devices.filter((device) => Boolean(streamStatus?.running && streamStatusForDevice(device, streamDevices)?.connected)).length;
  const enabledDeviceCount = devices.filter((device) => device.enabled).length;
  const streamSummary = enabledDeviceCount ? `${liveStreamCount} of ${enabledDeviceCount} ESPHome device${enabledDeviceCount === 1 ? "" : "s"} streaming live.` : "Add an ESPHome device to enable live native API streaming.";
  const refreshAccepted = () => { void reload().catch(() => undefined); void reloadStream().catch(() => undefined); void props.onChanged().catch(() => undefined); };
  const begin = (next: NonNullable<typeof operation>) => {
    if (!canManage || pending.current || loading || readError || !lifetime.isCurrent()) return false;
    pending.current = true; setOperation(next); setStatusMessage(""); setFeedback(null); return true;
  };
  const finish = () => { pending.current = false; if (lifetime.isCurrent()) { setOperation(null); setTestingId(""); setVerifyingStreamDeviceId(""); } };
  const fail = (error: unknown, detail: string) => { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "ESPHome update failed", detail: error instanceof Error ? error.message : detail }); };
  const addDevice = async () => {
    if (!form.name.trim() || !form.host.trim() || !begin("add")) return;
    try {
      const result = await addESPHomeDevice({ name: form.name.trim(), host: form.host.trim(), port: Number(form.port || 6053), encryption_key: form.encryption_key, timeout_seconds: Number(form.timeout_seconds || 30), enabled: true }, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      setDevices(result.devices); resetForm(); setAdding(false); setStatusMessage(settingsActivationWarning(result) || "ESPHome device added. Waiting for native stream..."); refreshAccepted();
    } catch (error) { fail(error, "Unable to add ESPHome device."); } finally { finish(); }
  };
  const removeDevice = async (device: ESPHomeDeviceSummary) => {
    if (pending.current || !canManage || !lifetime.isCurrent() || removePrompt.current) return;
    removePrompt.current = true;
    let confirmed = false;
    try { confirmed = await askConfirm({ title: `Remove ESPHome device ${device.name}?`, message: "This ESPHome device will be removed from IACS.", confirmLabel: "Remove", tone: "danger" }); }
    finally { removePrompt.current = false; }
    // begin() re-checks the pending fence and editor lifetime after the asynchronous prompt.
    if (!confirmed || !begin("remove")) return;
    try {
      const result = await removeESPHomeDevice(device, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      setDevices(result.devices); setStatusMessage(settingsActivationWarning(result) || "ESPHome device removed."); refreshAccepted();
    } catch (error) { fail(error, "Unable to remove ESPHome device."); } finally { finish(); }
  };
  const testDevice = async (device: ESPHomeDeviceSummary) => {
    if (!begin("test")) return;
    setTestingId(device.id);
    try {
      const result = await testESPHomeDevice(device, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      setStatusMessage(`${device.name} live stream verified. ${result.cover_count} cover${result.cover_count === 1 ? "" : "s"} available.`);
      void reloadStream().catch(() => undefined);
    } catch (error) { fail(error, `Unable to verify the live stream for ${device.name}.`); } finally { finish(); }
  };
  const verifyStream = async (device: ESPHomeDeviceSummary) => {
    if (!begin("verify")) return;
    setVerifyingStreamDeviceId(device.id);
    try {
      await reloadStream(); if (!lifetime.isCurrent()) return;
      const nextStream = statusRef.current?.state_stream_status?.esphome;
      const next = streamStatusForDevice(device, nextStream?.devices ?? []);
      setStatusMessage(nextStream?.running && next?.connected ? `${device.name} native stream is live.` : next?.last_error || nextStream?.last_error || `${device.name} is using polling mode; native stream is not confirmed live.`);
    } catch { /* The stream read owner reports the error separately. */ } finally { finish(); }
  };
  const testConnection = async () => {
    if (!begin("connection")) return;
    setFeedback({ tone: "progress", title: "Testing connection", detail: "Contacting ESPHome.", activeStep: 1 });
    try {
      const result = await testIntegrationSettings({ integration: props.definition.key, values: {} }, props.definition.title, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      if (!result.ok) throw new Error(result.message);
      setFeedback({ tone: "success", title: "Connection verified", detail: result.message });
    } catch (error) { fail(error, "Connection test failed."); } finally { finish(); }
  };
  return <EditorDialog title={props.definition.title} description={props.definition.description} dirty={dirty} busy={busy} onClose={props.onClose} onGuardChange={props.onGuardChange}>
    {({ requestClose }) => <>
    {readError ? <div className="auth-error inline-error">{readError}<button className="secondary-button" onClick={() => void reload().catch(() => undefined)} type="button">Try again</button></div> : null}
    {streamError ? <div className="auth-error inline-error">{streamError}</div> : null}
    <div className="apprise-manager esphome-manager">
      <div className="apprise-manager-header">
        <div>
          <strong>ESPHome Devices</strong>
          <span>Add one native API controller per gate or garage-door device. Cover mappings live under Settings Gates and Garage Doors.</span>
        </div>
        <div className="esphome-header-actions">
          <button className="primary-button" disabled={!canManage || busy || loading || Boolean(readError)} onClick={() => { const toggle = () => { if (pending.current) return; resetForm(); setAdding((current) => !current); }; if (!adding) toggle(); else whenLeaveAllowed(discardDraft(), toggle); }} type="button">
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
                <input disabled={busy} autoFocus value={form.name} onChange={(event) => updateForm("name", event.target.value)} placeholder="Top Gate" />
              </div>
            </label>
            <label className="field">
              <span>Host or IP</span>
              <div className="field-control">
                <PlugZap size={16} />
                <input disabled={busy} value={form.host} onChange={(event) => updateForm("host", event.target.value)} placeholder="10.0.107.22" />
              </div>
            </label>
            <label className="field">
              <span>Native API port</span>
              <div className="field-control">
                <SlidersHorizontal size={16} />
                <input disabled={busy} min={1} max={65535} step={1} type="number" value={form.port} onChange={(event) => updateForm("port", event.target.value)} />
              </div>
            </label>
            <label className="field">
              <span>Timeout seconds</span>
              <div className="field-control">
                <Clock3 size={16} />
                <input disabled={busy} min={5} step={1} type="number" value={form.timeout_seconds} onChange={(event) => updateForm("timeout_seconds", event.target.value)} />
              </div>
            </label>
            <label className="field">
              <span>Encryption key</span>
              <div className="field-control">
                <Key size={16} />
                <input disabled={busy} type="password" value={form.encryption_key} onChange={(event) => updateForm("encryption_key", event.target.value)} placeholder="Blank if encryption is disabled" />
              </div>
            </label>
          </div>
          <div className="apprise-add-actions">
            <button className="secondary-button" disabled={busy} onClick={() => whenLeaveAllowed(discardDraft(), () => { if (pending.current) return; resetForm(); setAdding(false); })} type="button">Cancel</button>
            <button className="primary-button" disabled={!canManage || busy || loading || Boolean(readError) || !form.name.trim() || !form.host.trim()} onClick={addDevice} type="button">
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
                    disabled={!canManage || busy}
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
                  <button className="secondary-button" onClick={() => testDevice(device)} disabled={!canManage || busy || loading || Boolean(readError)} type="button">
                    {testingId === device.id ? <Loader2 className="spin" size={14} /> : <Activity size={14} />}
                    {testingId === device.id ? "Testing" : "Test"}
                  </button>
                  <button className="icon-button danger" onClick={() => removeDevice(device)} disabled={!canManage || busy || loading || Boolean(readError)} type="button" aria-label={`Remove ${device.name}`}>
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
    {feedback ? <IntegrationFeedbackPanel feedback={feedback} /> : null}
    <div className="modal-actions"><button className="secondary-button" disabled={!canManage || busy || loading || Boolean(readError)} onClick={() => void testConnection()} type="button">{operation === "connection" ? "Testing..." : "Test Connection"}</button><button className="primary-button" onClick={requestClose} type="button">Done</button></div>
    </>}
  </EditorDialog>;
}
