import { useModalClose } from "../../ui/useModalClose";
import { useModalFocus } from "../../ui/useModalFocus";
import { useEditorDismiss } from "../../ui/useEditorDismiss";
import { Activity, Copy, Key, LogIn, Send, Settings, X } from "lucide-react";
import React from "react";
import { coerceSettingsPayload, secretSettingKeys } from "../../lib/settings";
import type { HomeAssistantDiscovery, IntegrationStatus, SettingsMap, UnifiProtectCamera, UserAccount } from "../../api/types";
import { AppriseUrlSummary, confirmIntegrationAction, ESPHomeDeviceSummary, ICloudCalendarPayload, integrationsApi, sendAppriseTestNotification, testIntegrationSettings, UnifiProtectStatus } from "../../api/integrations";
import { IntegrationDefinition, IntegrationFeedback, integrationInitialValues, ProtectIntegrationTab } from "./catalog";
import { UnifiProtectExposesPanel } from "./unifiProtect";
import { ProviderSettingsGrid, IntegrationFeedbackPanel, sleep } from "./components";
import { ICloudCalendarModal } from "./providers/ICloudCalendarPanel";
import { AppriseSettingsFields } from "./providers/ApprisePanel";
import { ESPHomeSettingsFields } from "./providers/ESPHomePanel";
import { HomeAssistantSettingsFields } from "./providers/HomeAssistantPanel";

