import {
  Activity,
  AlertTriangle,
  Bell,
  CalendarDays,
  Check,
  CheckCircle2,
  Clock3,
  Copy,
  Key,
  Loader2,
  Lock,
  LogIn,
  MessageCircle,
  PlugZap,
  Plus,
  RefreshCcw,
  RefreshCw,
  Send,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  UserRound,
  X,
  Zap
} from "lucide-react";
import React from "react";
import { formatDate, titleCase } from "../../lib/format";
import { coerceSettingsPayload, secretSettingKeys, SettingField } from "../../lib/settings";
import { Badge } from "../../ui/primitives";
import type { AccessDeviceStreamDeviceStatus, HomeAssistantDiscovery, HomeAssistantEntity, IntegrationStatus, Person, SettingsMap, UnifiProtectCamera, UserAccount } from "../../api/types";
import type { SettingFieldDefinition } from "../../lib/settings";
import type { BadgeTone } from "../../ui/primitives";
import {
  AppriseUrlSummary,
  addAppriseUrl,
  addESPHomeDevice,
  confirmIntegrationAction,
  DependencyPackage,
  DependencyStorageStatus,
  DiscordChannel,
  DiscordIdentity,
  DiscordStatus,
  ESPHomeDeviceSummary,
  ICloudCalendarAccount,
  ICloudCalendarPayload,
  ICloudCalendarSyncRun,
  integrationsApi,
  removeAppriseUrl,
  removeESPHomeDevice,
  sendAppriseTestNotification,
  sendDiscordTestNotification,
  sendWhatsAppTestMessage,
  testESPHomeDevice,
  testIntegrationSettings,
  UnifiProtectStatus,
  UnifiProtectUpdateStatus,
  WhatsAppStatus
} from "../../api/integrations";
import { IntegrationDefinition, IntegrationFeedback, integrationInitialValues, ProtectIntegrationTab } from "./catalog";
import { DependencyUpdatePanel, dependencyIsActionableUpdate } from "./dependencyUpdates";
import { UnifiProtectExposesPanel, UnifiProtectUpdatesPanel } from "./unifiProtect";
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
function ProviderSettingsGrid({
  fieldAction,
  fields,
  form,
  isConfiguredSecret,
  onChange,
  revealPasswordValue
}: {
  fieldAction?: (field: SettingFieldDefinition) => React.ReactNode;
  fields: SettingFieldDefinition[];
  form: Record<string, string>;
  isConfiguredSecret: (key: string) => boolean;
  onChange: (key: string, value: string) => void;
  revealPasswordValue?: (field: SettingFieldDefinition) => boolean;
}) {
  return (
    <div className="settings-form-grid">
      {fields.map((field) => (
        <SettingField
          action={fieldAction?.(field)}
          field={field}
          key={field.key}
          isConfiguredSecret={isConfiguredSecret(field.key)}
          revealPasswordValue={revealPasswordValue?.(field)}
          value={form[field.key] ?? ""}
          onChange={(value) => onChange(field.key, value)}
        />
      ))}
    </div>
  );
}
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
  protectUpdateStatus,
  accessDeviceStatus,
  homeAssistantStatus,
  dependencyPackages,
  dependencyStorage,
  icloudError,
  icloudLoading,
  icloudPayload,
  discordChannels,
  discordError,
  discordIdentities,
  discordLoading,
  discordStatus,
  whatsappError,
  whatsappLoading,
  whatsappStatus,
  people,
  onClose,
  onDiscordChanged,
  onWhatsAppChanged,
  onICloudChanged,
  onProtectUpdateChanged,
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
  protectUpdateStatus?: UnifiProtectUpdateStatus | null;
  accessDeviceStatus?: IntegrationStatus | null;
  homeAssistantStatus?: IntegrationStatus | null;
  dependencyPackages: DependencyPackage[];
  dependencyStorage: DependencyStorageStatus | null;
  icloudError?: string;
  icloudLoading?: boolean;
  icloudPayload?: ICloudCalendarPayload;
  discordChannels?: DiscordChannel[];
  discordError?: string;
  discordIdentities?: DiscordIdentity[];
  discordLoading?: boolean;
  discordStatus?: DiscordStatus | null;
  whatsappError?: string;
  whatsappLoading?: boolean;
  whatsappStatus?: WhatsAppStatus | null;
  people: Person[];
  onClose: () => void;
  onDiscordChanged?: () => Promise<void>;
  onWhatsAppChanged?: () => Promise<void>;
  onICloudChanged?: () => Promise<void>;
  onProtectUpdateChanged?: () => Promise<void>;
  onProtectRefresh?: () => Promise<void>;
  onSettingsChanged: () => Promise<void>;
  onAccessDeviceStatusChanged?: (status: IntegrationStatus) => void;
  onSaved: (updates: Record<string, unknown>, confirmationToken?: string) => Promise<void>;
}) {
  const [activeTab, setActiveTab] = React.useState<ProtectIntegrationTab>(initialTab);
  const [form, setForm] = React.useState<Record<string, string>>(() => integrationInitialValues(definition, values));
  const [testing, setTesting] = React.useState(false);
  const [sendingTest, setSendingTest] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
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
  const isDiscord = definition.key === "discord";
  const isWhatsApp = definition.key === "whatsapp";
  const canManage = currentUser.role === "admin";
  const hasDependencyUpdates = dependencyPackages.length > 0;
  React.useEffect(() => {
    setForm(integrationInitialValues(definition, values));
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
        detail: `Delivering through ${isDiscord ? "Discord" : isWhatsApp ? "WhatsApp" : "Apprise"}.`,
        activeStep: 1
      });
      if (isDiscord) {
        await sendDiscordTestNotification(form.discord_default_notification_channel_id || undefined);
      } else if (isWhatsApp) {
        await sendWhatsAppTestMessage(coerceSettingsPayload(form));
      } else {
        await sendAppriseTestNotification();
      }
      setFeedback({
        tone: "success",
        title: "Test notification sent",
        detail: `${isDiscord ? "Discord" : isWhatsApp ? "WhatsApp" : "Apprise"} accepted the notification request.`
      });
      if (isWhatsApp) await onWhatsAppChanged?.();
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
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card integration-modal">
        <div className="modal-header">
          <div>
            <h2>{definition.title}</h2>
            <p>{loading ? "Loading settings..." : definition.description}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close"><X size={16} /></button>
        </div>
        {isUnifiProtect || hasDependencyUpdates ? (
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
              aria-selected={activeTab === "updates"}
              className={activeTab === "updates" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("updates")}
              role="tab"
              type="button"
            >
              <RefreshCcw size={15} /> Updates
              {dependencyPackages.some(dependencyIsActionableUpdate) ? <Badge tone="amber">{dependencyPackages.filter(dependencyIsActionableUpdate).length}</Badge> : null}
            </button>
            {isUnifiProtect ? (
            <button
              aria-selected={activeTab === "exposes"}
              className={activeTab === "exposes" ? "integration-modal-tab active" : "integration-modal-tab"}
              onClick={() => setActiveTab("exposes")}
              role="tab"
              type="button"
            >
              <Activity size={15} /> Exposes
            </button>
            ) : null}
          </div>
        ) : null}
        {activeTab === "updates" ? (
          dependencyPackages.length ? (
            <DependencyUpdatePanel
              packages={dependencyPackages}
              storage={dependencyStorage}
              onChanged={onProtectUpdateChanged ?? onSettingsChanged}
            />
          ) : isUnifiProtect ? (
            <UnifiProtectUpdatesPanel
              status={protectUpdateStatus ?? null}
              onChanged={onProtectUpdateChanged ?? onSettingsChanged}
            />
          ) : (
            <div className="empty-state">No enrolled dependencies are linked to this integration yet</div>
          )
        ) : isICloudCalendar ? (
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
        ) : isDiscord ? (
          <DiscordSettingsFields
            channels={discordChannels ?? []}
            error={discordError ?? ""}
            fields={definition.fields}
            form={form}
            identities={discordIdentities ?? []}
            isConfiguredSecret={(key) => secretSettingKeys.has(key) && Boolean(values[key])}
            loading={Boolean(discordLoading)}
            onChange={update}
            onIdentityChanged={onDiscordChanged ?? onSettingsChanged}
            people={people}
            status={discordStatus ?? null}
          />
        ) : isWhatsApp ? (
          <WhatsAppSettingsFields
            error={whatsappError ?? ""}
            fields={definition.fields}
            form={form}
            isConfiguredSecret={(key) => secretSettingKeys.has(key) && Boolean(values[key])}
            loading={Boolean(whatsappLoading)}
            onChange={update}
            status={whatsappStatus ?? null}
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
          {isApprise || isDiscord || isWhatsApp ? (
            <button className="secondary-button" onClick={sendTestNotification} disabled={!canManage || sendingTest} type="button">
              <Send size={15} /> {sendingTest ? "Sending..." : "Send Test"}
            </button>
          ) : null}
          <button className="secondary-button" onClick={testConnection} disabled={!canManage || testing} type="button">
            {testing ? "Testing..." : "Test Connection"}
          </button>
          {isApprise || isESPHome ? (
            <button className="primary-button" onClick={onClose} type="button">Done</button>
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
function ICloudCalendarModal({
  payload,
  loading,
  error,
  onChanged
}: {
  payload: ICloudCalendarPayload;
  loading: boolean;
  error: string;
  onChanged: () => Promise<void>;
}) {
  const [adding, setAdding] = React.useState(false);
  const [step, setStep] = React.useState<"credentials" | "verify">("credentials");
  const [appleId, setAppleId] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [code, setCode] = React.useState("");
  const [handshakeId, setHandshakeId] = React.useState("");
  const [handshakeAppleId, setHandshakeAppleId] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [syncing, setSyncing] = React.useState(false);
  const [removingId, setRemovingId] = React.useState<string | null>(null);
  const [reconnectingAccountId, setReconnectingAccountId] = React.useState<string | null>(null);
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const activeAccounts = payload.accounts.filter((account) => account.is_active);
  const latestRun = payload.recent_sync_runs[0] ?? null;
  const hasAttention = activeAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));
  const isReconnectFlow = Boolean(reconnectingAccountId);
  const resetAddFlow = () => {
    setAdding(false);
    setStep("credentials");
    setAppleId("");
    setPassword("");
    setCode("");
    setHandshakeId("");
    setHandshakeAppleId("");
    setReconnectingAccountId(null);
    setFeedback(null);
  };
  const startAddFlow = () => {
    if (adding && !isReconnectFlow) {
      resetAddFlow();
      return;
    }
    setAdding(true);
    setStep("credentials");
    setAppleId("");
    setPassword("");
    setCode("");
    setHandshakeId("");
    setHandshakeAppleId("");
    setReconnectingAccountId(null);
    setFeedback(null);
  };
  const startReconnectFlow = (account: ICloudCalendarAccount) => {
    setAdding(true);
    setStep("credentials");
    setAppleId(account.apple_id);
    setPassword("");
    setCode("");
    setHandshakeId("");
    setHandshakeAppleId("");
    setReconnectingAccountId(account.id);
    setFeedback({
      tone: "info",
      title: "Reconnect iCloud Calendar",
      detail: `Enter the Apple ID details for ${account.display_name} to refresh the trusted session.`
    });
  };
  const startAuth = async (event: React.FormEvent) => {
    event.preventDefault();
    const submittedAppleId = appleId.trim();
    const reconnecting = isReconnectFlow;
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await integrationsApi.startICloudAuth(submittedAppleId, password);
      setPassword("");
      if (result.status === "requires_2fa" && result.handshake_id) {
        setHandshakeId(result.handshake_id);
        setHandshakeAppleId(result.apple_id || submittedAppleId);
        setStep("verify");
        setFeedback({
          tone: "info",
          title: "Verification code required",
          detail: result.detail || "Enter the six-digit Apple verification code to finish connecting this account."
        });
      } else {
        resetAddFlow();
        await onChanged();
        setFeedback({
          tone: "success",
          title: reconnecting ? "iCloud Calendar reconnected" : "iCloud Calendar connected",
          detail: `${result.account?.display_name || submittedAppleId} is ready for calendar sync.`
        });
      }
    } catch (authError) {
      setFeedback({
        tone: "error",
        title: reconnecting ? "Unable to reconnect iCloud Calendar" : "Unable to connect iCloud Calendar",
        detail: authError instanceof Error ? authError.message : "Unable to connect iCloud Calendar."
      });
    } finally {
      setSubmitting(false);
    }
  };
  const verifyCode = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!handshakeId) return;
    const reconnecting = isReconnectFlow;
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await integrationsApi.verifyICloudAuth(handshakeId, code.trim());
      resetAddFlow();
      await onChanged();
      setFeedback({
        tone: "success",
        title: reconnecting ? "iCloud Calendar reconnected" : "iCloud Calendar connected",
        detail: `${result.account.display_name} is ready for calendar sync.`
      });
    } catch (verifyError) {
      setFeedback({
        tone: "error",
        title: "Verification failed",
        detail: verifyError instanceof Error ? verifyError.message : "Unable to verify that code."
      });
    } finally {
      setSubmitting(false);
    }
  };
  const syncNow = async () => {
    setSyncing(true);
    setFeedback({
      tone: "progress",
      title: "Syncing calendars",
      detail: "Scanning connected accounts for Open Gate events.",
      activeStep: 1
    });
    try {
      const run = await integrationsApi.syncICloudCalendar();
      await onChanged();
      setFeedback({
        tone: run.status === "ok" ? "success" : "info",
        title: run.status === "ok" ? "Calendar sync complete" : "Calendar sync complete with notes",
        detail: icloudSyncRunSummary(run)
      });
    } catch (syncError) {
      setFeedback({
        tone: "error",
        title: "Calendar sync failed",
        detail: syncError instanceof Error ? syncError.message : "Unable to sync iCloud Calendars."
      });
    } finally {
      setSyncing(false);
    }
  };
  const removeAccount = async (account: ICloudCalendarAccount) => {
    if (!window.confirm(`Remove iCloud Calendar account ${account.display_name}? Future unused calendar passes from this account will be cancelled.`)) return;
    setRemovingId(account.id);
    setFeedback(null);
    try {
      await integrationsApi.removeICloudAccount(account.id);
      await onChanged();
      setFeedback({
        tone: "success",
        title: "Account removed",
        detail: `${account.display_name} is no longer connected.`
      });
    } catch (removeError) {
      setFeedback({
        tone: "error",
        title: "Unable to remove account",
        detail: removeError instanceof Error ? removeError.message : "Unable to remove that iCloud Calendar account."
      });
    } finally {
      setRemovingId(null);
    }
  };
  return (
    <div className="icloud-calendar-panel">
      <section className="icloud-overview">
        <div className="icloud-overview-icon">
          <CalendarDays size={20} />
        </div>
        <div className="icloud-overview-copy">
          <strong>Automated Visitor Passes</strong>
          <span>Events with Open Gate in their notes create or update Visitor Passes for the next 14 days.</span>
        </div>
        <Badge tone={error ? "red" : hasAttention ? "amber" : activeAccounts.length ? "green" : "gray"}>
          {error ? "Error" : hasAttention ? "Needs Attention" : activeAccounts.length ? `${activeAccounts.length} Connected` : "Not Configured"}
        </Badge>
      </section>
      <div className="icloud-actions">
        <button className="primary-button" onClick={startAddFlow} disabled={submitting || syncing} type="button">
          <Plus size={15} /> {adding && !isReconnectFlow ? "Close Add Account" : "Add Account"}
        </button>
        <button className="secondary-button" onClick={syncNow} disabled={loading || syncing || !activeAccounts.length} type="button">
          {syncing ? <Loader2 className="spin" size={15} /> : <RefreshCcw size={15} />}
          {syncing ? "Syncing..." : "Sync Calendars Now"}
        </button>
      </div>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {feedback ? <IntegrationFeedbackPanel feedback={feedback} /> : null}
      {adding ? (
        step === "credentials" ? (
          <form className="icloud-auth-panel" onSubmit={startAuth}>
            <div className="icloud-auth-heading">
              {isReconnectFlow ? <RefreshCcw size={17} /> : <Key size={17} />}
              <div>
                <strong>{isReconnectFlow ? "Reconnect iCloud account" : "Add iCloud account"}</strong>
                <span>
                  {isReconnectFlow
                    ? "Refresh the trusted iCloud session without removing existing calendar passes."
                    : "Enter the Apple ID details once; only the trusted session is stored."}
                </span>
              </div>
            </div>
            <div className="icloud-auth-grid">
              <label className="field">
                <span>Apple ID</span>
                <div className="field-control">
                  <UserRound size={15} />
                  <input
                    autoComplete="username"
                    autoFocus
                    inputMode="email"
                    onChange={(event) => setAppleId(event.target.value)}
                    placeholder="name@example.com"
                    type="email"
                    value={appleId}
                  />
                </div>
              </label>
              <label className="field">
                <span>Password</span>
                <div className="field-control">
                  <Lock size={15} />
                  <input
                    autoComplete="current-password"
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="App-specific or account password"
                    type="password"
                    value={password}
                  />
                </div>
              </label>
            </div>
            <div className="icloud-form-actions">
              <button className="secondary-button" onClick={resetAddFlow} disabled={submitting} type="button">Cancel</button>
              <button className="primary-button" disabled={submitting || !appleId.trim() || !password} type="submit">
                {submitting
                  ? isReconnectFlow
                    ? "Reconnecting..."
                    : "Connecting..."
                  : isReconnectFlow
                    ? "Reconnect"
                    : "Connect"}
              </button>
            </div>
          </form>
        ) : (
          <form className="icloud-auth-panel" onSubmit={verifyCode}>
            <div className="icloud-auth-heading">
              <ShieldCheck size={17} />
              <div>
                <strong>Enter verification code</strong>
                <span>{handshakeAppleId || "Apple"} is waiting for the six-digit code.</span>
              </div>
            </div>
            <label className="field icloud-code-field">
              <span>Verification code</span>
              <div className="field-control">
                <ShieldCheck size={15} />
                <input
                  autoComplete="one-time-code"
                  autoFocus
                  inputMode="numeric"
                  maxLength={6}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  pattern="[0-9]{6}"
                  placeholder="123456"
                  type="text"
                  value={code}
                />
              </div>
            </label>
            <div className="icloud-form-actions">
              <button className="secondary-button" onClick={resetAddFlow} disabled={submitting} type="button">Cancel</button>
              <button className="primary-button" disabled={submitting || code.length !== 6} type="submit">
                {submitting ? "Verifying..." : "Verify and Connect"}
              </button>
            </div>
          </form>
        )
      ) : null}
      <section className="icloud-section">
        <div className="icloud-section-heading">
          <strong>Connected Accounts</strong>
          <span>{loading ? "Refreshing accounts" : `${activeAccounts.length} active`}</span>
        </div>
        <div className="icloud-account-list">
          {activeAccounts.length ? (
            activeAccounts.map((account) => (
              <article className="icloud-account-card" key={account.id}>
                <div className="icloud-account-main">
                  <span className="icloud-account-icon"><CalendarDays size={16} /></span>
                  <div>
                    <strong>{account.display_name}</strong>
                    <span>{account.apple_id}</span>
                  </div>
                </div>
                <div className="icloud-account-status">
                  <Badge tone={icloudAccountStatusTone(account.status)}>{icloudAccountStatusLabel(account.status)}</Badge>
                  <span>{account.last_sync_at ? `Last sync ${formatDate(account.last_sync_at)}` : "Not synced yet"}</span>
                  {account.last_error ? <small>{account.last_error}</small> : null}
                </div>
                <div className="icloud-account-actions">
                  {icloudAccountNeedsReconnect(account) ? (
                    <button
                      className="secondary-button compact"
                      disabled={submitting || syncing || removingId === account.id}
                      onClick={() => startReconnectFlow(account)}
                      type="button"
                    >
                      <RefreshCcw size={14} /> Reconnect
                    </button>
                  ) : null}
                  <button
                    aria-label={`Remove ${account.display_name}`}
                    className="icon-button danger"
                    disabled={removingId === account.id}
                    onClick={() => removeAccount(account)}
                    type="button"
                  >
                    {removingId === account.id ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
                  </button>
                </div>
              </article>
            ))
          ) : (
            <div className="icloud-empty">No iCloud Calendar accounts connected</div>
          )}
        </div>
      </section>
      <section className="icloud-section">
        <div className="icloud-section-heading">
          <strong>Recent Sync</strong>
          <span>{latestRun ? formatOptionalDate(latestRun.started_at || latestRun.finished_at) : "No syncs yet"}</span>
        </div>
        {latestRun ? (
          <div className="icloud-sync-summary">
            <div>
              <Badge tone={latestRun.status === "ok" ? "green" : latestRun.status === "error" ? "red" : "amber"}>{titleCase(latestRun.status)}</Badge>
              <span>{icloudSyncRunSummary(latestRun)}</span>
            </div>
            {latestRun.error ? <small>{latestRun.error}</small> : null}
          </div>
        ) : (
          <div className="icloud-empty">Run a manual sync after connecting an account</div>
        )}
      </section>
    </div>
  );
}
function icloudAccountStatusLabel(status: string) {
  if (status === "requires_reauth") return "Reconnect";
  if (status === "connected") return "Connected";
  if (status === "error") return "Error";
  if (status === "removed") return "Removed";
  return titleCase(status || "unknown");
}
function icloudAccountNeedsReconnect(account: ICloudCalendarAccount): boolean {
  return ["error", "requires_reauth"].includes(account.status);
}
function icloudAccountStatusTone(status: string): BadgeTone {
  if (status === "connected") return "green";
  if (status === "requires_reauth") return "amber";
  if (status === "error") return "red";
  return "gray";
}
function icloudSyncRunSummary(run: ICloudCalendarSyncRun) {
  const changes = [
    `${run.events_matched} matched`,
    `${run.passes_created} created`,
    `${run.passes_updated} updated`,
    `${run.passes_cancelled} cancelled`,
    `${run.passes_skipped} skipped`
  ];
  return `${run.account_count} account${run.account_count === 1 ? "" : "s"} scanned, ${run.events_scanned} event${run.events_scanned === 1 ? "" : "s"} read, ${changes.join(", ")}.`;
}
function formatOptionalDate(value: string | null | undefined) {
  return value ? formatDate(value) : "Pending";
}
function IntegrationFeedbackPanel({ feedback }: { feedback: IntegrationFeedback }) {
  const steps = ["Prepare", "Connect", "Validate"];
  const Icon = feedback.tone === "success" ? CheckCircle2 : feedback.tone === "error" ? AlertTriangle : Activity;
  return (
    <div className={`integration-feedback ${feedback.tone}`}>
      <div className="feedback-icon">
        <Icon size={18} />
      </div>
      <div className="feedback-copy">
        <strong>{feedback.title}</strong>
        <span>{feedback.detail}</span>
        {feedback.tone === "progress" ? (
          <div className="feedback-steps" aria-label="Connection test progress">
            {steps.map((step, index) => (
              <span
                className={index <= (feedback.activeStep ?? 0) ? "active" : ""}
                key={step}
              >
                {index < (feedback.activeStep ?? 0) ? <Check size={11} /> : null}
                {step}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
function AppriseSettingsFields({
  canManage,
  loading,
  urls,
  onChanged,
  onError
}: {
  canManage: boolean;
  loading: boolean;
  urls: AppriseUrlSummary[];
  onChanged: (urls: AppriseUrlSummary[]) => Promise<void>;
  onError: (error: string) => void;
}) {
  const [adding, setAdding] = React.useState(false);
  const [newUrl, setNewUrl] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const addUrl = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!newUrl.trim()) return;
    if (!canManage) {
      onError("Administrator access is required to update notification URLs.");
      return;
    }
    setSubmitting(true);
    try {
      const urls = await addAppriseUrl(newUrl.trim());
      setNewUrl("");
      setAdding(false);
      await onChanged(urls);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to add Apprise URL.");
    } finally {
      setSubmitting(false);
    }
  };
  const removeUrl = async (url: AppriseUrlSummary) => {
    if (!canManage) {
      onError("Administrator access is required to update notification URLs.");
      return;
    }
    setSubmitting(true);
    try {
      await onChanged(await removeAppriseUrl(url));
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to remove Apprise URL.");
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <div className="apprise-manager">
      <div className="apprise-manager-header">
        <div>
          <strong>Notification URLs</strong>
          <span>Add one destination per service. Secrets stay encrypted; only safe previews are shown here.</span>
        </div>
        <button className="primary-button" disabled={!canManage} onClick={() => setAdding((current) => !current)} type="button">
          <Plus size={15} /> Add New Apprise URL
        </button>
      </div>
      {adding ? (
        <form className="apprise-add-row" onSubmit={addUrl}>
          <label className="field">
            <span>Apprise URL</span>
            <div className="field-control">
              <Bell size={16} />
              <input
                autoFocus
                value={newUrl}
                onChange={(event) => setNewUrl(event.target.value)}
                placeholder="pover://USER_KEY@APP_TOKEN"
              />
            </div>
            <small className="field-hint">For Pushover use `pover://USER_KEY@APP_TOKEN`. `pushover://USER_KEY/APP_TOKEN` is accepted too.</small>
          </label>
          <div className="apprise-add-actions">
            <button className="secondary-button" onClick={() => setAdding(false)} type="button">Cancel</button>
            <button className="primary-button" disabled={!canManage || submitting || !newUrl.trim()} type="submit">
              {submitting ? "Adding..." : "Add URL"}
            </button>
          </div>
        </form>
      ) : null}
      <div className="apprise-url-table">
        <div className="apprise-url-head">
          <span>Type</span>
          <span>API & Key Preview</span>
          <span />
        </div>
        {loading ? (
          <div className="apprise-empty">Loading saved URLs</div>
        ) : urls.length ? (
          urls.map((url) => (
            <div className="apprise-url-row" key={`${url.scheme}-${url.index}`}>
              <div>
                <Badge tone={url.type === "Pushover" ? "blue" : "gray"}>{url.type}</Badge>
              </div>
              <div>
                <strong>{url.preview}</strong>
                <span>{url.scheme}</span>
              </div>
              <button className="icon-button danger" onClick={() => removeUrl(url)} disabled={!canManage || submitting} type="button" aria-label={`Remove ${url.type} URL`}>
                <Trash2 size={15} />
              </button>
            </div>
          ))
        ) : (
          <div className="apprise-empty">No notification URLs configured</div>
        )}
      </div>
    </div>
  );
}
function ESPHomeSettingsFields({
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
function DiscordSettingsFields({
  channels,
  error,
  fields,
  form,
  identities,
  isConfiguredSecret,
  loading,
  onChange,
  onIdentityChanged,
  people,
  status
}: {
  channels: DiscordChannel[];
  error: string;
  fields: SettingFieldDefinition[];
  form: Record<string, string>;
  identities: DiscordIdentity[];
  isConfiguredSecret: (key: string) => boolean;
  loading: boolean;
  onChange: (key: string, value: string) => void;
  onIdentityChanged: () => Promise<void>;
  people: Person[];
  status: DiscordStatus | null;
}) {
  const [users, setUsers] = React.useState<UserAccount[]>([]);
  const [savingIdentityId, setSavingIdentityId] = React.useState<string | null>(null);
  const [identityError, setIdentityError] = React.useState("");
  React.useEffect(() => {
    integrationsApi.getUsers().then(setUsers).catch(() => setUsers([]));
  }, []);
  const linkIdentity = async (identity: DiscordIdentity, field: "user_id" | "person_id", value: string) => {
    setSavingIdentityId(identity.id);
    setIdentityError("");
    try {
      await integrationsApi.updateDiscordIdentity(identity.id, {
        user_id: field === "user_id" ? value || null : identity.user_id,
        person_id: field === "person_id" ? value || null : identity.person_id
      });
      await onIdentityChanged();
    } catch (error) {
      setIdentityError(error instanceof Error ? error.message : "Unable to update Discord identity.");
    } finally {
      setSavingIdentityId(null);
    }
  };
  return (
    <div className="discord-settings">
      <section className="discord-overview">
        <div className="discord-overview-main">
          <span className="discord-overview-icon"><MessageCircle size={18} /></span>
          <div>
            <strong>{status?.connected ? "Bot connected" : status?.configured ? "Bot configured" : "Bot not configured"}</strong>
            <span>{status?.connected ? `${status.guild_count} guilds, ${status.channel_count} channels` : status?.last_error || error || "Save a bot token and allowlists to start Alfred on Discord."}</span>
          </div>
        </div>
        <Badge tone={error ? "red" : status?.connected ? "green" : status?.configured ? "blue" : "gray"}>
          {error ? "Error" : status?.connected ? "Connected" : status?.configured ? "Configured" : "Not Configured"}
        </Badge>
      </section>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      <ProviderSettingsGrid fields={fields} form={form} isConfiguredSecret={isConfiguredSecret} onChange={onChange} />
      <section className="discord-section">
        <div className="icloud-section-heading">
          <strong>Notification Channels</strong>
          <span>{loading ? "Refreshing" : `${channels.length} available`}</span>
        </div>
        <div className="discord-channel-list">
          {channels.length ? channels.map((channel) => (
            <button
              className="discord-channel-row"
              key={channel.id}
              onClick={() => onChange("discord_default_notification_channel_id", channel.id)}
              type="button"
            >
              <span><MessageCircle size={14} /> {channel.label || channel.name}</span>
              <Badge tone={form.discord_default_notification_channel_id === channel.id ? "green" : "gray"}>
                {form.discord_default_notification_channel_id === channel.id ? "Default" : channel.id}
              </Badge>
            </button>
          )) : (
            <div className="icloud-empty">No channels discovered yet. Save the bot token and allowlists first.</div>
          )}
        </div>
      </section>
      <section className="discord-section">
        <div className="icloud-section-heading">
          <strong>Discord Identities</strong>
          <span>{loading ? "Refreshing" : `${identities.length} seen`}</span>
        </div>
        {identityError ? <div className="auth-error inline-error">{identityError}</div> : null}
        <div className="discord-identity-list">
          {identities.length ? identities.map((identity) => (
            <article className="discord-identity-row" key={identity.id}>
              <div>
                <strong>{identity.provider_display_name}</strong>
                <span>{identity.provider_user_id}{identity.last_seen_at ? ` · ${formatDate(identity.last_seen_at)}` : ""}</span>
              </div>
              <select
                disabled={savingIdentityId === identity.id}
                onChange={(event) => linkIdentity(identity, "user_id", event.target.value)}
                value={identity.user_id ?? ""}
              >
                <option value="">No IACS user</option>
                {users.map((user) => <option key={user.id} value={user.id}>{user.full_name || user.username}</option>)}
              </select>
              <select
                disabled={savingIdentityId === identity.id}
                onChange={(event) => linkIdentity(identity, "person_id", event.target.value)}
                value={identity.person_id ?? ""}
              >
                <option value="">No person</option>
                {people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}
              </select>
            </article>
          )) : (
            <div className="icloud-empty">No Discord users have messaged Alfred yet</div>
          )}
        </div>
      </section>
    </div>
  );
}
function WhatsAppSettingsFields({
  error,
  fields,
  form,
  isConfiguredSecret,
  loading,
  onChange,
  status
}: {
  error: string;
  fields: SettingFieldDefinition[];
  form: Record<string, string>;
  isConfiguredSecret: (key: string) => boolean;
  loading: boolean;
  onChange: (key: string, value: string) => void;
  status: WhatsAppStatus | null;
}) {
  const webhookUrl = `${window.location.origin}/api/v1/webhooks/whatsapp`;
  const copyWebhookUrl = async () => {
    try {
      await navigator.clipboard?.writeText(webhookUrl);
    } catch {
      window.prompt("WhatsApp webhook URL", webhookUrl);
    }
  };
  return (
    <div className="discord-settings">
      <section className="discord-overview">
        <div className="discord-overview-main">
          <span className="discord-overview-icon"><MessageCircle size={18} /></span>
          <div>
            <strong>{status?.enabled ? "WhatsApp enabled" : "WhatsApp disabled"}</strong>
            <span>{status?.configured ? `${status.graph_api_version} · ${status.admin_target_count} Admin targets` : status?.last_error || error || "Save the Meta Cloud API credentials to enable Alfred on WhatsApp."}</span>
            {status?.visitor_pass_template_name ? <small>Visitor Pass template: {status.visitor_pass_template_name} · {status.visitor_pass_template_language || "en"}</small> : null}
          </div>
        </div>
        <Badge tone={error ? "red" : status?.enabled && status?.configured ? "green" : status?.configured ? "blue" : "gray"}>
          {error ? "Error" : status?.enabled && status?.configured ? "Enabled" : status?.configured ? "Configured" : "Not Configured"}
        </Badge>
      </section>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      <ProviderSettingsGrid fields={fields} form={form} isConfiguredSecret={isConfiguredSecret} onChange={onChange} />
      <section className="discord-section">
        <div className="icloud-section-heading">
          <strong>Webhook URL</strong>
          <span>{loading ? "Refreshing" : status?.signature_configured ? "Signed POSTs required" : "App secret required for inbound webhooks"}</span>
        </div>
        <label className="field">
          <span>IACS webhook URL</span>
          <div className="field-control">
            <PlugZap size={17} />
            <input readOnly value={webhookUrl} />
            <button className="icon-button" onClick={copyWebhookUrl} type="button" aria-label="Copy WhatsApp webhook URL">
              <Copy size={15} />
            </button>
          </div>
          <small className="field-hint">Use this callback URL in the Meta Developer Portal for WhatsApp webhook verification and message delivery.</small>
        </label>
      </section>
    </div>
  );
}
function HomeAssistantSettingsFields({
  discovery,
  discoveryError,
  discoveryLoading,
  form,
  onChange,
  onReload,
  status
}: {
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
    || (status?.state_refreshed_at ? `State refreshed ${formatOptionalDate(status.state_refreshed_at)}` : configured ? "Credentials are saved, but live state has not been verified yet." : "Save credentials to enable Home Assistant state sync.");
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
              <button className="secondary-button ha-refresh-button" onClick={onReload} disabled={discoveryLoading} type="button">
                <RefreshCcw size={15} /> {discoveryLoading ? "Refreshing..." : "Refresh"}
              </button>
            </div>
            <div className={`ha-health-strip ${connectionTone}`}>
              <Badge tone={connectionTone}>{connectionLabel}</Badge>
              <span>{connectionDetail}</span>
            </div>
            <div className="ha-setup-grid">
              <SettingField
                field={{ key: "home_assistant_url", label: "URL" }}
                value={form.home_assistant_url ?? ""}
                onChange={(value) => onChange("home_assistant_url", value)}
              />
              <SettingField
                field={{ key: "home_assistant_token", label: "Long-lived token", type: "password" }}
                value={form.home_assistant_token ?? ""}
                onChange={(value) => onChange("home_assistant_token", value)}
              />
              <SettingField
                field={{ key: "home_assistant_gate_open_service", label: "Cover open service" }}
                value={form.home_assistant_gate_open_service ?? ""}
                onChange={(value) => onChange("home_assistant_gate_open_service", value)}
              />
              <SettingField
                field={{ key: "home_assistant_tts_service", label: "TTS service" }}
                value={form.home_assistant_tts_service ?? ""}
                onChange={(value) => onChange("home_assistant_tts_service", value)}
              />
              <div className="ha-grid-wide">
                <EntitySelectField
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
  label,
  value,
  entities,
  domainLabel,
  onChange
}: {
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
      <select value={value} onChange={(event) => onChange(event.target.value)}>
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
function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
