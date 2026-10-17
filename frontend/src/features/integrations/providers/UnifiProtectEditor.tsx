import React from "react";
import { Activity, Copy, Key, Settings } from "lucide-react";
import { integrationsApi, type UnifiProtectStatus } from "../../../api/integrations";
import type { UnifiProtectCamera } from "../../../api/types";
import { secretSettingKeys } from "../../../lib/settings";
import { useCoordinatedRead } from "../../../lib/useCoordinatedRead";
import { EditorDialog } from "../../../ui/EditorDialog";
import type { ProtectIntegrationTab } from "../catalog";
import { IntegrationFeedbackPanel, ProviderSettingsGrid } from "../components";
import type { ProviderEditorProps } from "../editorTypes";
import { integrationEventMatches } from "../invalidation";
import { UnifiProtectExposesPanel } from "../unifiProtect";
import { useIntegrationSettingsEditor } from "../useIntegrationSettingsEditor";

export function UnifiProtectEditor(props: ProviderEditorProps) {
  const editor = useIntegrationSettingsEditor(props);
  const [tab, setTab] = React.useState<ProtectIntegrationTab>(props.initialTab);
  const [status, setStatus] = React.useState<UnifiProtectStatus | null>(null);
  const [cameras, setCameras] = React.useState<UnifiProtectCamera[]>([]);
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [manualCopy, setManualCopy] = React.useState(false);
  const read = React.useCallback(async (signal: AbortSignal) => {
    setLoading(true); setError("");
    try {
      const next = await integrationsApi.getProtectStatus({ signal });
      if (signal.aborted) return;
      setStatus(next);
      if (next.configured && tab === "exposes") {
        const items = await integrationsApi.getProtectCameras(true, { signal });
        if (!signal.aborted) setCameras(items);
      } else if (!next.configured) setCameras([]);
    } catch (error) { if (!signal.aborted) setError(error instanceof Error ? error.message : "UniFi Protect metadata unavailable."); throw error; }
    finally { if (!signal.aborted) setLoading(false); }
  }, [tab]);
  const reload = useCoordinatedRead(read, integrationEventMatches);
  const generate = () => {
    if (editor.busy || !editor.canManage) return;
    if (!window.crypto?.getRandomValues) { editor.setFeedback({ tone: "error", title: "Token generation unavailable", detail: "This browser does not expose secure random generation." }); return; }
    const bytes = new Uint8Array(32); window.crypto.getRandomValues(bytes);
    editor.update("lpr_webhook_token", Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(""));
    setManualCopy(false);
    editor.setFeedback({ tone: "success", title: "Token generated", detail: "Copy this token into UniFi Protect before saving or closing this modal." });
  };
  const copy = async () => {
    const token = editor.form.lpr_webhook_token;
    if (!token || editor.busy || !editor.canManage) return;
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(token);
      if (editor.lifetime.isCurrent()) { setManualCopy(false); editor.setFeedback({ tone: "success", title: "Token copied", detail: "Paste it into UniFi Protect as the X-IACS-LPR-Token header value." }); }
    } catch {
      // Never log the token; show it in a read-only field the user can select and copy.
      if (editor.lifetime.isCurrent()) { setManualCopy(true); editor.setFeedback({ tone: "error", title: "Copy unavailable", detail: "Select the token below and copy it manually." }); }
    }
  };
  const tabs = <div className="integration-modal-tabs" role="tablist" aria-label={`${props.definition.title} settings sections`}>
    <button aria-selected={tab === "general"} className={tab === "general" ? "integration-modal-tab active" : "integration-modal-tab"} onClick={() => setTab("general")} role="tab" type="button"><Settings size={15} /> General</button>
    <button aria-selected={tab === "exposes"} className={tab === "exposes" ? "integration-modal-tab active" : "integration-modal-tab"} onClick={() => setTab("exposes")} role="tab" type="button"><Activity size={15} /> Exposes</button>
  </div>;
  return <EditorDialog title={props.definition.title} description={props.loading ? "Loading settings..." : props.definition.description} dirty={editor.dirty} busy={editor.busy} onClose={props.onClose} onGuardChange={props.onGuardChange} tabs={tabs}>
    {({ closeAccepted }) => <>
      {tab === "exposes" ? <UnifiProtectExposesPanel cameras={cameras} error={error || status?.last_error || status?.realtime_error || ""} loading={loading} onRefresh={() => reload().catch(() => undefined)} status={status} /> : <form className="integration-settings-form" onSubmit={async (event) => { event.preventDefault(); if (await editor.save()) void closeAccepted(); }}>
        <ProviderSettingsGrid fields={props.definition.fields} disabled={!editor.canManage || editor.busy || props.loading} form={editor.form} isConfiguredSecret={(key) => secretSettingKeys.has(key) && Boolean(props.values[key])} onChange={editor.update} revealPasswordValue={(field) => field.key === "lpr_webhook_token" && Boolean(editor.form.lpr_webhook_token)} fieldAction={(field) => field.key === "lpr_webhook_token" ? <>
          {editor.form.lpr_webhook_token ? <button className="secondary-button compact" disabled={!editor.canManage || editor.busy} onClick={() => void copy()} type="button"><Copy size={14} /> Copy</button> : null}
          <button className="secondary-button compact" disabled={!editor.canManage || editor.busy} onClick={generate} type="button"><Key size={14} /> Generate</button>
        </> : undefined} />
        {manualCopy && editor.form.lpr_webhook_token ? <label className="field">
          <span>LPR webhook token</span>
          <input aria-label="LPR webhook token (copy manually)" autoFocus onFocus={(event) => event.currentTarget.select()} readOnly spellCheck={false} type="text" value={editor.form.lpr_webhook_token} />
        </label> : null}
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        {editor.feedback ? <IntegrationFeedbackPanel feedback={editor.feedback} /> : null}
        <div className="modal-actions"><button className="secondary-button" disabled={!editor.canManage || editor.busy || props.loading} onClick={editor.test} type="button">{editor.operation === "test" ? "Testing..." : "Test Connection"}</button><button className="primary-button" disabled={!editor.canManage || editor.busy || props.loading} type="submit">{editor.operation === "save" ? "Saving..." : "Save"}</button></div>
      </form>}
    </>}
  </EditorDialog>;
}
