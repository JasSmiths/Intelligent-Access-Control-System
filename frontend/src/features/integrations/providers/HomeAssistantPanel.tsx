import React from "react";
import { integrationsApi } from "../../../api/integrations";
import { useCoordinatedRead } from "../../../lib/useCoordinatedRead";
import { EditorDialog } from "../../../ui/EditorDialog";
import { IntegrationFeedbackPanel } from "../components";
import type { ProviderEditorProps } from "../editorTypes";
import { integrationEventMatches } from "../invalidation";
import { useIntegrationSettingsEditor } from "../useIntegrationSettingsEditor";
import { RefreshCcw } from "lucide-react";
import { SettingField } from "../../../lib/settings";
import { Badge } from "../../../ui/primitives";
import type { HomeAssistantDiscovery, HomeAssistantEntity, IntegrationStatus } from "../../../api/types";
import type { BadgeTone } from "../../../ui/primitives";
import { formatDate } from "../../../lib/format";

export function HomeAssistantSettingsFields({
  disabled = false,
  discovery,
  discoveryError,
  discoveryLoading,
  form,
  onChange,
  onReload,
  status
}: {
  disabled?: boolean;
  discovery: HomeAssistantDiscovery | null;
  discoveryError: string;
  discoveryLoading: boolean;
  form: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onReload: () => Promise<void>;
  status: IntegrationStatus | null;
}) {
  const configured = Boolean(status?.configured || form.home_assistant_url || form.home_assistant_token);
  const connected = status?.connected === true;
  const degraded = Boolean(configured && (status?.degraded || status?.connected === false || status?.last_error));
  const connectionLabel = connected ? "Connected" : degraded ? "Degraded" : configured ? "Configured" : "Not Configured";
  const connectionTone: BadgeTone = connected ? "green" : degraded ? "red" : configured ? "blue" : "gray";
  const connectionDetail = status?.last_error
    || (status?.state_refreshed_at ? `State refreshed ${formatDate(status.state_refreshed_at)}` : configured ? "Credentials are saved, but live state has not been verified yet." : "Save credentials to enable Home Assistant state sync.");
  return (
    <div className="ha-config-shell">
      {discoveryError ? <div className="auth-error inline-error">{discoveryError}</div> : null}
      <div className="ha-tab-panel" role="tabpanel">
          <section className="ha-setup-panel">
            <div className="ha-section-heading">
              <div>
                <strong>Connection</strong>
                <span>{discovery ? "Entities loaded from Home Assistant" : "Save credentials, then refresh discovery"}</span>
              </div>
              <button className="secondary-button ha-refresh-button" onClick={onReload} disabled={disabled || discoveryLoading} type="button">
                <RefreshCcw size={15} /> {discoveryLoading ? "Refreshing..." : "Refresh"}
              </button>
            </div>
            <div className={`ha-health-strip ${connectionTone}`}>
              <Badge tone={connectionTone}>{connectionLabel}</Badge>
              <span>{connectionDetail}</span>
            </div>
            <div className="ha-setup-grid">
              <SettingField
                disabled={disabled}
                field={{ key: "home_assistant_url", label: "URL" }}
                value={form.home_assistant_url ?? ""}
                onChange={(value) => onChange("home_assistant_url", value)}
              />
              <SettingField
                disabled={disabled}
                field={{ key: "home_assistant_token", label: "Long-lived token", type: "password" }}
                value={form.home_assistant_token ?? ""}
                onChange={(value) => onChange("home_assistant_token", value)}
              />
              <SettingField
                disabled={disabled}
                field={{ key: "home_assistant_gate_open_service", label: "Cover open service" }}
                value={form.home_assistant_gate_open_service ?? ""}
                onChange={(value) => onChange("home_assistant_gate_open_service", value)}
              />
              <SettingField
                disabled={disabled}
                field={{ key: "home_assistant_tts_service", label: "TTS service" }}
                value={form.home_assistant_tts_service ?? ""}
                onChange={(value) => onChange("home_assistant_tts_service", value)}
              />
              <div className="ha-grid-wide">
                <EntitySelectField
                  disabled={disabled}
                  label="Default media player"
                  value={form.home_assistant_default_media_player ?? ""}
                  entities={discovery?.media_player_entities ?? []}
                  domainLabel="media_player"
                  onChange={(value) => onChange("home_assistant_default_media_player", value)}
                />
              </div>
            </div>
          </section>
      </div>
    </div>
  );
}

function EntitySelectField({
  disabled,
  label,
  value,
  entities,
  domainLabel,
  onChange
}: {
  disabled: boolean;
  label: string;
  value: string;
  entities: HomeAssistantEntity[];
  domainLabel: string;
  onChange: (value: string) => void;
}) {
  const hasCurrentValue = value && !entities.some((entity) => entity.entity_id === value);
  return (
    <label className="field">
      <span>{label}</span>
      <select disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Select {domainLabel} entity</option>
        {hasCurrentValue ? <option value={value}>{value}</option> : null}
        {entities.map((entity) => (
          <option key={entity.entity_id} value={entity.entity_id}>
            {entity.name ? `${entity.name} - ${entity.entity_id}` : entity.entity_id}
          </option>
        ))}
      </select>
    </label>
  );
}

export function HomeAssistantEditor(props: ProviderEditorProps) {
  const editor = useIntegrationSettingsEditor(props);
  const [discovery, setDiscovery] = React.useState<HomeAssistantDiscovery | null>(null);
  const [status, setStatus] = React.useState<IntegrationStatus | null>(null);
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const read = React.useCallback(async (signal: AbortSignal) => {
    setLoading(true); setError("");
    const results = await Promise.allSettled([integrationsApi.getHomeAssistantDiscovery({ signal }), integrationsApi.getHomeAssistantStatus({ signal })]);
    if (signal.aborted) return;
    if (results[0].status === "fulfilled") setDiscovery(results[0].value);
    if (results[1].status === "fulfilled") setStatus(results[1].value);
    setLoading(false);
    const failed = results.find((result) => result.status === "rejected");
    if (failed?.status === "rejected") { setError(failed.reason instanceof Error ? failed.reason.message : "Home Assistant metadata unavailable."); throw failed.reason; }
  }, []);
  const reload = useCoordinatedRead(read, integrationEventMatches);
  return <EditorDialog title={props.definition.title} description={props.loading ? "Loading settings..." : props.definition.description} dirty={editor.dirty} busy={editor.busy} onClose={props.onClose} onGuardChange={props.onGuardChange}>
    {({ closeAccepted }) => <form className="integration-settings-form" onSubmit={async (event) => { event.preventDefault(); if (await editor.save()) void closeAccepted(); }}>
      <HomeAssistantSettingsFields disabled={!editor.canManage || editor.busy || props.loading} discovery={discovery} discoveryError={error} discoveryLoading={loading} form={editor.form} onChange={editor.update} onReload={() => reload().catch(() => undefined)} status={status} />
      {editor.feedback ? <IntegrationFeedbackPanel feedback={editor.feedback} /> : null}
      <div className="modal-actions"><button className="secondary-button" disabled={!editor.canManage || editor.busy || props.loading} onClick={editor.test} type="button">{editor.operation === "test" ? "Testing..." : "Test Connection"}</button><button className="primary-button" disabled={!editor.canManage || editor.busy || props.loading} type="submit">{editor.operation === "save" ? "Saving..." : "Save"}</button></div>
    </form>}
  </EditorDialog>;
}