export function IntegrationModal({
  definition,
  currentUser,
  initialTab,
  values,
  loading,
  protectCameras,
  protectError,
  protectLoading,
  protectStatus,
  accessDeviceStatus,
  homeAssistantStatus,
  icloudError,
  icloudLoading,
  icloudPayload,
  onClose: finishClose,
  onICloudChanged,
  onProtectRefresh,
  onSettingsChanged,
  onAccessDeviceStatusChanged,
  onSaved
}: {
  definition: IntegrationDefinition;
  currentUser: UserAccount;
  initialTab: ProtectIntegrationTab;
  values: SettingsMap;
  loading: boolean;
  protectCameras?: UnifiProtectCamera[];
  protectError?: string;
  protectLoading?: boolean;
  protectStatus?: UnifiProtectStatus | null;
  accessDeviceStatus?: IntegrationStatus | null;
  homeAssistantStatus?: IntegrationStatus | null;
  icloudError?: string;
  icloudLoading?: boolean;
  icloudPayload?: ICloudCalendarPayload;
  onClose: () => void;
  onICloudChanged?: () => Promise<void>;
  onProtectRefresh?: () => Promise<void>;
  onSettingsChanged: () => Promise<void>;
  onAccessDeviceStatusChanged?: (status: IntegrationStatus) => void;
  onSaved: (updates: Record<string, unknown>, confirmationToken?: string) => Promise<void>;
}) {
  const modalRef = React.useRef<HTMLDivElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const [activeTab, setActiveTab] = React.useState<ProtectIntegrationTab>(initialTab);
  const [form, setForm] = React.useState<Record<string, string>>(() => integrationInitialValues(definition, values));
  const [testing, setTesting] = React.useState(false);
  const [sendingTest, setSendingTest] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const initialForm = React.useRef(JSON.stringify(form));
  const requestClose = useEditorDismiss(onClose, JSON.stringify(form) !== initialForm.current, saving, "integration changes");
  useModalFocus(modalRef, true, requestClose);
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const [haDiscovery, setHaDiscovery] = React.useState<HomeAssistantDiscovery | null>(null);
  const [haDiscoveryError, setHaDiscoveryError] = React.useState("");
  const [haDiscoveryLoading, setHaDiscoveryLoading] = React.useState(false);
  const [appriseUrls, setAppriseUrls] = React.useState<AppriseUrlSummary[]>([]);
  const [appriseLoading, setAppriseLoading] = React.useState(false);
  const [esphomeDevices, setEsphomeDevices] = React.useState<ESPHomeDeviceSummary[]>([]);
  const [esphomeLoading, setEsphomeLoading] = React.useState(false);
  const [generatedLprWebhookToken, setGeneratedLprWebhookToken] = React.useState("");
  const isHomeAssistant = definition.key === "home_assistant";
  const isApprise = definition.key === "apprise";
  const isESPHome = definition.key === "esphome";
  const isUnifiProtect = definition.key === "unifi_protect";
  const isICloudCalendar = definition.key === "icloud_calendar";
  const canManage = currentUser.role === "admin";
  React.useEffect(() => {
    const next = integrationInitialValues(definition, values);
    initialForm.current = JSON.stringify(next);
    setForm(next);
    setActiveTab(initialTab);
    setFeedback(null);
    setHaDiscovery(null);
    setHaDiscoveryError("");
    setAppriseUrls([]);
    setEsphomeDevices([]);
    setGeneratedLprWebhookToken("");
  }, [definition.key, initialTab]);
  const update = (key: string, value: string) => {
    if (key === "lpr_webhook_token") setGeneratedLprWebhookToken(value);
    setForm((current) => ({ ...current, [key]: value }));
  };
  const loadHomeAssistantDiscovery = React.useCallback(async () => {
    if (!isHomeAssistant) return;
    setHaDiscoveryLoading(true);
    setHaDiscoveryError("");
    try {
      setHaDiscovery(await integrationsApi.getHomeAssistantDiscovery());
    } catch (error) {
      setHaDiscoveryError(error instanceof Error ? error.message : "Unable to load Home Assistant entities.");
    } finally {
      setHaDiscoveryLoading(false);
    }
  }, [isHomeAssistant]);
  React.useEffect(() => {
    if (isHomeAssistant) {
      loadHomeAssistantDiscovery().catch(() => undefined);
    }
  }, [isHomeAssistant, loadHomeAssistantDiscovery]);
  const loadAppriseUrls = React.useCallback(async () => {
    if (!isApprise) return;
    setAppriseLoading(true);
    try {
      setAppriseUrls(await integrationsApi.getAppriseUrls());
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Unable to load Apprise URLs",
        detail: error instanceof Error ? error.message : "Unable to load Apprise URLs."
      });
    } finally {
      setAppriseLoading(false);
    }
  }, [isApprise]);
  React.useEffect(() => {
    if (isApprise) {
      loadAppriseUrls().catch(() => undefined);
    }
  }, [isApprise, loadAppriseUrls]);
  const loadESPHomeDevices = React.useCallback(async () => {
    if (!isESPHome) return;
    setEsphomeLoading(true);
    try {
      setEsphomeDevices(await integrationsApi.getESPHomeDevices());
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Unable to load ESPHome devices",
        detail: error instanceof Error ? error.message : "Unable to load ESPHome devices."
      });
    } finally {
      setEsphomeLoading(false);
    }
  }, [isESPHome]);
  React.useEffect(() => {
    if (isESPHome) {
      loadESPHomeDevices().catch(() => undefined);
    }
  }, [isESPHome, loadESPHomeDevices]);
  const testConnection = async () => {
    if (!canManage) {
      setFeedback({
        tone: "error",
        title: "Administrator required",
        detail: "Administrator access is required to test integrations."
      });
      return;
    }
    setTesting(true);
    setFeedback({
      tone: "progress",
      title: "Testing connection",
      detail: "Preparing integration settings.",
      activeStep: 0
    });
    try {
      await sleep(180);
      setFeedback({
        tone: "progress",
        title: "Testing connection",
        detail: `Contacting ${definition.title}.`,
        activeStep: 1
      });
      const payload = {
        integration: definition.key,
        values: coerceSettingsPayload(form)
      };
      const request = testIntegrationSettings(payload, definition.title);
      await sleep(260);
      setFeedback({
        tone: "progress",
        title: "Testing connection",
        detail: "Validating the response.",
        activeStep: 2
      });
      const result = await request;
      if (!result.ok) throw new Error(result.message);
      setFeedback({
        tone: "success",
        title: "Connection verified",
        detail: result.message
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Connection failed",
        detail: error instanceof Error ? error.message : "Connection test failed."
      });
    } finally {
      setTesting(false);
    }
  };
  const sendTestNotification = async () => {
    if (!canManage) {
      setFeedback({
        tone: "error",
        title: "Administrator required",
        detail: "Administrator access is required to send test notifications."
      });
      return;
    }
    setSendingTest(true);
    setFeedback({
      tone: "progress",
      title: "Sending test notification",
      detail: "Composing a test message.",
      activeStep: 0
    });
    try {
      await sleep(180);
      setFeedback({
        tone: "progress",
        title: "Sending test notification",
        detail: "Delivering through Apprise.",
        activeStep: 1
      });
      await sendAppriseTestNotification();
      setFeedback({
        tone: "success",
        title: "Test notification sent",
        detail: "Apprise accepted the notification request."
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Notification failed",
        detail: error instanceof Error ? error.message : "Unable to send test notification."
      });
    } finally {
      setSendingTest(false);
    }
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canManage) {
      setFeedback({
        tone: "error",
        title: "Administrator required",
        detail: "Administrator access is required to save integration settings."
      });
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const updates = coerceSettingsPayload(form);
      const payload = {
        integration: definition.key,
        values: updates
      };
      const confirmation = await confirmIntegrationAction("settings.update", { values: updates }, {
        target_entity: "Integration",
        target_id: definition.key,
        target_label: definition.title,
        reason: "Save integration settings"
      });
      await onSaved(payload.values, confirmation.confirmation_token);
      await onClose();
    } catch (error) {
      setFeedback({
        tone: "error",
        title: "Unable to save settings",
        detail: error instanceof Error ? error.message : "Unable to save settings."
      });
    } finally {
      setSaving(false);
    }
  };
  const generateLprWebhookToken = () => {
    if (!window.crypto?.getRandomValues) {
      setFeedback({
        tone: "error",
        title: "Token generation unavailable",
        detail: "This browser does not expose secure random generation."
      });
      return;
    }
    const bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    const token = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    update("lpr_webhook_token", token);
    setFeedback({
      tone: "success",
      title: "Token generated",
      detail: "Copy this token into UniFi Protect before saving or closing this modal."
    });
  };
  const copyLprWebhookToken = async () => {
    const token = form.lpr_webhook_token || generatedLprWebhookToken;
    if (!token) return;
    try {
      await navigator.clipboard?.writeText(token);
      setFeedback({
        tone: "success",
        title: "Token copied",
        detail: "Paste it into UniFi Protect as the X-IACS-LPR-Token header value."
      });
    } catch {
      window.prompt("LPR webhook token", token);
    }
  };
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
      <div ref={modalRef} className="modal-card integration-modal" role="dialog" aria-modal="true" aria-label={definition.title}>
        <div className="modal-header">
          <div>
            <h2>{definition.title}</h2>
            <p>{loading ? "Loading settings..." : definition.description}</p>
          </div>
          <button className="icon-button" onClick={requestClose} type="button" aria-label="Close"><X size={16} /></button>
        </div>
        {isUnifiProtect ? (
          <div className="integration-modal-tabs" role="tablist" aria-label={`${definition.title} settings sections`}>
            <button
              aria-selected={activeTab === "general"}
              className={activeTab === "general" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("general")}
              role="tab"
              type="button"
            >
              <Settings size={15} /> General
            </button>
            <button
              aria-selected={activeTab === "exposes"}
              className={activeTab === "exposes" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("exposes")}
              role="tab"
              type="button"
            >
              <Activity size={15} /> Exposes
            </button>
          </div>
        ) : null}
        {isICloudCalendar ? (
          <ICloudCalendarModal
            error={icloudError ?? ""}
            loading={Boolean(icloudLoading)}
            payload={icloudPayload ?? { accounts: [], recent_sync_runs: [] }}
            onChanged={onICloudChanged ?? onSettingsChanged}
          />
        ) : isUnifiProtect && activeTab === "exposes" ? (
          <UnifiProtectExposesPanel
            cameras={protectCameras ?? []}
            error={protectError ?? ""}
            loading={Boolean(protectLoading)}
            onRefresh={onProtectRefresh ?? onSettingsChanged}
            status={protectStatus ?? null}
          />
        ) : (
          <form className="integration-settings-form" onSubmit={save}>
        {definition.oauth ? (
          <button className="secondary-button full" onClick={() => setFeedback({
            tone: "info",
            title: "OAuth is not active yet",
            detail: "Use an API key for this integration in the current build."
          })} type="button">
            <LogIn size={16} /> Login to {definition.title}
          </button>
        ) : null}
        {isHomeAssistant ? (
          <HomeAssistantSettingsFields
            discovery={haDiscovery}
            discoveryError={haDiscoveryError}
            discoveryLoading={haDiscoveryLoading}
            form={form}
            onChange={update}
            onReload={loadHomeAssistantDiscovery}
            status={homeAssistantStatus ?? null}
          />
        ) : isApprise ? (
          <AppriseSettingsFields
            canManage={canManage}
            loading={appriseLoading}
            urls={appriseUrls}
            onChanged={async (urls) => {
              setAppriseUrls(urls);
              await onSettingsChanged();
            }}
            onError={(error) => setFeedback({
              tone: "error",
              title: "Apprise URL update failed",
              detail: error
            })}
          />
        ) : isESPHome ? (
          <ESPHomeSettingsFields
            accessStatus={accessDeviceStatus ?? null}
            canManage={canManage}
            devices={esphomeDevices}
            loading={esphomeLoading}
            onAccessStatusChanged={onAccessDeviceStatusChanged}
            onChanged={async (devices) => {
              setEsphomeDevices(devices);
              await onSettingsChanged();
            }}
            onError={(error) => setFeedback({
              tone: "error",
              title: "ESPHome update failed",
              detail: error
            })}
          />
        ) : (
          <ProviderSettingsGrid
            fields={definition.fields}
            form={form}
            isConfiguredSecret={(key) => secretSettingKeys.has(key) && Boolean(values[key])}
            onChange={update}
            revealPasswordValue={(field) => field.key === "lpr_webhook_token" && Boolean(form.lpr_webhook_token)}
            fieldAction={(field) => field.key === "lpr_webhook_token" ? (
              <>
                {form.lpr_webhook_token ? (
                  <button className="secondary-button compact" disabled={!canManage} onClick={(event) => { event.preventDefault(); copyLprWebhookToken(); }} type="button">
                    <Copy size={14} /> Copy
                  </button>
                ) : null}
                <button className="secondary-button compact" disabled={!canManage} onClick={(event) => { event.preventDefault(); generateLprWebhookToken(); }} type="button">
                  <Key size={14} /> Generate
                </button>
              </>
            ) : undefined}
          />
        )}
        {feedback ? <IntegrationFeedbackPanel feedback={feedback} /> : null}
        <div className="modal-actions">
          {isApprise ? (
            <button className="secondary-button" onClick={sendTestNotification} disabled={!canManage || sendingTest} type="button">
              <Send size={15} /> {sendingTest ? "Sending..." : "Send Test"}
            </button>
          ) : null}
          <button className="secondary-button" onClick={testConnection} disabled={!canManage || testing} type="button">
            {testing ? "Testing..." : "Test Connection"}
          </button>
          {isApprise || isESPHome ? (
            <button className="primary-button" onClick={requestClose} type="button">Done</button>
          ) : (
            <button className="primary-button" disabled={!canManage || saving} type="submit">
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </div>
          </form>
        )}
      </div>
    </div>
  );
}
