import React from "react";
import { createPortal } from "react-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Camera,
  CalendarDays,
  Car,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Command,
  ClipboardPaste,
  Construction,
  Copy,
  Database,
  DoorClosed,
  DoorOpen,
  Download,
  File as FileIcon,
  FileImage,
  FileText,
  Gauge,
  GitBranch,
  HardHat,
  Home,
  Key,
  LayoutDashboard,
  Lock,
  LogIn,
  LogOut,
  Loader2,
  MessageCircle,
  Menu,
  Moon,
  Monitor,
  MoreHorizontal,
  Play,
  PlugZap,
  Plus,
  Paperclip,
  Pencil,
  RefreshCcw,
  RefreshCw,
  Search,
  Send,
  Smile,
  Smartphone,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Save,
  Split,
  Sparkles,
  Sun,
  Terminal,
  Ticket,
  Trash2,
  Trophy,
  Type,
  Unlock,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  X,
  Zap
} from "lucide-react";

import {
  api,
  Badge,
  BadgeTone,
  coerceSettingsPayload,
  discordListSettingKeys,
  EmptyState,
  formatDate,
  formatFileSize,
  HomeAssistantDiscovery,
  HomeAssistantEntity,
  HomeAssistantManagedCover,
  IntegrationStatus,
  isLlmProviderConfigured,
  isRecord,
  llmProviderDefinitions,
  LlmProviderKey,
  normalizeLlmProvider,
  NotificationChannelId,
  notificationChannelMeta,
  Person,
  RealtimeMessage,
  Schedule,
  secretSettingKeys,
  SettingField,
  SettingFieldDefinition,
  SettingsMap,
  stringifySetting,
  stringPayload,
  titleCase,
  titleFromEntityId,
  Toolbar,
  UnifiProtectCamera,
  UserAccount,
  useScheduleDefaultPolicyOptionLabel,
  useSettings,
  wsUrl
} from "../shared";



export type ICloudCalendarAccount = {
  id: string;
  apple_id: string;
  display_name: string;
  status: string;
  is_active: boolean;
  last_auth_at: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_summary: Record<string, unknown> | null;
  last_error: string | null;
  created_by_user_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type ICloudCalendarSyncRun = {
  id: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  trigger_source: string;
  triggered_by_user_id: string | null;
  account_count: number;
  events_scanned: number;
  events_matched: number;
  passes_created: number;
  passes_updated: number;
  passes_cancelled: number;
  passes_skipped: number;
  account_results: Record<string, unknown>[];
  error: string | null;
};

export type ICloudCalendarPayload = {
  accounts: ICloudCalendarAccount[];
  recent_sync_runs: ICloudCalendarSyncRun[];
};

export type ICloudAuthStartResponse = {
  status: "connected" | "requires_2fa";
  requires_2fa?: boolean;
  handshake_id?: string;
  apple_id?: string;
  detail?: string;
  account?: ICloudCalendarAccount;
};

export type ICloudAuthVerifyResponse = {
  status: "connected";
  account: ICloudCalendarAccount;
};

export type AppriseUrlSummary = {
  id?: string;
  index: number;
  type: string;
  scheme: string;
  preview: string;
};

export type DiscordStatus = {
  configured: boolean;
  connected: boolean;
  library_available: boolean;
  guild_count: number;
  channel_count: number;
  default_notification_channel_id: string;
  allow_direct_messages: boolean;
  require_mention: boolean;
  last_error: string | null;
  ready_at: string | null;
};

export type DiscordChannel = {
  id: string;
  guild_id: string;
  name: string;
  label: string;
};

export type DiscordIdentity = {
  id: string;
  provider_user_id: string;
  provider_display_name: string;
  user_id: string | null;
  user_label: string | null;
  person_id: string | null;
  person_label: string | null;
  last_seen_at: string | null;
};

export type WhatsAppStatus = {
  enabled: boolean;
  configured: boolean;
  webhook_configured: boolean;
  signature_configured: boolean;
  phone_number_id: string;
  business_account_id: string;
  graph_api_version: string;
  visitor_pass_template_name: string;
  visitor_pass_template_language: string;
  admin_target_count: number;
  last_error: string | null;
};

export type UnifiProtectStatus = {
  configured: boolean;
  connected: boolean;
  last_error: string | null;
  camera_count: number;
  host: string;
  port: number;
  verify_ssl: boolean;
  snapshot_width: number;
  snapshot_height: number;
};

export type UnifiProtectEvent = {
  id: string;
  type: string;
  camera_id: string;
  camera_name: string;
  start: string | null;
  end: string | null;
  score: number;
  smart_detect_types: string[];
  thumbnail_url: string;
  video_url: string | null;
};

export type UnifiProtectAnalysis = {
  camera_id: string;
  provider: string;
  text: string;
  snapshot_retained: boolean;
};

export type UnifiProtectUpdateStatus = {
  package: string;
  current_version: string;
  latest_version: string;
  update_available: boolean;
  active_package: {
    mode: string;
    version?: string | null;
    path?: string | null;
    installed_at?: string | null;
  };
  installed_overlays: Array<{ version: string; path: string }>;
  latest_summary?: Record<string, unknown>;
};

export type UnifiProtectReleaseNotes = {
  source: string;
  title: string;
  body: string;
  published_at?: string | null;
  html_url?: string | null;
};

export type UnifiProtectUpdateAnalysis = {
  package: string;
  current_version: string;
  target_version: string;
  latest_version: string;
  update_available: boolean;
  provider: string;
  analysis: string;
  release_notes: UnifiProtectReleaseNotes;
};

export type UnifiProtectBackup = {
  id: string;
  created_at: string;
  reason: string;
  package_version: string;
  settings_count: number;
  size_bytes: number;
  download_url: string;
  active_package?: {
    mode: string;
    version?: string | null;
  };
};

export type UnifiProtectUpdateApplyResult = {
  ok: boolean;
  previous_version: string;
  current_version: string;
  target_version: string;
  backup: UnifiProtectBackup;
  verification: {
    package_version?: string;
    camera_count?: number;
    snapshot_bytes?: number;
  };
};

export type ProtectIntegrationTab = "general" | "exposes" | "updates";

export type IntegrationsPageTab = "integrations" | "updates";

export type DependencyRiskStatus = "safe" | "warning" | "breaking" | "unknown";

export type DependencyPackage = {
  id: string;
  ecosystem: string;
  package_name: string;
  normalized_name: string;
  current_version: string | null;
  latest_version: string | null;
  dependant_area: string;
  manifest_path: string | null;
  manifest_section: string | null;
  requirement_spec: string | null;
  is_direct: boolean;
  is_enabled: boolean;
  update_available: boolean;
  risk_status: DependencyRiskStatus | string;
  last_checked_at: string | null;
  metadata: Record<string, unknown>;
  latest_analysis: DependencyAnalysis | null;
};

export type DependencyAnalysis = {
  id: string;
  dependency_id: string;
  target_version: string;
  provider: string;
  model: string | null;
  verdict: DependencyRiskStatus | string;
  summary_markdown: string;
  changelog_source: string | null;
  changelog_markdown: string | null;
  usage_summary: {
    reference_count?: number;
    references?: Array<{ path: string; line: number; text: string }>;
  };
  breaking_changes: Array<Record<string, unknown>>;
  verification_steps: string[];
  suggested_diff: string | null;
  created_at: string;
};

export type DependencyBackup = {
  id: string;
  dependency_id: string | null;
  package_name: string;
  ecosystem: string;
  version: string | null;
  reason: string;
  archive_path: string;
  storage_root: string;
  checksum_sha256: string;
  size_bytes: number;
  created_at: string;
  restored_at: string | null;
  metadata: Record<string, unknown>;
};

export type DependencyJob = {
  id: string;
  dependency_id: string | null;
  kind: string;
  status: string;
  phase: string | null;
  actor: string;
  target_version: string | null;
  backup_id: string | null;
  stdout_log_path: string | null;
  started_at: string | null;
  ended_at: string | null;
  result: Record<string, unknown>;
  error: string | null;
  trace_id: string | null;
};

export type DependencyCheckAllResult = {
  ok: boolean;
  checked: number;
  failed: number;
  updates: number;
  direct_only: boolean;
  errors: Array<{ dependency_id: string; error: string }>;
  packages?: DependencyPackage[];
};

export type DependencyStorageStatus = {
  mode: "local" | "nfs" | "samba" | string;
  mount_source: string;
  mount_options: string;
  mount_options_configured: boolean;
  mount_options_redacted: boolean;
  config_status: "active" | "pending_reboot" | "error" | string;
  backup_root: string;
  exists: boolean;
  writable: boolean;
  free_bytes: number;
  min_free_bytes: number;
  retention_days?: string;
  ok: boolean;
  detail: string;
};

export type DependencyJobEvent = {
  type: string;
  job_id?: string;
  created_at?: string;
  phase?: string;
  message?: string;
  diagnosis?: DependencyFailureDiagnosis;
  result?: Record<string, unknown>;
};

export const DEPENDENCY_JOB_EVENT_LIMIT = 200;

export const DEPENDENCY_JOB_MESSAGE_LIMIT = 2000;

export function compactDependencyJobEvent(event: DependencyJobEvent): DependencyJobEvent {
  if (typeof event.message !== "string" || event.message.length <= DEPENDENCY_JOB_MESSAGE_LIMIT) {
    return event;
  }
  return {
    ...event,
    message: `${event.message.slice(0, DEPENDENCY_JOB_MESSAGE_LIMIT)}... [truncated]`
  };
}

export type DependencyFailureDiagnosis = {
  category: string;
  title: string;
  summary: string;
  safe_state: string;
  retry_recommendation: string;
  actions: string[];
  affected_packages: string[];
  command?: string;
  technical_detail?: string;
};

export type DependencyConfirmAction =
  | { kind: "apply" }
  | { kind: "restore"; backup: DependencyBackup };

export function IntegrationsView({ people, realtime, schedules, status }: { people: Person[]; realtime: RealtimeMessage[]; schedules: Schedule[]; status: IntegrationStatus | null }) {
  const { values, loading, save, reload } = useSettings();
  const [pageTab, setPageTab] = React.useState<IntegrationsPageTab>("integrations");
  const [active, setActive] = React.useState<IntegrationDefinition | null>(null);
  const [activeTab, setActiveTab] = React.useState<ProtectIntegrationTab>("general");
  const [activeDependency, setActiveDependency] = React.useState<DependencyPackage | null>(null);
  const [llmProviderSaving, setLlmProviderSaving] = React.useState(false);
  const [protectStatus, setProtectStatus] = React.useState<UnifiProtectStatus | null>(null);
  const [protectCameras, setProtectCameras] = React.useState<UnifiProtectCamera[]>([]);
  const [protectSnapshotRefreshToken, setProtectSnapshotRefreshToken] = React.useState(0);
  const [protectUpdateStatus, setProtectUpdateStatus] = React.useState<UnifiProtectUpdateStatus | null>(null);
  const [protectLoading, setProtectLoading] = React.useState(false);
  const [protectError, setProtectError] = React.useState("");
  const [icloudPayload, setIcloudPayload] = React.useState<ICloudCalendarPayload>({ accounts: [], recent_sync_runs: [] });
  const [icloudLoading, setIcloudLoading] = React.useState(false);
  const [icloudError, setIcloudError] = React.useState("");
  const [discordStatus, setDiscordStatus] = React.useState<DiscordStatus | null>(null);
  const [discordChannels, setDiscordChannels] = React.useState<DiscordChannel[]>([]);
  const [discordIdentities, setDiscordIdentities] = React.useState<DiscordIdentity[]>([]);
  const [discordLoading, setDiscordLoading] = React.useState(false);
  const [discordError, setDiscordError] = React.useState("");
  const [whatsappStatus, setWhatsappStatus] = React.useState<WhatsAppStatus | null>(null);
  const [whatsappLoading, setWhatsappLoading] = React.useState(false);
  const [whatsappError, setWhatsappError] = React.useState("");
  const [dependencyPackages, setDependencyPackages] = React.useState<DependencyPackage[]>([]);
  const [dependencyStorage, setDependencyStorage] = React.useState<DependencyStorageStatus | null>(null);
  const [dependencyLoading, setDependencyLoading] = React.useState(false);
  const [dependencyError, setDependencyError] = React.useState("");
  const processedIcloudRealtimeRef = React.useRef(new Set<string>());
  const loadProtect = React.useCallback(async (forceRefresh = false) => {
    setProtectLoading(true);
    setProtectError("");
    try {
      const refreshSuffix = forceRefresh ? "?refresh=true" : "";
      const nextStatus = await api.get<UnifiProtectStatus>("/api/v1/integrations/unifi-protect/status");
      setProtectStatus(nextStatus);
      if (nextStatus.configured) {
        const result = await api.get<{ cameras: UnifiProtectCamera[] }>(`/api/v1/integrations/unifi-protect/cameras${refreshSuffix}`);
        setProtectCameras(result.cameras);
        setProtectSnapshotRefreshToken(Date.now());
      } else {
        setProtectCameras([]);
      }
    } catch (error) {
      setProtectError(error instanceof Error ? error.message : "Unable to load UniFi Protect cameras.");
    } finally {
      setProtectLoading(false);
    }
  }, []);
  const loadProtectUpdateStatus = React.useCallback(async () => {
    try {
      setProtectUpdateStatus(await api.get<UnifiProtectUpdateStatus>("/api/v1/integrations/unifi-protect/update/status"));
    } catch {
      setProtectUpdateStatus(null);
    }
  }, []);
  const loadICloudCalendar = React.useCallback(async () => {
    setIcloudLoading(true);
    setIcloudError("");
    try {
      setIcloudPayload(await api.get<ICloudCalendarPayload>("/api/v1/integrations/icloud-calendar/accounts"));
    } catch (error) {
      setIcloudError(error instanceof Error ? error.message : "Unable to load iCloud Calendar accounts.");
    } finally {
      setIcloudLoading(false);
    }
  }, []);

  React.useEffect(() => {
    for (const message of realtime.slice(0, 20).reverse()) {
      const key = `${message.type}-${message.created_at ?? ""}`;
      if (processedIcloudRealtimeRef.current.has(key)) continue;
      if (message.type !== "icloud_calendar.accounts_changed" && message.type !== "icloud_calendar.sync_completed") continue;
      processedIcloudRealtimeRef.current.add(key);
      if (message.type === "icloud_calendar.accounts_changed" && Array.isArray(message.payload.accounts)) {
        setIcloudPayload((current) => ({ ...current, accounts: message.payload.accounts as ICloudCalendarAccount[] }));
      }
      if (message.type === "icloud_calendar.sync_completed" && isRecord(message.payload.sync)) {
        const run = message.payload.sync as ICloudCalendarSyncRun;
        setIcloudPayload((current) => ({
          ...current,
          recent_sync_runs: [run, ...current.recent_sync_runs.filter((item) => item.id !== run.id)].slice(0, 5)
        }));
      }
    }
  }, [realtime]);
  const loadDiscord = React.useCallback(async () => {
    setDiscordLoading(true);
    setDiscordError("");
    try {
      const [statusResult, channelResult, identityResult] = await Promise.all([
        api.get<DiscordStatus>("/api/v1/integrations/discord/status"),
        api.get<{ channels: DiscordChannel[] }>("/api/v1/integrations/discord/channels"),
        api.get<{ identities: DiscordIdentity[] }>("/api/v1/integrations/discord/identities")
      ]);
      setDiscordStatus(statusResult);
      setDiscordChannels(channelResult.channels);
      setDiscordIdentities(identityResult.identities);
    } catch (error) {
      setDiscordError(error instanceof Error ? error.message : "Unable to load Discord integration.");
    } finally {
      setDiscordLoading(false);
    }
  }, []);
  const loadWhatsApp = React.useCallback(async () => {
    setWhatsappLoading(true);
    setWhatsappError("");
    try {
      setWhatsappStatus(await api.get<WhatsAppStatus>("/api/v1/integrations/whatsapp/status"));
    } catch (error) {
      setWhatsappError(error instanceof Error ? error.message : "Unable to load WhatsApp integration.");
    } finally {
      setWhatsappLoading(false);
    }
  }, []);
  const loadDependencyUpdates = React.useCallback(async () => {
    setDependencyLoading(true);
    setDependencyError("");
    try {
      const [packagesResult, storageResult] = await Promise.all([
        api.get<{ packages: DependencyPackage[] }>("/api/v1/dependency-updates/packages"),
        api.get<DependencyStorageStatus>("/api/v1/dependency-updates/storage/status")
      ]);
      setDependencyPackages(packagesResult.packages);
      setDependencyStorage(storageResult);
    } catch (error) {
      setDependencyError(error instanceof Error ? error.message : "Unable to load dependency updates.");
    } finally {
      setDependencyLoading(false);
    }
  }, []);
  const reloadSettingsAndProtect = React.useCallback(async () => {
    await reload();
    await loadProtect(true);
    await loadProtectUpdateStatus();
    await loadICloudCalendar();
    await loadDiscord();
    await loadWhatsApp();
    await loadDependencyUpdates();
  }, [loadDependencyUpdates, loadDiscord, loadICloudCalendar, loadProtect, loadProtectUpdateStatus, loadWhatsApp, reload]);

  React.useEffect(() => {
    loadProtect(false).catch(() => undefined);
    loadProtectUpdateStatus().catch(() => undefined);
    loadICloudCalendar().catch(() => undefined);
    loadDiscord().catch(() => undefined);
    loadWhatsApp().catch(() => undefined);
    loadDependencyUpdates().catch(() => undefined);
  }, [loadDependencyUpdates, loadDiscord, loadICloudCalendar, loadProtect, loadProtectUpdateStatus, loadWhatsApp]);

  const actionableDependencyUpdateCount = dependencyPackages.filter(dependencyIsActionableUpdate).length;
  const tiles = integrationDefinitions(status, values, protectStatus, protectUpdateStatus, icloudPayload.accounts, icloudError, discordStatus, discordError, whatsappStatus, whatsappError, dependencyPackages);
  const groupedTiles = integrationCategories
    .map((category) => ({
      ...category,
      tiles: tiles.filter((tile) => tile.category === category.key)
    }))
    .filter((category) => category.tiles.length);
  return (
    <section className="view-stack integrations-page">
      <Toolbar title="API & Integrations" count={tiles.length} icon={PlugZap} />
      <div className="integration-page-tabs" role="tablist" aria-label="API and integrations sections">
        <button className={pageTab === "integrations" ? "integration-page-tab active" : "integration-page-tab"} onClick={() => setPageTab("integrations")} type="button">
          <PlugZap size={16} /> Integrations
        </button>
        <button className={pageTab === "updates" ? "integration-page-tab active" : "integration-page-tab"} onClick={() => setPageTab("updates")} type="button">
          <RefreshCcw size={16} /> Updates
          {actionableDependencyUpdateCount ? <Badge tone="amber">{actionableDependencyUpdateCount}</Badge> : null}
        </button>
      </div>
      {pageTab === "updates" ? (
        <DependencyUpdatesHub
          loading={dependencyLoading}
          packages={dependencyPackages}
          storage={dependencyStorage}
          error={dependencyError}
          onChanged={loadDependencyUpdates}
          onInspect={setActiveDependency}
        />
      ) : (
      <div className="integration-category-stack">
        {groupedTiles.map((category) => (
          <section className="integration-category" key={category.key}>
            <div className="integration-category-header">
              <div className="integration-category-title">
                <strong>{category.label}</strong>
                <span>{category.description}</span>
              </div>
              <div className="integration-category-actions">
                {category.key === "ai" ? (
                  <LlmProviderSelector
                    saving={llmProviderSaving || loading}
                    values={values}
                    onChange={async (provider) => {
                      setLlmProviderSaving(true);
                      try {
                        await save({ llm_provider: provider });
                      } finally {
                        setLlmProviderSaving(false);
                      }
                    }}
                  />
                ) : null}
                <Badge tone="gray">{category.tiles.length}</Badge>
              </div>
            </div>
            <div className="integration-tile-grid">
              {category.tiles.map((tile) => {
                const Icon = tile.icon;
                return (
                  <article className="card integration-tile" key={tile.key}>
                    <button
                      className="integration-tile-main"
                      onClick={() => {
                        setActive(tile);
                        setActiveTab(tile.updateAvailable ? "updates" : "general");
                      }}
                      type="button"
                    >
                      <span className="integration-icon"><Icon size={22} /></span>
                      <div>
                        <strong>{tile.title}</strong>
                        <span>{tile.description}</span>
                        {tile.notificationChannels?.length ? (
                          <span className="integration-notification-link">
                            <Bell size={13} /> Available to Notifications: {tile.notificationChannels.map((channel) => notificationChannelMeta[channel].label).join(", ")}
                          </span>
                        ) : null}
                      </div>
                      <Badge tone={tile.statusTone}>{tile.statusLabel}</Badge>
                      {tile.updateAvailable ? <Badge tone="amber">Update Available</Badge> : null}
                    </button>
                  </article>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      )}
      {active ? (
        <IntegrationModal
          definition={active}
          initialTab={activeTab}
          dependencyPackages={dependenciesForIntegration(active, dependencyPackages)}
          dependencyStorage={dependencyStorage}
          loading={loading}
          protectCameras={protectCameras}
          protectError={protectError || protectStatus?.last_error || ""}
          protectLoading={protectLoading}
          protectStatus={protectStatus}
          protectUpdateStatus={protectUpdateStatus}
          icloudError={icloudError}
          icloudLoading={icloudLoading}
          icloudPayload={icloudPayload}
          discordChannels={discordChannels}
          discordError={discordError}
          discordIdentities={discordIdentities}
          discordLoading={discordLoading}
          discordStatus={discordStatus}
          whatsappError={whatsappError}
          whatsappLoading={whatsappLoading}
          whatsappStatus={whatsappStatus}
          people={people}
          schedules={schedules}
          values={values}
          onClose={() => setActive(null)}
          onICloudChanged={loadICloudCalendar}
          onDiscordChanged={loadDiscord}
          onWhatsAppChanged={loadWhatsApp}
          onProtectUpdateChanged={async () => {
            await loadProtectUpdateStatus();
            await loadProtect(true);
            await loadDependencyUpdates();
          }}
          onProtectRefresh={() => loadProtect(true)}
          onSettingsChanged={reloadSettingsAndProtect}
          onSaved={async (updates) => {
            await save(updates);
            await loadProtect(true);
            await loadWhatsApp();
            await loadDependencyUpdates();
            setActive(null);
          }}
        />
      ) : null}
      {activeDependency ? (
        <DependencyUpdateModal
          dependency={activeDependency}
          storage={dependencyStorage}
          onClose={() => setActiveDependency(null)}
          onChanged={loadDependencyUpdates}
        />
      ) : null}
      <UnifiProtectCameraSection
        cameras={protectCameras}
        error={protectError || protectStatus?.last_error || ""}
        loading={protectLoading}
        onRefresh={() => loadProtect(true)}
        refreshToken={protectSnapshotRefreshToken}
        status={protectStatus}
      />
    </section>
  );
}

export function LlmProviderSelector({
  saving,
  values,
  onChange
}: {
  saving: boolean;
  values: SettingsMap;
  onChange: (provider: LlmProviderKey) => Promise<void>;
}) {
  const activeProvider = normalizeLlmProvider(values.llm_provider);
  return (
    <div className="llm-provider-selector">
      <Bot size={15} />
      <label className="llm-provider-select">
        <span>System LLM</span>
        <select
          disabled={saving}
          value={activeProvider}
          onChange={(event) => onChange(event.target.value as LlmProviderKey)}
        >
          {llmProviderDefinitions.map((provider) => {
            const configured = isLlmProviderConfigured(provider.key, values);
            return (
              <option disabled={!configured && provider.key !== activeProvider} key={provider.key} value={provider.key}>
                {provider.label}{configured ? "" : " (not configured)"}
              </option>
            );
          })}
        </select>
      </label>
    </div>
  );
}

export type IntegrationDefinition = {
  key: string;
  title: string;
  description: string;
  category: "access" | "notifications" | "data" | "ai";
  icon: React.ElementType;
  fields: SettingFieldDefinition[];
  statusLabel: string;
  statusTone: BadgeTone;
  notificationChannels?: NotificationChannelId[];
  oauth?: boolean;
  updateAvailable?: boolean;
};

export type IntegrationFeedback = {
  tone: "progress" | "success" | "error" | "info";
  title: string;
  detail: string;
  activeStep?: number;
};

export const integrationCategories: Array<{
  key: IntegrationDefinition["category"];
  label: string;
  description: string;
}> = [
  {
    key: "access",
    label: "Access Control",
    description: "Physical site controls and sensor integrations."
  },
  {
    key: "notifications",
    label: "Notification Providers",
    description: "Destinations made available to the notification rules engine."
  },
  {
    key: "data",
    label: "Data & Intelligence",
    description: "Vehicle data, cameras, and operational enrichment."
  },
  {
    key: "ai",
    label: "AI Providers",
    description: "LLM providers used by chat, summaries, and analysis."
  }
];

export function dependenciesForIntegration(definition: IntegrationDefinition, dependencies: DependencyPackage[]) {
  return dependenciesForIntegrationKey(definition.key, dependencies);
}

export function dependenciesForIntegrationKey(key: string, dependencies: DependencyPackage[]) {
  const labels: Record<string, string[]> = {
    home_assistant: ["home assistant", "home-assistant"],
    icloud_calendar: ["icloud", "pyicloud"],
    apprise: ["apprise", "notifications"],
    discord: ["discord", "discord.py", "discord messaging"],
    whatsapp: ["whatsapp"],
    dvla: ["dvla"],
    unifi_protect: ["unifi", "uiprotect"],
    openai: ["openai"],
    gemini: ["gemini"],
    anthropic: ["anthropic", "claude"],
    ollama: ["ollama"]
  };
  const needles = labels[key] ?? [key.replace(/_/g, " ")];
  return dependencies.filter((dependency) => {
    const haystack = [
      dependency.package_name,
      dependency.normalized_name,
      dependency.dependant_area,
      dependency.manifest_path
    ].join(" ").toLowerCase();
    return needles.some((needle) => haystack.includes(needle.toLowerCase()));
  });
}

export function integrationDefinitions(
  status: IntegrationStatus | null,
  values: SettingsMap,
  protectStatus: UnifiProtectStatus | null,
  protectUpdateStatus: UnifiProtectUpdateStatus | null,
  icloudAccounts: ICloudCalendarAccount[],
  icloudError: string,
  discordStatus: DiscordStatus | null,
  discordError: string,
  whatsappStatus: WhatsAppStatus | null,
  whatsappError: string,
  dependencies: DependencyPackage[] = []
): IntegrationDefinition[] {
  const activeProvider = normalizeLlmProvider(values.llm_provider);
  const providerStatus = (key: string, secretKey?: string): Pick<IntegrationDefinition, "statusLabel" | "statusTone"> => {
    if (activeProvider === key) return { statusLabel: "Active", statusTone: "green" };
    if (secretKey && values[secretKey]) return { statusLabel: "Configured", statusTone: "blue" };
    if (key === "ollama" && values.ollama_base_url) return { statusLabel: "Configured", statusTone: "blue" };
    return { statusLabel: "Not Configured", statusTone: "gray" };
  };

  const hasDependencyUpdate = (key: string) => dependenciesForIntegrationKey(key, dependencies).some(dependencyIsActionableUpdate);
  const protectUpdateAvailable = Boolean(protectStatus?.connected && protectUpdateStatus?.update_available) || hasDependencyUpdate("unifi_protect");
  const activeIcloudAccounts = icloudAccounts.filter((account) => account.is_active);
  const icloudNeedsAttention = activeIcloudAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));

  return [
    {
      key: "home_assistant",
      title: "Home Assistant",
      description: "Gate control, mobile app notifications, TTS announcements, and state sync.",
      category: "access",
      icon: Home,
      statusLabel: status?.configured ? "Connected" : "Not Configured",
      statusTone: status?.configured ? "green" : "gray",
      updateAvailable: hasDependencyUpdate("home_assistant"),
      notificationChannels: ["mobile", "voice"],
      fields: [
        { key: "home_assistant_url", label: "URL" },
        { key: "home_assistant_token", label: "Long-lived token", type: "password" },
        { key: "home_assistant_gate_entities", label: "Gate entities" },
        { key: "home_assistant_gate_open_service", label: "Cover open service" },
        { key: "home_assistant_garage_door_entities", label: "Garage doors" },
        { key: "home_assistant_tts_service", label: "TTS service" },
        { key: "home_assistant_default_media_player", label: "Default media player" }
      ]
    },
    {
      key: "icloud_calendar",
      title: "iCloud Calendar",
      description: "Create Visitor Passes from calendar events marked Open Gate.",
      category: "access",
      icon: CalendarDays,
      statusLabel: icloudError
        ? "Error"
        : icloudNeedsAttention
          ? "Needs Attention"
          : activeIcloudAccounts.length
            ? `${activeIcloudAccounts.length} Connected`
            : "Not Configured",
      statusTone: icloudError ? "red" : icloudNeedsAttention ? "amber" : activeIcloudAccounts.length ? "green" : "gray",
      updateAvailable: hasDependencyUpdate("icloud_calendar"),
      fields: []
    },
    {
      key: "apprise",
      title: "Apprise",
      description: "Mobile and push notification fan-out.",
      category: "notifications",
      icon: Bell,
      statusLabel: values.apprise_urls ? "Configured" : "Not Configured",
      statusTone: values.apprise_urls ? "green" : "gray",
      updateAvailable: hasDependencyUpdate("apprise"),
      notificationChannels: ["mobile"],
      fields: [{
        key: "apprise_urls",
        label: "Apprise URLs",
        type: "textarea",
        href: "https://github.com/caronc/apprise/wiki",
        help: "For Pushover use pover://USER_KEY@APP_TOKEN. The app also accepts pushover://USER_KEY/APP_TOKEN and normalizes it."
      }]
    },
    {
      key: "discord",
      title: "Discord",
      description: "Bidirectional Alfred chat and Discord notification channels.",
      category: "notifications",
      icon: MessageCircle,
      statusLabel: discordError
        ? "Error"
        : discordStatus?.connected
          ? "Connected"
          : discordStatus?.configured || values.discord_bot_token
            ? "Configured"
            : "Not Configured",
      statusTone: discordError ? "red" : discordStatus?.connected ? "green" : discordStatus?.configured || values.discord_bot_token ? "blue" : "gray",
      updateAvailable: hasDependencyUpdate("discord"),
      notificationChannels: ["discord"],
      fields: [
        { key: "discord_bot_token", label: "Bot token", type: "password" },
        { key: "discord_guild_allowlist", label: "Guild allowlist", type: "textarea", help: "One Discord server ID per line." },
        { key: "discord_channel_allowlist", label: "Channel allowlist", type: "textarea", help: "One channel ID per line. Empty denies guild-channel messages." },
        { key: "discord_user_allowlist", label: "User allowlist", type: "textarea", help: "One Discord user ID per line." },
        { key: "discord_role_allowlist", label: "Role allowlist", type: "textarea", help: "One Discord role ID per line." },
        { key: "discord_admin_role_ids", label: "Admin role IDs", type: "textarea", help: "Members with these roles can resolve Alfred confirmations." },
        { key: "discord_default_notification_channel_id", label: "Default notification channel" },
        { key: "discord_allow_direct_messages", label: "Allow direct messages", type: "select", options: ["false", "true"] },
        { key: "discord_require_mention", label: "Require mention", type: "select", options: ["true", "false"] }
      ]
    },
    {
      key: "whatsapp",
      title: "WhatsApp",
      description: "Bidirectional Alfred chat and WhatsApp notification messages.",
      category: "notifications",
      icon: MessageCircle,
      statusLabel: whatsappError
        ? "Error"
        : whatsappStatus?.enabled && whatsappStatus?.configured
          ? "Enabled"
          : whatsappStatus?.configured || values.whatsapp_access_token || values.whatsapp_phone_number_id
            ? "Configured"
            : "Not Configured",
      statusTone: whatsappError ? "red" : whatsappStatus?.enabled && whatsappStatus?.configured ? "green" : whatsappStatus?.configured || values.whatsapp_access_token || values.whatsapp_phone_number_id ? "blue" : "gray",
      updateAvailable: hasDependencyUpdate("whatsapp"),
      notificationChannels: ["whatsapp"],
      fields: [
        { key: "whatsapp_enabled", label: "Enabled", type: "select", options: ["false", "true"] },
        { key: "whatsapp_access_token", label: "Access token", type: "password" },
        { key: "whatsapp_phone_number_id", label: "Phone Number ID" },
        { key: "whatsapp_business_account_id", label: "WhatsApp Business Account ID" },
        { key: "whatsapp_webhook_verify_token", label: "Webhook verify token", type: "password" },
        { key: "whatsapp_app_secret", label: "App secret", type: "password", help: "Required for incoming POST webhooks. IACS rejects unsigned WhatsApp payloads when WhatsApp is enabled." },
        { key: "whatsapp_graph_api_version", label: "Graph API version" },
        { key: "whatsapp_visitor_pass_template_name", label: "Visitor Pass template name" },
        { key: "whatsapp_visitor_pass_template_language", label: "Visitor Pass template language" }
      ]
    },
    {
      key: "dvla",
      title: "DVLA Lookup",
      description: "Vehicle Enquiry Service API plate lookups.",
      category: "data",
      icon: Search,
      statusLabel: values.dvla_api_key ? "Configured" : "Not Configured",
      statusTone: values.dvla_api_key ? "green" : "gray",
      updateAvailable: hasDependencyUpdate("dvla"),
      fields: [
        {
          key: "dvla_api_key",
          label: "DVLA API Key",
          type: "password",
          href: "https://developer-portal.driver-vehicle-licensing.api.gov.uk/apis/vehicle-enquiry-service/vehicle-enquiry-service-description.html"
        },
        {
          key: "dvla_vehicle_enquiry_url",
          label: "Vehicle enquiry URL",
          help: "Production endpoint for the DVLA Vehicle Enquiry Service API."
        },
        {
          key: "dvla_test_registration_number",
          label: "Test VRN",
          help: "Used only when this modal tests the DVLA connection."
        },
        { key: "dvla_timeout_seconds", label: "Timeout seconds", type: "number", min: 1, step: 1 }
      ]
    },
    {
      key: "unifi_protect",
      title: "UniFi Protect",
      description: "Camera snapshots, detection events, and AI image analysis.",
      category: "data",
      icon: Camera,
      statusLabel: protectUpdateAvailable ? `Update ${protectUpdateStatus?.latest_version}` : protectStatus?.connected ? "Connected" : protectStatus?.configured || values.unifi_protect_host ? "Configured" : "Not Configured",
      statusTone: protectUpdateAvailable ? "amber" : protectStatus?.connected ? "green" : protectStatus?.configured || values.unifi_protect_host ? "blue" : "gray",
      updateAvailable: protectUpdateAvailable,
      fields: [
        { key: "unifi_protect_host", label: "Console host" },
        { key: "unifi_protect_port", label: "HTTPS port", type: "number", min: 1, max: 65535, step: 1 },
        { key: "unifi_protect_username", label: "Local username", type: "password" },
        { key: "unifi_protect_password", label: "Local password", type: "password" },
        {
          key: "unifi_protect_api_key",
          label: "Integration API key",
          type: "password",
          href: "https://uiprotect.readthedocs.io"
        },
        { key: "unifi_protect_verify_ssl", label: "Verify TLS", type: "select", options: ["false", "true"] },
        { key: "unifi_protect_snapshot_width", label: "Snapshot width", type: "number", min: 160, max: 4096, step: 1 },
        { key: "unifi_protect_snapshot_height", label: "Snapshot height", type: "number", min: 90, max: 2160, step: 1 }
      ]
    },
    {
      key: "openai",
      title: "OpenAI",
      description: "Responses API provider for tool-capable chat.",
      category: "ai",
      icon: Bot,
      ...providerStatus("openai", "openai_api_key"),
      oauth: true,
      fields: [
        { key: "openai_api_key", label: "API key", type: "password", href: "https://platform.openai.com/api-keys" },
        { key: "openai_model", label: "Model" },
        { key: "openai_base_url", label: "Base URL" }
      ]
    },
    {
      key: "gemini",
      title: "Gemini",
      description: "Google Gemini provider.",
      category: "ai",
      icon: CircleDot,
      ...providerStatus("gemini", "gemini_api_key"),
      oauth: true,
      fields: [
        { key: "gemini_api_key", label: "API key", type: "password", href: "https://aistudio.google.com/app/apikey" },
        { key: "gemini_model", label: "Model" },
        { key: "gemini_base_url", label: "Base URL" }
      ]
    },
    {
      key: "anthropic",
      title: "Anthropic",
      description: "Claude provider.",
      category: "ai",
      icon: MessageCircle,
      ...providerStatus("anthropic", "anthropic_api_key"),
      fields: [
        { key: "anthropic_api_key", label: "API key", type: "password", href: "https://console.anthropic.com/settings/keys" },
        { key: "anthropic_model", label: "Model" },
        { key: "anthropic_base_url", label: "Base URL" }
      ]
    },
    {
      key: "ollama",
      title: "Ollama",
      description: "Local model endpoint.",
      category: "ai",
      icon: Database,
      ...providerStatus("ollama"),
      fields: [
        { key: "ollama_model", label: "Model" },
        { key: "ollama_base_url", label: "Base URL" }
      ]
    }
  ];
}

export function UnifiProtectCameraSection({
  cameras,
  error,
  loading,
  onRefresh,
  refreshToken,
  status
}: {
  cameras: UnifiProtectCamera[];
  error: string;
  loading: boolean;
  onRefresh: () => Promise<void>;
  refreshToken: number;
  status: UnifiProtectStatus | null;
}) {
  const [snapshotNonce, setSnapshotNonce] = React.useState<Record<string, number>>({});
  const [eventsByCamera, setEventsByCamera] = React.useState<Record<string, UnifiProtectEvent[]>>({});
  const [eventsLoading, setEventsLoading] = React.useState<Record<string, boolean>>({});
  const [analysisDrafts, setAnalysisDrafts] = React.useState<Record<string, string>>({});
  const [analysisByCamera, setAnalysisByCamera] = React.useState<Record<string, UnifiProtectAnalysis | string>>({});
  const [analysisLoading, setAnalysisLoading] = React.useState<Record<string, boolean>>({});

  const refreshSnapshot = (cameraId: string) => {
    setSnapshotNonce((current) => ({ ...current, [cameraId]: Date.now() }));
  };

  const loadEvents = async (cameraId: string) => {
    setEventsLoading((current) => ({ ...current, [cameraId]: true }));
    try {
      const result = await api.get<{ events: UnifiProtectEvent[] }>(`/api/v1/integrations/unifi-protect/events?camera_id=${encodeURIComponent(cameraId)}&limit=5`);
      setEventsByCamera((current) => ({ ...current, [cameraId]: result.events }));
    } catch (loadError) {
      setAnalysisByCamera((current) => ({
        ...current,
        [cameraId]: loadError instanceof Error ? loadError.message : "Unable to load recent camera events."
      }));
    } finally {
      setEventsLoading((current) => ({ ...current, [cameraId]: false }));
    }
  };

  const analyzeSnapshot = async (camera: UnifiProtectCamera) => {
    const prompt = analysisDrafts[camera.id]?.trim() || "Describe what is visible in this access-control camera snapshot. Call out people, vehicles, animals, packages, and anything unusual.";
    setAnalysisLoading((current) => ({ ...current, [camera.id]: true }));
    setAnalysisByCamera((current) => ({ ...current, [camera.id]: "" }));
    try {
      const result = await api.post<UnifiProtectAnalysis>(`/api/v1/integrations/unifi-protect/cameras/${encodeURIComponent(camera.id)}/analyze`, { prompt });
      setAnalysisByCamera((current) => ({ ...current, [camera.id]: result }));
    } catch (analysisError) {
      setAnalysisByCamera((current) => ({
        ...current,
        [camera.id]: analysisError instanceof Error ? analysisError.message : "Camera analysis failed."
      }));
    } finally {
      setAnalysisLoading((current) => ({ ...current, [camera.id]: false }));
    }
  };

  const configured = status?.configured ?? false;
  const connected = status?.connected ?? false;

  return (
    <section className="protect-section">
      <div className="protect-section-header">
        <div className="card-title">
          <Camera size={18} />
          <h2>UniFi Protect Cameras</h2>
        </div>
        <div className="protect-section-actions">
          <Badge tone={connected ? "green" : configured ? "blue" : "gray"}>{connected ? "Connected" : configured ? "Configured" : "Not Configured"}</Badge>
          <button className="secondary-button" onClick={onRefresh} disabled={loading} type="button">
            <RefreshCcw size={15} /> {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {!configured ? (
        <div className="empty-state">Configure UniFi Protect to load cameras</div>
      ) : loading && !cameras.length ? (
        <div className="empty-state">Loading cameras</div>
      ) : cameras.length ? (
        <div className="protect-camera-grid">
          {cameras.map((camera) => {
            const events = eventsByCamera[camera.id] ?? [];
            const analysis = analysisByCamera[camera.id];
            const detectionLabels = camera.detections.active.length ? camera.detections.active : camera.is_motion_detected ? ["motion"] : [];
            const snapshotUrl = `${camera.snapshot_url}?width=640&height=360&_=${snapshotNonce[camera.id] ?? refreshToken}`;
            return (
              <article className="protect-camera-card" key={camera.id}>
                <div className="protect-camera-media">
                  <img alt="" src={snapshotUrl} />
                  <div className="protect-camera-badges">
                    <Badge tone={camera.is_video_ready ? "green" : "amber"}>{camera.is_video_ready ? "Video Ready" : "Video Pending"}</Badge>
                    {camera.is_recording ? <Badge tone="blue">Recording</Badge> : null}
                  </div>
                </div>
                <div className="protect-camera-body">
                  <div className="protect-camera-title">
                    <div>
                      <strong>{camera.name}</strong>
                      <span>{camera.model || "UniFi Protect camera"} · {camera.state || "unknown"}</span>
                    </div>
                    <button className="icon-button" onClick={() => refreshSnapshot(camera.id)} type="button" aria-label={`Refresh ${camera.name} snapshot`}>
                      <RefreshCcw size={15} />
                    </button>
                  </div>

                  <div className="protect-detection-row">
                    {detectionLabels.length ? detectionLabels.map((label) => (
                      <Badge tone={label === "motion" ? "amber" : "blue"} key={label}>{titleCase(label)}</Badge>
                    )) : <Badge tone="gray">Clear</Badge>}
                    {camera.feature_flags.has_mic ? <Badge tone="gray">Mic</Badge> : null}
                    {camera.feature_flags.has_package_camera ? <Badge tone="gray">Package Cam</Badge> : null}
                  </div>

                  <div className="protect-channel-row">
                    {camera.channels.slice(0, 3).map((channel) => (
                      <span key={channel.id}>
                        {channel.width ?? "-"}x{channel.height ?? "-"} {channel.fps ? `${channel.fps}fps` : ""}
                      </span>
                    ))}
                  </div>

                  <div className="protect-camera-actions">
                    <button className="secondary-button" onClick={() => loadEvents(camera.id)} disabled={eventsLoading[camera.id]} type="button">
                      <Play size={15} /> {eventsLoading[camera.id] ? "Loading..." : "Recent Events"}
                    </button>
                  </div>

                  {events.length ? (
                    <div className="protect-event-list">
                      {events.map((event) => (
                        <div className="protect-event-row" key={event.id}>
                          <img alt="" src={`${event.thumbnail_url}?width=96&height=54`} />
                          <div>
                            <strong>{titleCase(event.type)}</strong>
                            <span>{event.start ? formatDate(event.start) : "Time pending"} · {event.smart_detect_types.map(titleCase).join(", ") || "motion"}</span>
                          </div>
                          {event.video_url ? <a className="icon-button" href={event.video_url} target="_blank" rel="noreferrer" aria-label="Open event clip"><Play size={14} /></a> : null}
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <div className="protect-analysis-box">
                    <input
                      value={analysisDrafts[camera.id] ?? ""}
                      onChange={(event) => setAnalysisDrafts((current) => ({ ...current, [camera.id]: event.target.value }))}
                      placeholder="Ask what to inspect"
                    />
                    <button className="primary-button" onClick={() => analyzeSnapshot(camera)} disabled={analysisLoading[camera.id]} type="button">
                      <Bot size={15} /> {analysisLoading[camera.id] ? "Analyzing..." : "Analyze"}
                    </button>
                  </div>
                  {analysis ? (
                    <div className={typeof analysis === "string" ? "protect-analysis-result error" : "protect-analysis-result"}>
                      {typeof analysis === "string" ? analysis : analysis.text}
                    </div>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty-state">No Protect cameras returned</div>
      )}
    </section>
  );
}

export type ProtectExposeRow = {
  name: string;
  value: string;
};

export function UnifiProtectExposesPanel({
  cameras,
  error,
  loading,
  onRefresh,
  status
}: {
  cameras: UnifiProtectCamera[];
  error: string;
  loading: boolean;
  onRefresh: () => Promise<void>;
  status: UnifiProtectStatus | null;
}) {
  const rows = buildProtectExposeRows(status, cameras);
  return (
    <div className="protect-exposes-panel">
      <div className="protect-exposes-header">
        <div>
          <strong>Exposed entities</strong>
          <span>Current values from UniFi Protect discovery and camera state.</span>
        </div>
        <button className="secondary-button" onClick={onRefresh} disabled={loading} type="button">
          <RefreshCcw size={15} /> {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {!status?.configured ? (
        <div className="empty-state">Configure UniFi Protect to see exposed entities</div>
      ) : (
        <div className="protect-exposes-grid">
          <ProtectExposeTable title="Console" rows={rows.console} defaultOpen />
          <ProtectExposeTable title="Cameras" rows={rows.cameras} defaultOpen />
          <ProtectExposeTable title="Sensors" rows={rows.sensors} defaultOpen />
          <ProtectExposeTable title="Detections" rows={rows.detections} defaultOpen />
          <ProtectExposeTable title="Channels" rows={rows.channels} />
        </div>
      )}
    </div>
  );
}

export function ProtectExposeTable({
  defaultOpen = false,
  rows,
  title
}: {
  defaultOpen?: boolean;
  rows: ProtectExposeRow[];
  title: string;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <section className="protect-expose-table-card">
      <button className="protect-expose-table-toggle" onClick={() => setOpen((current) => !current)} type="button" aria-expanded={open}>
        <div>
          <strong>{title}</strong>
          <span>{rows.length} item{rows.length === 1 ? "" : "s"}</span>
        </div>
        {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
      </button>
      {open ? (
        rows.length ? (
          <table className="protect-expose-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Current value</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${title}-${row.name}`}>
                  <td>{row.name}</td>
                  <td>{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state compact">No {title.toLowerCase()} exposed yet</div>
        )
      ) : null}
    </section>
  );
}

export function buildProtectExposeRows(status: UnifiProtectStatus | null, cameras: UnifiProtectCamera[]) {
  const consoleRows: ProtectExposeRow[] = [
    { name: "Connection", value: status?.connected ? "Connected" : status?.configured ? "Configured" : "Not configured" },
    { name: "Console", value: status?.host ? `${status.host}:${status.port}` : "Not configured" },
    { name: "TLS verification", value: formatExposeValue(status?.verify_ssl) },
    { name: "Camera count", value: String(status?.camera_count ?? cameras.length) },
    { name: "Snapshot dimensions", value: status ? `${status.snapshot_width}x${status.snapshot_height}` : "Unknown" }
  ];

  const cameraRows = cameras.map((camera) => ({
    name: camera.name,
    value: [
      camera.state || "unknown",
      camera.is_video_ready ? "video ready" : "video pending",
      camera.is_recording ? "recording" : "not recording"
    ].join(" · ")
  }));

  const sensorRows = cameras.flatMap((camera) => [
    { name: `${camera.name} motion`, value: formatExposeValue(camera.is_motion_detected) },
    { name: `${camera.name} smart detection`, value: formatExposeValue(camera.is_smart_detected) },
    { name: `${camera.name} recording enabled`, value: formatExposeValue(camera.is_recording_enabled) },
    { name: `${camera.name} microphone`, value: formatExposeValue(camera.feature_flags.has_mic) },
    { name: `${camera.name} package camera`, value: formatExposeValue(camera.feature_flags.has_package_camera) }
  ]);

  const detectionRows = cameras.flatMap((camera) => [
    { name: `${camera.name} active detections`, value: camera.detections.active.length ? camera.detections.active.map(titleCase).join(", ") : "Clear" },
    { name: `${camera.name} supported smart detections`, value: camera.feature_flags.smart_detect_types.length ? camera.feature_flags.smart_detect_types.map(titleCase).join(", ") : "None" },
    { name: `${camera.name} supported audio detections`, value: camera.feature_flags.smart_detect_audio_types.length ? camera.feature_flags.smart_detect_audio_types.map(titleCase).join(", ") : "None" },
    { name: `${camera.name} last motion`, value: camera.last_motion_at ? formatDate(camera.last_motion_at) : "None" },
    { name: `${camera.name} last smart detection`, value: camera.last_smart_detect_at ? formatDate(camera.last_smart_detect_at) : "None" }
  ]);

  const channelRows = cameras.flatMap((camera) => camera.channels.map((channel) => ({
    name: `${camera.name} · ${channel.name || channel.id}`,
    value: [
      channel.width && channel.height ? `${channel.width}x${channel.height}` : "resolution unknown",
      channel.fps ? `${channel.fps}fps` : null,
      channel.bitrate ? `${channel.bitrate}kbps` : null,
      channel.is_rtsp_enabled ? "RTSP enabled" : "RTSP disabled",
      channel.is_package ? "package channel" : null
    ].filter(Boolean).join(" · ")
  })));

  return {
    console: consoleRows,
    cameras: cameraRows,
    sensors: sensorRows,
    detections: detectionRows,
    channels: channelRows
  };
}

export function formatExposeValue(value: boolean | string | number | null | undefined) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === undefined || value === "") return "Unknown";
  return String(value);
}

export type ProtectUpdateConfirmAction =
  | { kind: "apply" }
  | { kind: "restore"; backup: UnifiProtectBackup }
  | { kind: "delete"; backup: UnifiProtectBackup };

export function dependencyUpdateTone(dependency: DependencyPackage): BadgeTone {
  if (dependency.update_available) return "amber";
  if (dependency.last_checked_at) return "green";
  return "gray";
}

export function dependencyUpdateLabel(dependency: DependencyPackage): string {
  if (dependency.update_available && !dependencyCanApply(dependency)) return "Transitive Update";
  if (dependency.update_available) return "Update Available";
  if (dependency.last_checked_at) return "Current";
  return "Unchecked";
}

export function dependencyCanApply(dependency: DependencyPackage): boolean {
  return dependency.ecosystem === "docker_image" || dependency.is_direct;
}

export function dependencyIsActionableUpdate(dependency: DependencyPackage): boolean {
  return dependency.update_available && dependencyCanApply(dependency);
}

export function dependencyJobProgress(job: DependencyJob | null, events: DependencyJobEvent[]) {
  const phase = job?.phase || [...events].reverse().find((event) => event.phase)?.phase || "starting";
  const status = job?.status || "queued";
  const phaseProgress: Record<string, number> = {
    queued: 3,
    starting: 8,
    backup: 22,
    validate_backup: 22,
    apply: 55,
    restore_files: 55,
    verify: 82,
    rollback: 92,
    completed: 100,
    failed: 100
  };
  const labelMap: Record<string, string> = {
    queued: "Queued",
    starting: "Starting",
    backup: "Creating offline backup",
    validate_backup: "Validating backup",
    apply: "Applying update",
    restore_files: "Restoring files",
    verify: "Verifying",
    rollback: "Rolling back",
    completed: "Completed",
    failed: "Failed"
  };
  return {
    percent: status === "completed" ? 100 : status === "failed" ? 100 : phaseProgress[phase] ?? 10,
    label: labelMap[phase] || titleCase(phase),
    phase,
    status
  };
}

export function formatDependencyLogTime(value?: string) {
  if (!value) return "now";
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dependencyLogTypeLabel(value: string) {
  if (value === "stdout") return "log";
  if (value === "connection.ready") return "ready";
  return value.replaceAll("_", " ");
}

export function dependencyJobDiagnosis(job: DependencyJob | null): DependencyFailureDiagnosis | null {
  const value = job?.result?.diagnosis;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  return {
    category: stringPayload(payload.category) || "unknown",
    title: stringPayload(payload.title) || "Update job failed",
    summary: stringPayload(payload.summary) || "The update did not complete.",
    safe_state: stringPayload(payload.safe_state) || "IACS stopped before promoting unverified runtime changes.",
    retry_recommendation: stringPayload(payload.retry_recommendation) || "Review the logs and retry when the blocker is resolved.",
    actions: arrayOfStrings(payload.actions),
    affected_packages: arrayOfStrings(payload.affected_packages),
    command: stringPayload(payload.command),
    technical_detail: stringPayload(payload.technical_detail)
  };
}

export function dependencyRollbackSummary(job: DependencyJob | null): string {
  const rollback = job?.result?.rollback;
  if (!rollback || typeof rollback !== "object" || Array.isArray(rollback)) {
    return "No live manifests were promoted.";
  }
  const payload = rollback as Record<string, unknown>;
  if (payload.restored === true) return "Offline backup restored; live manifests are back to their pre-update state.";
  if (payload.attempted === true) return "Rollback was attempted. Review the job logs to confirm the restore result.";
  return "No live manifests were promoted.";
}

export function arrayOfStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

export function verificationStepDetails(step: string) {
  const clean = step.trim();
  const explicit = clean.match(/^\[(automated|operator|manual|iacs)\]\s*(.+)$/i);
  if (explicit) {
    const type = explicit[1].toLowerCase();
    return {
      label: type === "operator" || type === "manual" ? "Operator" : "IACS job",
      text: explicit[2],
      tone: type === "operator" || type === "manual" ? "amber" as BadgeTone : "blue" as BadgeTone
    };
  }
  const automated = /\b(npm run build|frontend build|compile|pytest|unit test|health|typecheck|lint)\b/i.test(clean);
  return {
    label: automated ? "IACS job" : "Operator",
    text: clean,
    tone: automated ? "blue" as BadgeTone : "amber" as BadgeTone
  };
}

export function parseSuggestedDiff(diff: string) {
  const files: Array<{ file: string; added: number; removed: number; lines: string[] }> = [];
  let current: { file: string; added: number; removed: number; lines: string[] } | null = null;
  for (const line of diff.split(/\r?\n/)) {
    const fileMatch = line.match(/^\*\*\* (?:Update|Add|Delete) File:\s+(.+)$/);
    if (fileMatch) {
      current = { file: fileMatch[1], added: 0, removed: 0, lines: [line] };
      files.push(current);
      continue;
    }
    if (!current) {
      current = { file: "Suggested patch", added: 0, removed: 0, lines: [] };
      files.push(current);
    }
    current.lines.push(line);
    if (line.startsWith("+") && !line.startsWith("+++")) current.added += 1;
    if (line.startsWith("-") && !line.startsWith("---")) current.removed += 1;
  }
  return files;
}

export function DependencyUpdatesHub({
  packages,
  storage,
  loading,
  error,
  onChanged,
  onInspect
}: {
  packages: DependencyPackage[];
  storage: DependencyStorageStatus | null;
  loading: boolean;
  error: string;
  onChanged: () => Promise<void>;
  onInspect: (dependency: DependencyPackage) => void;
}) {
  const [checkingAll, setCheckingAll] = React.useState(false);
  const [checkSummary, setCheckSummary] = React.useState<DependencyCheckAllResult | null>(null);
  const [checkError, setCheckError] = React.useState("");
  const [showAll, setShowAll] = React.useState(false);
  const updateRows = React.useMemo(() => packages.filter(dependencyIsActionableUpdate), [packages]);
  const transitiveUpdateCount = packages.filter((dependency) => dependency.update_available && !dependencyCanApply(dependency)).length;
  const rows = showAll ? packages : updateRows;
  const checkedCount = packages.filter((dependency) => dependency.last_checked_at).length;
  const directCount = packages.filter((dependency) => dependency.is_direct).length;
  const sync = async () => {
    await api.post("/api/v1/dependency-updates/sync", {});
    await onChanged();
  };
  const checkAll = async () => {
    setCheckingAll(true);
    setCheckSummary(null);
    setCheckError("");
    try {
      const result = await api.post<DependencyCheckAllResult>("/api/v1/dependency-updates/check", { direct_only: false });
      setCheckSummary(result);
      await onChanged();
      setShowAll(false);
    } catch (nextError) {
      setCheckError(nextError instanceof Error ? nextError.message : "Unable to check dependencies.");
    } finally {
      setCheckingAll(false);
    }
  };
  return (
    <div className="dependency-updates-page">
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {checkError ? <div className="auth-error inline-error">{checkError}</div> : null}
      <DependencyStoragePanel storage={storage} onChanged={onChanged} />
      <section className="card dependency-update-table-card">
        <div className="dependency-update-table-head">
          <div>
            <h2>{showAll ? "Enrolled Dependencies" : "Available Updates"}</h2>
            <p>
              {showAll
                ? "All auto-enrolled external packages, including dependencies that are current or not checked yet."
                : updateRows.length
                  ? "Direct packages and images with newer versions that can be applied from IACS."
                  : checkedCount
                    ? "No actionable updates are currently known."
                    : "Run Check All to compare enrolled packages with their registries."}
            </p>
          </div>
          <div className="dependency-update-actions">
            <button className="primary-button" onClick={checkAll} disabled={loading || checkingAll} type="button">
              {checkingAll ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />} Check All
            </button>
            <button className="secondary-button" onClick={sync} disabled={loading} type="button">
              <RefreshCcw size={15} /> Sync Enrollment
            </button>
            <button className="secondary-button" onClick={onChanged} disabled={loading} type="button">
              <RefreshCw size={15} /> Refresh
            </button>
            <button className={showAll ? "secondary-button active" : "secondary-button"} onClick={() => setShowAll((value) => !value)} disabled={loading} type="button">
              {showAll ? "Show Updates" : "Show All"}
            </button>
          </div>
        </div>
        <div className="dependency-update-metrics">
          <div><span>Actionable</span><strong>{updateRows.length}</strong></div>
          <div><span>Enrolled</span><strong>{packages.length}</strong></div>
          <div><span>Direct</span><strong>{directCount}</strong></div>
          <div><span>Checked</span><strong>{checkedCount}</strong></div>
          <div><span>Transitive</span><strong>{transitiveUpdateCount}</strong></div>
        </div>
        {checkSummary ? (
          <div className={checkSummary.failed ? "dependency-check-summary warning" : "dependency-check-summary"}>
            Checked {checkSummary.checked} packages and found {checkSummary.updates} registry updates.
            {updateRows.length ? ` ${updateRows.length} can be applied directly from this hub.` : ""}
            {transitiveUpdateCount ? ` ${transitiveUpdateCount} transitive lockfile updates are available under Show All.` : ""}
            {checkSummary.failed ? ` ${checkSummary.failed} checks failed; see Updates & Rollbacks logs for details.` : ""}
          </div>
        ) : null}
        {loading || checkingAll ? <div className="loading-panel">{checkingAll ? "Checking every enrolled dependency" : "Loading dependency updates"}</div> : null}
        {!loading && !checkingAll && rows.length ? (
          <div className="dependency-update-table">
            <div className="dependency-update-row header">
              <span>Package</span>
              <span>Dependant</span>
              <span>Current</span>
              <span>New</span>
              <span>{showAll ? "Update" : "Risk"}</span>
              <span />
            </div>
            {rows.map((dependency) => (
              <div className="dependency-update-row" key={dependency.id}>
                <div>
                  <strong>{dependency.package_name}</strong>
                  <small>{dependency.ecosystem} · {dependency.is_direct ? "direct" : "transitive"}</small>
                </div>
                <span>{dependency.dependant_area}</span>
                <code>{dependency.current_version || "unknown"}</code>
                <code>{dependency.latest_version || "unchecked"}</code>
                <Badge tone={showAll ? dependencyUpdateTone(dependency) : riskTone(dependency.risk_status)}>
                  {showAll ? dependencyUpdateLabel(dependency) : titleCase(String(dependency.risk_status || "unknown"))}
                </Badge>
                <button className="secondary-button" onClick={() => onInspect(dependency)} type="button">
                  {dependency.update_available && dependencyCanApply(dependency) ? "Inspect/Update" : "Inspect"}
                </button>
              </div>
            ))}
          </div>
        ) : null}
        {!loading && !checkingAll && !rows.length ? (
          <div className="dependency-empty-state">
            <EmptyState icon={RefreshCcw} label={packages.length ? "No actionable updates." : "No dependencies enrolled yet."} />
            {packages.length ? <p>{checkedCount ? transitiveUpdateCount ? `${transitiveUpdateCount} transitive update${transitiveUpdateCount === 1 ? "" : "s"} can be inspected in Show All, but should move through their direct parent dependency.` : "Everything currently checked is up to date." : "Run Check All to populate this hub."}</p> : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}

export function DependencyUpdatePanel({
  packages,
  storage,
  onChanged
}: {
  packages: DependencyPackage[];
  storage: DependencyStorageStatus | null;
  onChanged: () => Promise<void>;
}) {
  const [selected, setSelected] = React.useState<DependencyPackage | null>(packages.find(dependencyIsActionableUpdate) ?? packages.find((dependency) => dependency.update_available) ?? packages[0] ?? null);
  React.useEffect(() => {
    setSelected((current) => current && packages.some((dependency) => dependency.id === current.id)
      ? packages.find((dependency) => dependency.id === current.id) ?? current
      : packages.find(dependencyIsActionableUpdate) ?? packages.find((dependency) => dependency.update_available) ?? packages[0] ?? null);
  }, [packages]);
  if (!packages.length) return <div className="empty-state">No enrolled dependencies are linked to this integration yet</div>;
  return (
    <div className="dependency-integration-panel">
      <div className="dependency-package-list">
        {packages.map((dependency) => (
          <button className={selected?.id === dependency.id ? "dependency-package-button active" : "dependency-package-button"} key={dependency.id} onClick={() => setSelected(dependency)} type="button">
            <span>
              <strong>{dependency.package_name}</strong>
              <small>{dependency.current_version || "unknown"}{" -> "}{dependency.latest_version || "unchecked"}</small>
            </span>
            <Badge tone={dependencyUpdateTone(dependency)}>{dependencyUpdateLabel(dependency)}</Badge>
          </button>
        ))}
      </div>
      {selected ? (
        <DependencyUpdateDeepDive dependency={selected} embedded storage={storage} onChanged={onChanged} />
      ) : null}
    </div>
  );
}

export function DependencyUpdateModal({
  dependency,
  storage,
  onClose,
  onChanged
}: {
  dependency: DependencyPackage;
  storage: DependencyStorageStatus | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card dependency-update-modal" role="dialog" aria-modal="true" aria-labelledby="dependency-update-title">
        <div className="modal-header">
          <div>
            <h2 id="dependency-update-title">{dependency.package_name}</h2>
            <p>{dependency.dependant_area} · {dependency.ecosystem}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close"><X size={16} /></button>
        </div>
        <DependencyUpdateDeepDive dependency={dependency} storage={storage} onChanged={onChanged} />
      </div>
    </div>
  );
}

export function DependencyUpdateDeepDive({
  dependency,
  storage,
  embedded = false,
  onChanged
}: {
  dependency: DependencyPackage;
  storage: DependencyStorageStatus | null;
  embedded?: boolean;
  onChanged: () => Promise<void>;
}) {
  const [current, setCurrent] = React.useState(dependency);
  const [analysis, setAnalysis] = React.useState<DependencyAnalysis | null>(dependency.latest_analysis);
  const [backups, setBackups] = React.useState<DependencyBackup[]>([]);
  const [job, setJob] = React.useState<DependencyJob | null>(null);
  const [jobEvents, setJobEvents] = React.useState<DependencyJobEvent[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [confirmAction, setConfirmAction] = React.useState<DependencyConfirmAction | null>(null);
  const jobSocketRef = React.useRef<WebSocket | null>(null);

  const loadBackups = React.useCallback(async () => {
    const result = await api.get<{ backups: DependencyBackup[] }>(`/api/v1/dependency-updates/packages/${dependency.id}/backups`);
    setBackups(result.backups);
  }, [dependency.id]);

  const loadCurrentDependency = React.useCallback(async () => {
    const result = await api.get<{ packages: DependencyPackage[] }>("/api/v1/dependency-updates/packages");
    const next = result.packages.find((candidate) => candidate.id === dependency.id);
    if (!next) return null;
    setCurrent(next);
    setAnalysis(next.latest_analysis);
    return next;
  }, [dependency.id]);

  React.useEffect(() => {
    setCurrent(dependency);
    setAnalysis(dependency.latest_analysis);
    loadBackups().catch(() => undefined);
  }, [dependency, loadBackups]);

  const check = async () => {
    setLoading(true);
    setError("");
    try {
      const next = await api.post<DependencyPackage>(`/api/v1/dependency-updates/packages/${dependency.id}/check`, {});
      setCurrent(next);
      setAnalysis(next.latest_analysis);
      await onChanged();
    } catch (checkError) {
      setError(checkError instanceof Error ? checkError.message : "Unable to check for updates.");
    } finally {
      setLoading(false);
    }
  };

  const analyze = async () => {
    setLoading(true);
    setError("");
    try {
      const next = await api.post<DependencyAnalysis>(`/api/v1/dependency-updates/packages/${dependency.id}/analyze`, {
        target_version: current.latest_version || undefined
      });
      setAnalysis(next);
      await onChanged();
    } catch (analysisError) {
      setError(analysisError instanceof Error ? analysisError.message : "Unable to analyze this update.");
    } finally {
      setLoading(false);
    }
  };

  const closeJobSocket = React.useCallback((socket?: WebSocket) => {
    const target = socket ?? jobSocketRef.current;
    if (!target) return;
    target.onmessage = null;
    target.onerror = null;
    target.onclose = null;
    if (jobSocketRef.current === target) jobSocketRef.current = null;
    if (target.readyState === WebSocket.CONNECTING || target.readyState === WebSocket.OPEN) {
      target.close();
    }
  }, []);

  React.useEffect(() => () => closeJobSocket(), [closeJobSocket]);

  const openJobSocket = React.useCallback((jobId: string) => {
    closeJobSocket();
    const socket = new WebSocket(wsUrl(`/api/v1/dependency-updates/jobs/${jobId}/ws`));
    jobSocketRef.current = socket;
    socket.onmessage = (event) => {
      let parsed: DependencyJobEvent;
      try {
        parsed = JSON.parse(event.data) as DependencyJobEvent;
      } catch {
        return;
      }
      const next = compactDependencyJobEvent(parsed);
      if (next.type === "connection.ready") return;
      setJobEvents((events) => [...events, next].slice(-DEPENDENCY_JOB_EVENT_LIMIT));
      if (next.phase) {
        setJob((currentJob) => currentJob ? { ...currentJob, phase: next.phase || currentJob.phase } : currentJob);
      }
      if (next.type === "completed" || next.type === "failed") {
        setJob((currentJob) => currentJob ? {
          ...currentJob,
          status: next.type === "completed" ? "completed" : "failed",
          phase: next.phase || currentJob.phase,
          error: next.type === "failed" ? next.message || currentJob.error : currentJob.error,
          result: next.result || currentJob.result
        } : currentJob);
        if (next.type === "completed") {
          setCurrent((dependencyState) => ({
            ...dependencyState,
            current_version: dependencyState.latest_version || dependencyState.current_version,
            update_available: false,
            risk_status: "safe"
          }));
        }
        closeJobSocket(socket);
        Promise.all([
          api.get<DependencyJob>(`/api/v1/dependency-updates/jobs/${jobId}`).then(setJob),
          onChanged(),
          loadCurrentDependency(),
          loadBackups()
        ]).catch(() => undefined);
      }
    };
    socket.onerror = () => closeJobSocket(socket);
    socket.onclose = () => {
      if (jobSocketRef.current === socket) jobSocketRef.current = null;
    };
  }, [closeJobSocket, loadBackups, loadCurrentDependency, onChanged]);

  const startApplyUpdate = async () => {
    setLoading(true);
    setError("");
    setJobEvents([]);
    try {
      const nextJob = await api.post<DependencyJob>(`/api/v1/dependency-updates/packages/${dependency.id}/apply`, {
        target_version: current.latest_version || undefined,
        confirmed: true
      });
      setJob(nextJob);
      openJobSocket(nextJob.id);
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "Unable to start update job.");
    } finally {
      setLoading(false);
    }
  };

  const startRestoreBackup = async (backup: DependencyBackup) => {
    setLoading(true);
    setError("");
    setJobEvents([]);
    try {
      const nextJob = await api.post<DependencyJob>(`/api/v1/dependency-updates/backups/${backup.id}/restore`, { confirmed: true });
      setJob(nextJob);
      openJobSocket(nextJob.id);
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "Unable to start restore job.");
    } finally {
      setLoading(false);
    }
  };

  const confirmSelectedAction = async () => {
    const action = confirmAction;
    if (!action) return;
    setConfirmAction(null);
    if (action.kind === "apply") {
      await startApplyUpdate();
    } else {
      await startRestoreBackup(action.backup);
    }
  };

  const updateActionAvailable = Boolean(current.update_available && current.latest_version);
  const checked = Boolean(current.last_checked_at);
  const analysisMatchesTarget = Boolean(analysis && current.latest_version && analysis.target_version === current.latest_version);
  const analysisRequired = updateActionAvailable && !analysisMatchesTarget;
  const breakingBlocked = updateActionAvailable && analysisMatchesTarget && String(analysis?.verdict || "").toLowerCase() === "breaking";
  const applyActionAvailable = updateActionAvailable && dependencyCanApply(current) && !analysisRequired && !breakingBlocked;
  const applyActionTitle = applyActionAvailable
    ? "Apply this update"
    : breakingBlocked
      ? "Breaking updates are blocked until the migration is resolved and analysis is re-run"
      : analysisRequired
        ? "Analyze this target version before applying"
        : updateActionAvailable
          ? "Transitive packages must be updated through their direct dependency"
          : checked
            ? "No update is available to apply"
            : "Check this dependency first";
  const jobActive = job?.status === "queued" || job?.status === "running";
  const jobCompleted = job?.status === "completed";
  const hasExecution = Boolean(job || jobEvents.length);

  return (
    <div className={embedded ? "dependency-update-deep-dive embedded" : "dependency-update-deep-dive"}>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      <div className="dependency-version-strip">
        <div><span>Current</span><strong>{current.current_version || "unknown"}</strong></div>
        <div><span>Latest</span><strong>{current.latest_version || "unchecked"}</strong></div>
        <div><span>Storage</span><strong>{storage?.config_status || "unknown"}</strong></div>
        <Badge tone={dependencyUpdateTone(current)}>{dependencyUpdateLabel(current)}</Badge>
      </div>
      <div className="dependency-update-actions">
        <button className="secondary-button" onClick={check} disabled={loading} type="button">
          <RefreshCcw size={15} /> Check
        </button>
        {jobCompleted ? (
          <button className="secondary-button" disabled type="button">
            <CheckCircle2 size={15} /> Update Complete
          </button>
        ) : (
          <>
            <button className="primary-button" onClick={analyze} disabled={loading || jobActive || !updateActionAvailable} title={updateActionAvailable ? "Analyze this update" : checked ? "No update is available to analyze" : "Check this dependency first"} type="button">
              <Bot size={15} /> Analyze
            </button>
            <button
              className="primary-button"
              onClick={() => setConfirmAction({ kind: "apply" })}
              disabled={loading || jobActive || !applyActionAvailable}
              title={applyActionTitle}
              type="button"
            >
              <Play size={15} /> Proceed with Update
            </button>
          </>
        )}
      </div>
      {updateActionAvailable && !dependencyCanApply(current) ? (
        <div className="dependency-check-summary warning">This package is transitive. Review the analysis here, then update the owning direct dependency or lockfile.</div>
      ) : null}
      {updateActionAvailable && dependencyCanApply(current) && analysisRequired ? (
        <div className="dependency-check-summary warning">Run analysis for {current.latest_version} before applying so IACS can review changelog risk against local usage.</div>
      ) : null}
      {breakingBlocked ? (
        <div className="dependency-check-summary danger">IACS blocked this update because the latest analysis marked it Breaking. Resolve the proposed migration, run the build checks, then re-run analysis before applying.</div>
      ) : null}
      {hasExecution ? (
        <DependencyLiveExecution
          events={jobEvents}
          job={job}
          onRetry={() => setConfirmAction({ kind: "apply" })}
          retryDisabled={loading || !applyActionAvailable || jobActive}
        />
      ) : (
        <>
          <section className="dependency-analysis-panel">
            <div className="dependency-panel-title">
              <strong>LLM Analysis</strong>
              {analysis ? <Badge tone={riskTone(analysis.verdict)}>{titleCase(String(analysis.verdict))}</Badge> : <Badge tone="gray">Not Analyzed</Badge>}
            </div>
            {analysis ? (
              <DependencyAnalysisReview analysis={analysis} />
            ) : (
              <div className="empty-state">Run analysis to review changelog risk and local code usage.</div>
            )}
          </section>
          {analysis?.suggested_diff ? <DependencySuggestedFixes diff={analysis.suggested_diff} /> : null}
        </>
      )}
      <section className="dependency-backup-panel">
        <div className="dependency-panel-title">
          <strong>Backup History</strong>
          <Badge tone="gray">{backups.length}</Badge>
        </div>
        {backups.length ? backups.map((backup) => (
          <div className="dependency-backup-row" key={backup.id}>
            <div>
              <strong>{backup.version || "unknown"} · {backup.reason}</strong>
              <span>{formatDate(backup.created_at)} · {formatFileSize(backup.size_bytes)}</span>
            </div>
            <button className="secondary-button" onClick={() => setConfirmAction({ kind: "restore", backup })} disabled={loading || jobActive} type="button">
              <ShieldCheck size={15} /> Restore
            </button>
          </div>
        )) : <div className="empty-state">No backups have been created for this package.</div>}
      </section>
      {confirmAction ? (
        <DependencyUpdateConfirmModal
          action={confirmAction}
          dependency={current}
          loading={loading}
          onCancel={() => setConfirmAction(null)}
          onConfirm={confirmSelectedAction}
        />
      ) : null}
    </div>
  );
}

export function DependencyAnalysisReview({ analysis }: { analysis: DependencyAnalysis }) {
  const summaryLines = analysis.summary_markdown.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return (
    <div className="dependency-analysis-review">
      <section className="dependency-analysis-card">
        <h4>Summary</h4>
        <div className="dependency-analysis-lines">
          {summaryLines.map((line, index) => (
            <ProtectAnalysisLine line={line} key={`${analysis.id}-summary-${index}`} />
          ))}
        </div>
      </section>
      <section className="dependency-analysis-card">
        <div className="dependency-verification-title">
          <h4>Verification Plan</h4>
          <Badge tone="blue">Guided</Badge>
        </div>
        <p>IACS runs install, build, and health checks during Live Execution. These LLM-generated steps are the remaining checks to confirm the affected feature still behaves correctly.</p>
        <div className="dependency-verification-list">
          {analysis.verification_steps.length ? analysis.verification_steps.map((step, index) => {
            const details = verificationStepDetails(step);
            return (
              <div className="dependency-verification-step" key={`${analysis.id}-verify-${index}`}>
                <Badge tone={details.tone}>{details.label}</Badge>
                <span>{renderInlineMarkdown(details.text)}</span>
              </div>
            );
          }) : (
            <span className="dependency-muted">No extra verification steps were suggested.</span>
          )}
        </div>
      </section>
    </div>
  );
}

export function DependencySuggestedFixes({ diff }: { diff: string }) {
  const files = React.useMemo(() => parseSuggestedDiff(diff), [diff]);
  return (
    <section className="dependency-fix-panel">
      <div className="dependency-panel-title">
        <div>
          <strong>Proposed Fixes</strong>
          <p>LLM-generated patch guidance. IACS applies package-manager changes automatically when you proceed.</p>
        </div>
        <Badge tone="gray">Draft</Badge>
      </div>
      <div className="dependency-fix-files">
        {files.map((file) => (
          <details className="dependency-fix-file" key={file.file}>
            <summary>
              <span>
                <strong>{file.file}</strong>
                <small>{file.added} added · {file.removed} removed</small>
              </span>
              <span className="dependency-fix-toggle">Show patch</span>
            </summary>
            <pre>{file.lines.join("\n")}</pre>
          </details>
        ))}
      </div>
    </section>
  );
}

export function DependencyLiveExecution({
  events,
  job,
  onRetry,
  retryDisabled
}: {
  events: DependencyJobEvent[];
  job: DependencyJob | null;
  onRetry: () => void;
  retryDisabled: boolean;
}) {
  const progress = dependencyJobProgress(job, events);
  const failed = progress.status === "failed";
  const completed = progress.status === "completed";
  const diagnosis = dependencyJobDiagnosis(job);
  const rollbackSummary = dependencyRollbackSummary(job);
  const terminalRef = React.useRef<HTMLDivElement | null>(null);
  const latestEvent = events[events.length - 1];
  const latestEventKey = latestEvent
    ? `${latestEvent.created_at || ""}:${latestEvent.type}:${latestEvent.phase || ""}:${latestEvent.message || ""}`
    : "empty";

  React.useEffect(() => {
    const terminal = terminalRef.current;
    if (!terminal) return undefined;
    const frame = window.requestAnimationFrame(() => {
      terminal.scrollTop = terminal.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [latestEventKey, progress.status]);

  return (
    <section className="dependency-terminal-panel">
      <div className="dependency-panel-title">
        <div>
          <strong>Live Execution</strong>
          <p>{progress.label}</p>
        </div>
        {job ? <Badge tone={failed ? "red" : completed ? "green" : "blue"}>{titleCase(job.status)}</Badge> : null}
      </div>
      <div className={failed ? "dependency-progress failed" : completed ? "dependency-progress completed" : "dependency-progress"}>
        <div className="dependency-progress-track" aria-label={`Update progress ${progress.percent}%`}>
          <span style={{ width: `${progress.percent}%` }} />
        </div>
        <div className="dependency-progress-meta">
          <span>{progress.percent}%</span>
          <span>{progress.label}</span>
        </div>
      </div>
      {failed ? (
        <div className="dependency-job-resolution error">
          <strong>{diagnosis?.title || "Update did not complete."}</strong>
          <p>{diagnosis?.summary || "IACS could not complete this update."}</p>
          <p>{rollbackSummary}</p>
          {diagnosis?.affected_packages.length ? (
            <div className="dependency-recovery-pills">
              {diagnosis.affected_packages.map((name) => <Badge tone="amber" key={name}>{name}</Badge>)}
            </div>
          ) : null}
          {diagnosis?.actions.length ? (
            <div className="dependency-recovery-list">
              {diagnosis.actions.map((action, index) => (
                <div key={`${diagnosis.category}-action-${index}`}>
                  <CheckCircle2 size={14} />
                  <span>{action}</span>
                </div>
              ))}
            </div>
          ) : null}
          {diagnosis?.retry_recommendation ? <p>{diagnosis.retry_recommendation}</p> : null}
          {diagnosis?.command ? <code className="dependency-failed-command">{diagnosis.command}</code> : null}
          <button className="secondary-button" onClick={onRetry} disabled={retryDisabled} type="button">
            <RefreshCcw size={15} /> Retry Update
          </button>
        </div>
      ) : null}
      <div className="log-console dependency-terminal" ref={terminalRef}>
        {events.length ? events.map((event, index) => (
          <div className="log-line" key={`${event.created_at}-${event.type}-${index}`}>
            <time>{formatDependencyLogTime(event.created_at)}</time>
            <strong>{dependencyLogTypeLabel(event.type)}</strong>
            <code>{event.message || event.phase || ""}</code>
          </div>
        )) : (
          <div className="log-line">
            <time>now</time>
            <strong>queued</strong>
            <code>Waiting for job output...</code>
          </div>
        )}
      </div>
    </section>
  );
}

export function DependencyUpdateConfirmModal({
  action,
  dependency,
  loading,
  onCancel,
  onConfirm
}: {
  action: DependencyConfirmAction;
  dependency: DependencyPackage;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const isApply = action.kind === "apply";
  return (
    <div className="modal-backdrop nested-modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="dependency-update-confirm-title">
        <div className="gate-confirm-title">
          <span className="gate-confirm-icon">
            {isApply ? <Play size={19} /> : <ShieldCheck size={19} />}
          </span>
          <div>
            <h2 id="dependency-update-confirm-title">{isApply ? "Proceed with dependency update?" : "Restore dependency backup?"}</h2>
            <p>
              {isApply
                ? `${dependency.package_name} will update from ${dependency.current_version || "unknown"} to ${dependency.latest_version || "the selected version"}. IACS will create an offline backup first, stream progress, verify the build, and roll back automatically if the update cannot be completed.`
                : `Restore backup ${action.backup.id}. IACS will validate the archive checksum, restore manifests, and run verification afterwards.`}
            </p>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading} type="button">Cancel</button>
          <button className="primary-button" onClick={onConfirm} disabled={loading} type="button">
            {isApply ? <Play size={15} /> : <ShieldCheck size={15} />}
            {loading ? "Starting..." : isApply ? "Start Update" : "Restore Backup"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function DependencyStoragePanel({ storage, onChanged }: { storage: DependencyStorageStatus | null; onChanged: () => Promise<void> }) {
  const [mode, setMode] = React.useState(storage?.mode || "local");
  const [source, setSource] = React.useState(storage?.mount_source || "");
  const [options, setOptions] = React.useState("");
  const [optionsTouched, setOptionsTouched] = React.useState(false);
  const [minFree, setMinFree] = React.useState(String(storage?.min_free_bytes ?? 1073741824));
  const [retentionDays, setRetentionDays] = React.useState(String(storage?.retention_days || ""));
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  React.useEffect(() => {
    setMode(storage?.mode || "local");
    setSource(storage?.mount_source || "");
    setOptions("");
    setOptionsTouched(false);
    setMinFree(String(storage?.min_free_bytes ?? 1073741824));
    setRetentionDays(String(storage?.retention_days || ""));
  }, [storage]);

  const savedMountOptions = Boolean(storage?.mount_options_configured);
  const mountOptionsHint = mode === "local"
    ? "Remote mount options are not used for local backup storage."
    : savedMountOptions && !optionsTouched
      ? "Sensitive options are saved and hidden. Enter a new value to replace them."
      : optionsTouched && options.trim()
        ? "New mount options will replace the saved sensitive value."
        : optionsTouched
          ? "Saved mount options will be cleared when you save."
          : "Optional Docker mount options for this remote share.";

  const saveStorage = async () => {
    setSaving(true);
    setError("");
    try {
      const payload: {
        mode: string;
        mount_source: string;
        mount_options?: string;
        retention_days: string;
        min_free_bytes: number;
      } = {
        mode,
        mount_source: mode === "local" ? "" : source,
        retention_days: retentionDays,
        min_free_bytes: Number(minFree) || 0
      };
      if (mode === "local" || optionsTouched) {
        payload.mount_options = mode === "local" ? "" : options;
      }
      await api.post("/api/v1/dependency-updates/storage/config", payload);
      await onChanged();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save storage configuration.");
    } finally {
      setSaving(false);
    }
  };

  const validate = async () => {
    setSaving(true);
    setError("");
    try {
      await api.post("/api/v1/dependency-updates/storage/validate", {});
      await onChanged();
    } catch (validateError) {
      setError(validateError instanceof Error ? validateError.message : "Unable to validate storage.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card dependency-storage-panel">
      <div className="dependency-storage-summary">
        <div>
          <h2>Backup Storage</h2>
          <p>{storage?.detail || "Configure where offline update backups are stored."}</p>
        </div>
        <Badge tone={storage?.config_status === "pending_reboot" ? "amber" : storage?.ok ? "green" : "red"}>
          {storage?.config_status === "pending_reboot" ? "Reboot Required" : storage?.ok ? "Ready" : "Needs Attention"}
        </Badge>
      </div>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      <div className="dependency-storage-grid">
        <label className="field">
          <span>Mode</span>
          <select value={mode} onChange={(event) => setMode(event.target.value)}>
            <option value="local">Local bind mount</option>
            <option value="nfs">Host-mounted NFS path</option>
            <option value="samba">Host-mounted Samba/CIFS path</option>
          </select>
        </label>
        <label className="field">
          <span>Mount source</span>
          <input value={source} onChange={(event) => setSource(event.target.value)} placeholder={mode === "local" ? "./data/backend/dependency-update-backups" : mode === "nfs" ? "/mnt/iacs-update-backups" : "/mnt/iacs-update-backups"} disabled={mode === "local"} />
        </label>
        <label className="field">
          <span>Mount options</span>
          <input
            value={options}
            onChange={(event) => {
              setOptions(event.target.value);
              setOptionsTouched(true);
            }}
            placeholder={mode === "local" ? "not used for local mode" : mode === "samba" ? "username=iacs,password=...,vers=3.0,rw" : "addr=nas.local,rw"}
            disabled={mode === "local"}
          />
          <small className="field-hint">{mountOptionsHint}</small>
        </label>
        {mode !== "local" && savedMountOptions ? (
          <div className="dependency-storage-secret-controls">
            <button
              className="secondary-button"
              onClick={() => {
                setOptions("");
                setOptionsTouched(true);
              }}
              disabled={saving}
              type="button"
            >
              Clear Saved Options
            </button>
          </div>
        ) : null}
        <label className="field">
          <span>Minimum free bytes</span>
          <input value={minFree} onChange={(event) => setMinFree(event.target.value)} inputMode="numeric" />
        </label>
        <label className="field">
          <span>Retention days</span>
          <input value={retentionDays} onChange={(event) => setRetentionDays(event.target.value)} inputMode="numeric" placeholder="optional" />
        </label>
      </div>
      <div className="dependency-storage-meta">
        <span>Active root: <code>{storage?.backup_root || "/app/update-backups"}</code></span>
        <span>Free: <strong>{formatFileSize(storage?.free_bytes ?? 0)}</strong></span>
      </div>
      <div className="dependency-update-actions">
        <button className="secondary-button" onClick={validate} disabled={saving} type="button">
          <CheckCircle2 size={15} /> Validate
        </button>
        <button className="primary-button" onClick={saveStorage} disabled={saving} type="button">
          <Save size={15} /> Save Storage Config
        </button>
      </div>
      <p className="dependency-storage-note">Changing NFS/Samba storage writes a generated Compose override and requires a host reboot or full Compose recreation before the mount changes.</p>
    </section>
  );
}

export function riskTone(value: string | null | undefined): BadgeTone {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "breaking" || normalized === "red" || normalized === "error") return "red";
  if (normalized === "warning" || normalized === "unknown" || normalized === "amber") return "amber";
  if (normalized === "safe" || normalized === "green") return "green";
  return "gray";
}

export function UnifiProtectUpdatesPanel({
  status,
  onChanged
}: {
  status: UnifiProtectUpdateStatus | null;
  onChanged: () => Promise<void>;
}) {
  const [updateStatus, setUpdateStatus] = React.useState<UnifiProtectUpdateStatus | null>(status);
  const [targetVersion, setTargetVersion] = React.useState(status?.latest_version ?? "");
  const [analysis, setAnalysis] = React.useState<UnifiProtectUpdateAnalysis | null>(null);
  const [backups, setBackups] = React.useState<UnifiProtectBackup[]>([]);
  const [result, setResult] = React.useState<UnifiProtectUpdateApplyResult | null>(null);
  const [confirmAction, setConfirmAction] = React.useState<ProtectUpdateConfirmAction | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  const loadUpdateData = React.useCallback(async () => {
    setError("");
    try {
      const [nextStatus, backupResult] = await Promise.all([
        api.get<UnifiProtectUpdateStatus>("/api/v1/integrations/unifi-protect/update/status"),
        api.get<{ backups: UnifiProtectBackup[] }>("/api/v1/integrations/unifi-protect/backups")
      ]);
      setUpdateStatus(nextStatus);
      setTargetVersion((current) => current || nextStatus.latest_version);
      setBackups(backupResult.backups);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load UniFi Protect update data.");
    }
  }, []);

  React.useEffect(() => {
    loadUpdateData().catch(() => undefined);
  }, [loadUpdateData]);

  const analyze = async () => {
    setLoading(true);
    setError("");
    setAnalysis(null);
    try {
      setAnalysis(await api.post<UnifiProtectUpdateAnalysis>("/api/v1/integrations/unifi-protect/update/analyze", {
        target_version: targetVersion || undefined
      }));
    } catch (analysisError) {
      setError(analysisError instanceof Error ? analysisError.message : "Unable to analyze the update.");
    } finally {
      setLoading(false);
    }
  };

  const createBackup = async () => {
    setLoading(true);
    setError("");
    try {
      const backup = await api.post<UnifiProtectBackup>("/api/v1/integrations/unifi-protect/backups", {});
      setBackups((current) => [backup, ...current]);
    } catch (backupError) {
      setError(backupError instanceof Error ? backupError.message : "Unable to create backup.");
    } finally {
      setLoading(false);
    }
  };

  const applyUpdate = async () => {
    if (!analysis) {
      setError("Analyze the release notes before applying the update.");
      return;
    }
    setConfirmAction({ kind: "apply" });
  };

  const runApplyUpdate = async () => {
    if (!analysis) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const applied = await api.post<UnifiProtectUpdateApplyResult>("/api/v1/integrations/unifi-protect/update/apply", {
        target_version: analysis.target_version,
        confirmed: true
      });
      setResult(applied);
      await loadUpdateData();
      await onChanged();
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "Unable to apply the update.");
      await loadUpdateData();
    } finally {
      setLoading(false);
    }
  };

  const restore = async (backup: UnifiProtectBackup) => {
    setConfirmAction({ kind: "restore", backup });
  };

  const deleteBackup = async (backup: UnifiProtectBackup) => {
    setConfirmAction({ kind: "delete", backup });
  };

  const runRestore = async (backup: UnifiProtectBackup) => {
    setLoading(true);
    setError("");
    try {
      await api.post(`/api/v1/integrations/unifi-protect/backups/${encodeURIComponent(backup.id)}/restore`, {});
      await loadUpdateData();
      await onChanged();
    } catch (restoreError) {
      setError(restoreError instanceof Error ? restoreError.message : "Unable to restore backup.");
    } finally {
      setLoading(false);
    }
  };

  const runDeleteBackup = async (backup: UnifiProtectBackup) => {
    setLoading(true);
    setError("");
    try {
      await api.delete(`/api/v1/integrations/unifi-protect/backups/${encodeURIComponent(backup.id)}`);
      setBackups((current) => current.filter((item) => item.id !== backup.id));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete backup.");
    } finally {
      setLoading(false);
    }
  };

  const currentVersion = updateStatus?.current_version ?? status?.current_version ?? "unknown";
  const latestVersion = updateStatus?.latest_version ?? status?.latest_version ?? "unknown";
  const updateAvailable = Boolean(updateStatus?.update_available);
  const updateApplied = Boolean(result?.ok);

  return (
    <div className="protect-update-panel">
        {error ? <div className="auth-error inline-error">{error}</div> : null}

        <div className="protect-update-summary">
          <div>
            <span>Current</span>
            <strong>{currentVersion}</strong>
          </div>
          <div>
            <span>Latest</span>
            <strong>{latestVersion}</strong>
          </div>
          <Badge tone={updateAvailable ? "amber" : "green"}>{updateAvailable ? "Update Available" : "Up To Date"}</Badge>
        </div>

        <div className="protect-update-actions">
          <label className="field protect-version-field">
            <span>Target version</span>
            <input value={targetVersion} onChange={(event) => setTargetVersion(event.target.value)} placeholder={latestVersion} />
          </label>
          <button className="secondary-button" onClick={createBackup} disabled={loading} type="button">
            <Download size={15} /> Backup
          </button>
        </div>

        <div className="protect-review-cta">
          <button className="primary-button" onClick={analyze} disabled={loading} type="button">
            <Bot size={15} /> {loading && !analysis ? "Reviewing..." : "Review Changes to Verify Compatibility"}
          </button>
        </div>

        {analysis ? (
          <section className="protect-update-analysis">
            <div className="protect-update-analysis-head">
              <div>
                <strong>AI Review</strong>
                <span>{analysis.provider} · {analysis.current_version} to {analysis.target_version}</span>
              </div>
              {analysis.release_notes.html_url ? <a href={analysis.release_notes.html_url} target="_blank" rel="noreferrer">Release notes</a> : null}
            </div>
            <ProtectAnalysisReview analysis={analysis.analysis} />
            <button className={updateApplied ? "secondary-button full" : "primary-button full"} onClick={applyUpdate} disabled={loading || updateApplied} type="button">
              {updateApplied ? <CheckCircle2 size={15} /> : <RefreshCcw size={15} />}
              {updateApplied ? "Update Complete" : loading ? "Applying..." : "Apply Update & Verify"}
            </button>
          </section>
        ) : (
          <div className="empty-state">Run analysis before applying a UniFi Protect package update</div>
        )}

        {result ? (
          <div className="protect-update-result">
            <CheckCircle2 size={17} />
            <div>
              <strong>Updated to {result.current_version}</strong>
              <span>{result.verification.camera_count ?? 0} cameras verified, sample snapshot {result.verification.snapshot_bytes ?? 0} bytes. Backup {result.backup.id} was created first.</span>
            </div>
          </div>
        ) : null}

        <section className="protect-backup-panel">
          <div className="protect-backup-title">
            <strong>Backups</strong>
            <span>Encrypted integration settings and package state.</span>
          </div>
          {backups.length ? backups.map((backup) => (
            <div className="protect-backup-row" key={backup.id}>
              <div>
                <strong>{backup.reason}</strong>
                <span>{formatDate(backup.created_at)} · package {backup.package_version} · {backup.settings_count} settings</span>
              </div>
              <a className="icon-button" href={backup.download_url} aria-label={`Download backup ${backup.id}`}>
                <Download size={14} />
              </a>
              <button className="icon-button danger" onClick={() => deleteBackup(backup)} disabled={loading} type="button" aria-label={`Delete backup ${backup.id}`}>
                <Trash2 size={14} />
              </button>
              <button className="secondary-button" onClick={() => restore(backup)} disabled={loading} type="button">
                Restore
              </button>
            </div>
          )) : (
            <div className="empty-state">No UniFi Protect backups yet</div>
          )}
        </section>

        {confirmAction ? (
          <ProtectUpdateConfirmModal
            action={confirmAction}
            loading={loading}
            onCancel={() => setConfirmAction(null)}
            onConfirm={async () => {
              const action = confirmAction;
              setConfirmAction(null);
              if (action.kind === "apply") {
                await runApplyUpdate();
              } else if (action.kind === "restore") {
                await runRestore(action.backup);
              } else {
                await runDeleteBackup(action.backup);
              }
            }}
          />
        ) : null}
    </div>
  );
}

export function ProtectAnalysisReview({ analysis }: { analysis: string }) {
  const sections = parseProtectAnalysisSections(analysis);
  const riskSection = findAnalysisSection(sections, "risk level");
  const recommendationSection = findAnalysisSection(sections, "recommendation");
  const risk = firstMeaningfulAnalysisLine(riskSection?.body) || "Review";
  const recommendation = firstMeaningfulAnalysisLine(recommendationSection?.body) || "Review the notes before applying.";
  const riskTone = analysisTone(risk);
  const recommendationTone = analysisTone(recommendation);
  const detailSections = sections.filter((section) => !["risk level", "recommendation"].includes(section.title.toLowerCase()));

  return (
    <div className="protect-analysis-review">
      <div className="protect-analysis-summary">
        <div className={`protect-analysis-callout ${riskTone}`}>
          <span>Risk Level</span>
          <strong>{risk}</strong>
        </div>
        <div className={`protect-analysis-callout ${recommendationTone}`}>
          <span>Recommendation</span>
          <strong>{recommendation}</strong>
        </div>
      </div>
      <div className="protect-analysis-sections">
        {detailSections.length ? detailSections.map((section) => (
          <section className="protect-analysis-section" key={section.title}>
            <h4>{section.title}</h4>
            <div className="protect-analysis-lines">
              {section.body.map((line, index) => (
                <ProtectAnalysisLine line={line} key={`${section.title}-${index}`} />
              ))}
            </div>
          </section>
        )) : (
          <section className="protect-analysis-section">
            <h4>Review</h4>
            <div className="protect-analysis-lines">
              {analysis.split(/\r?\n/).map((line, index) => <ProtectAnalysisLine line={line} key={index} />)}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

export function ProtectAnalysisLine({ line }: { line: string }) {
  if (!line.trim()) return null;
  const leadingSpaces = line.match(/^\s*/)?.[0].length ?? 0;
  const cleanLine = line.trim().replace(/^[-*]\s+/, "");
  const isBullet = /^\s*[-*]\s+/.test(line);
  return (
    <div className={isBullet ? "protect-analysis-line bullet" : "protect-analysis-line"} style={isBullet ? { "--analysis-indent": String(Math.min(leadingSpaces / 2, 3)) } as React.CSSProperties : undefined}>
      {isBullet ? <span className="analysis-dot" aria-hidden="true" /> : null}
      <span>{renderInlineMarkdown(cleanLine)}</span>
    </div>
  );
}

export type ProtectAnalysisSection = {
  title: string;
  body: string[];
};

export function parseProtectAnalysisSections(markdown: string): ProtectAnalysisSection[] {
  const sections: ProtectAnalysisSection[] = [];
  let current: ProtectAnalysisSection | null = null;
  for (const line of markdown.split(/\r?\n/)) {
    const heading = line.match(/^#{1,3}\s+(.+)$/);
    if (heading) {
      current = { title: cleanInlineMarkdown(heading[1]), body: [] };
      sections.push(current);
      continue;
    }
    if (!current) {
      current = { title: "Review", body: [] };
      sections.push(current);
    }
    current.body.push(line);
  }
  return sections.filter((section) => section.title || section.body.some((line) => line.trim()));
}

export function findAnalysisSection(sections: ProtectAnalysisSection[], title: string) {
  return sections.find((section) => section.title.toLowerCase().includes(title));
}

export function firstMeaningfulAnalysisLine(lines: string[] | undefined) {
  return cleanInlineMarkdown(lines?.find((line) => line.trim()) ?? "");
}

export function cleanInlineMarkdown(value: string) {
  return value.replace(/^[-*]\s+/, "").replace(/\*\*/g, "").replace(/`/g, "").trim();
}

export function analysisTone(value: string): "green" | "amber" | "red" | "blue" {
  const normalized = value.toLowerCase();
  if (normalized.includes("no-go") || normalized.includes("no go") || normalized.includes("high") || normalized.includes("critical")) return "red";
  if (normalized.includes("medium") || normalized.includes("caution") || normalized.includes("manual")) return "amber";
  if (normalized.includes("go") || normalized.includes("low")) return "green";
  return "blue";
}

export function renderInlineMarkdown(value: string) {
  const parts = value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={index}>{part.slice(1, -1)}</code>;
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
}

export function ProtectUpdateConfirmModal({
  action,
  loading,
  onCancel,
  onConfirm
}: {
  action: ProtectUpdateConfirmAction;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => Promise<void>;
}) {
  const isApply = action.kind === "apply";
  const isDelete = action.kind === "delete";
  return (
    <div className="modal-backdrop nested-modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="protect-update-confirm-title">
        <div className="gate-confirm-title">
          <span className="gate-confirm-icon">
            {isApply ? <RefreshCcw size={19} /> : isDelete ? <Trash2 size={19} /> : <ShieldCheck size={19} />}
          </span>
          <div>
            <h2 id="protect-update-confirm-title">
              {isApply ? "Apply UniFi Protect update?" : isDelete ? "Delete UniFi Protect backup?" : "Restore UniFi Protect backup?"}
            </h2>
            <p>
              {isApply
                ? "A backup will be created first, then the package update will be applied and cameras verified."
                : isDelete
                  ? `Permanently delete backup ${action.backup.id}. This cannot be restored later.`
                  : `Restore backup ${action.backup.id} and verify the integration afterwards.`}
            </p>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" onClick={onCancel} disabled={loading} type="button">Cancel</button>
          <button className={isDelete ? "danger-button" : "primary-button"} onClick={onConfirm} disabled={loading} type="button">
            {isApply ? <RefreshCcw size={15} /> : isDelete ? <Trash2 size={15} /> : <ShieldCheck size={15} />}
            {loading ? "Working..." : isApply ? "Apply Update" : isDelete ? "Delete Backup" : "Restore Backup"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function IntegrationModal({
  definition,
  initialTab,
  values,
  loading,
  protectCameras,
  protectError,
  protectLoading,
  protectStatus,
  protectUpdateStatus,
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
  schedules,
  onClose,
  onDiscordChanged,
  onWhatsAppChanged,
  onICloudChanged,
  onProtectUpdateChanged,
  onProtectRefresh,
  onSettingsChanged,
  onSaved
}: {
  definition: IntegrationDefinition;
  initialTab: ProtectIntegrationTab;
  values: SettingsMap;
  loading: boolean;
  protectCameras?: UnifiProtectCamera[];
  protectError?: string;
  protectLoading?: boolean;
  protectStatus?: UnifiProtectStatus | null;
  protectUpdateStatus?: UnifiProtectUpdateStatus | null;
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
  schedules: Schedule[];
  onClose: () => void;
  onDiscordChanged?: () => Promise<void>;
  onWhatsAppChanged?: () => Promise<void>;
  onICloudChanged?: () => Promise<void>;
  onProtectUpdateChanged?: () => Promise<void>;
  onProtectRefresh?: () => Promise<void>;
  onSettingsChanged: () => Promise<void>;
  onSaved: (updates: Record<string, unknown>) => Promise<void>;
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
  const isHomeAssistant = definition.key === "home_assistant";
  const isApprise = definition.key === "apprise";
  const isUnifiProtect = definition.key === "unifi_protect";
  const isICloudCalendar = definition.key === "icloud_calendar";
  const isDiscord = definition.key === "discord";
  const isWhatsApp = definition.key === "whatsapp";
  const hasDependencyUpdates = dependencyPackages.length > 0;

  React.useEffect(() => {
    setForm(integrationInitialValues(definition, values));
    setActiveTab(initialTab);
    setFeedback(null);
    setHaDiscovery(null);
    setHaDiscoveryError("");
    setAppriseUrls([]);
  }, [definition.key, initialTab]);

  const update = (key: string, value: string) => setForm((current) => ({ ...current, [key]: value }));

  const loadHomeAssistantDiscovery = React.useCallback(async () => {
    if (!isHomeAssistant) return;
    setHaDiscoveryLoading(true);
    setHaDiscoveryError("");
    try {
      const discovery = await api.get<HomeAssistantDiscovery>("/api/v1/integrations/home-assistant/entities");
      setHaDiscovery(discovery);
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
      const result = await api.get<{ urls: AppriseUrlSummary[] }>("/api/v1/integrations/apprise/urls");
      setAppriseUrls(result.urls);
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

  const testConnection = async () => {
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
      const request = api.post<{ ok: boolean; message: string }>("/api/v1/settings/test", {
        integration: definition.key,
        values: coerceSettingsPayload(form)
      });
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
        await api.post("/api/v1/integrations/discord/test", {
          channel_id: form.discord_default_notification_channel_id || undefined,
          message: "This is a test Discord notification from API & Integrations."
        });
      } else if (isWhatsApp) {
        await api.post("/api/v1/integrations/whatsapp/test", {
          message: "This is a test WhatsApp notification from API & Integrations.",
          values: coerceSettingsPayload(form)
        });
      } else {
        await api.post("/api/v1/integrations/notifications/test", {
          subject: "IACS test notification",
          severity: "info",
          message: "This is a test notification from API & Integrations."
        });
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
    setSaving(true);
    setFeedback(null);
    try {
      await onSaved(coerceSettingsPayload(form));
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
            schedules={schedules}
          />
        ) : isApprise ? (
          <AppriseSettingsFields
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
          <div className="settings-form-grid">
            {definition.fields.map((field) => (
              <SettingField
                field={field}
                key={field.key}
                isConfiguredSecret={secretSettingKeys.has(field.key) && Boolean(values[field.key])}
                value={form[field.key] ?? ""}
                onChange={(value) => update(field.key, value)}
              />
            ))}
          </div>
        )}
        {feedback ? <IntegrationFeedbackPanel feedback={feedback} /> : null}
        <div className="modal-actions">
          {isApprise || isDiscord || isWhatsApp ? (
            <button className="secondary-button" onClick={sendTestNotification} disabled={sendingTest} type="button">
              <Send size={15} /> {sendingTest ? "Sending..." : "Send Test"}
            </button>
          ) : null}
          <button className="secondary-button" onClick={testConnection} disabled={testing} type="button">
            {testing ? "Testing..." : "Test Connection"}
          </button>
          {isApprise ? (
            <button className="primary-button" onClick={onClose} type="button">Done</button>
          ) : (
            <button className="primary-button" disabled={saving} type="submit">
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

export function ICloudCalendarModal({
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
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const activeAccounts = payload.accounts.filter((account) => account.is_active);
  const latestRun = payload.recent_sync_runs[0] ?? null;
  const hasAttention = activeAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));

  const resetAddFlow = () => {
    setAdding(false);
    setStep("credentials");
    setAppleId("");
    setPassword("");
    setCode("");
    setHandshakeId("");
    setHandshakeAppleId("");
  };

  const startAuth = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await api.post<ICloudAuthStartResponse>("/api/v1/integrations/icloud-calendar/accounts/auth/start", {
        apple_id: appleId.trim(),
        password
      });
      setPassword("");
      if (result.status === "requires_2fa" && result.handshake_id) {
        setHandshakeId(result.handshake_id);
        setHandshakeAppleId(result.apple_id || appleId.trim());
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
          title: "iCloud Calendar connected",
          detail: `${result.account?.display_name || appleId.trim()} is ready for calendar sync.`
        });
      }
    } catch (authError) {
      setFeedback({
        tone: "error",
        title: "Unable to connect iCloud Calendar",
        detail: authError instanceof Error ? authError.message : "Unable to connect iCloud Calendar."
      });
    } finally {
      setSubmitting(false);
    }
  };

  const verifyCode = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!handshakeId) return;
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await api.post<ICloudAuthVerifyResponse>("/api/v1/integrations/icloud-calendar/accounts/auth/verify", {
        handshake_id: handshakeId,
        code: code.trim()
      });
      resetAddFlow();
      await onChanged();
      setFeedback({
        tone: "success",
        title: "iCloud Calendar connected",
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
      const run = await api.post<ICloudCalendarSyncRun>("/api/v1/integrations/icloud-calendar/sync");
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
      await api.delete<ICloudCalendarAccount>(`/api/v1/integrations/icloud-calendar/accounts/${account.id}`);
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
        <button className="primary-button" onClick={() => setAdding((current) => !current)} disabled={submitting || syncing} type="button">
          <Plus size={15} /> {adding ? "Close Add Account" : "Add Account"}
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
              <Key size={17} />
              <div>
                <strong>Add iCloud account</strong>
                <span>Enter the Apple ID details once; only the trusted session is stored.</span>
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
                {submitting ? "Connecting..." : "Connect"}
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
                <button
                  aria-label={`Remove ${account.display_name}`}
                  className="icon-button danger"
                  disabled={removingId === account.id}
                  onClick={() => removeAccount(account)}
                  type="button"
                >
                  {removingId === account.id ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
                </button>
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

export function icloudAccountStatusLabel(status: string) {
  if (status === "requires_reauth") return "Reconnect";
  if (status === "connected") return "Connected";
  if (status === "error") return "Error";
  if (status === "removed") return "Removed";
  return titleCase(status || "unknown");
}

export function icloudAccountStatusTone(status: string): BadgeTone {
  if (status === "connected") return "green";
  if (status === "requires_reauth") return "amber";
  if (status === "error") return "red";
  return "gray";
}

export function icloudSyncRunSummary(run: ICloudCalendarSyncRun) {
  const changes = [
    `${run.events_matched} matched`,
    `${run.passes_created} created`,
    `${run.passes_updated} updated`,
    `${run.passes_cancelled} cancelled`,
    `${run.passes_skipped} skipped`
  ];
  return `${run.account_count} account${run.account_count === 1 ? "" : "s"} scanned, ${run.events_scanned} event${run.events_scanned === 1 ? "" : "s"} read, ${changes.join(", ")}.`;
}

export function formatOptionalDate(value: string | null | undefined) {
  return value ? formatDate(value) : "Pending";
}

export function IntegrationFeedbackPanel({ feedback }: { feedback: IntegrationFeedback }) {
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

export function AppriseSettingsFields({
  loading,
  urls,
  onChanged,
  onError
}: {
  loading: boolean;
  urls: AppriseUrlSummary[];
  onChanged: (urls: AppriseUrlSummary[]) => Promise<void>;
  onError: (error: string) => void;
}) {
  const [adding, setAdding] = React.useState(false);
  const [newUrl, setNewUrl] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  const reload = async () => {
    const result = await api.get<{ urls: AppriseUrlSummary[] }>("/api/v1/integrations/apprise/urls");
    await onChanged(result.urls);
  };

  const addUrl = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!newUrl.trim()) return;
    setSubmitting(true);
    try {
      const result = await api.post<{ urls: AppriseUrlSummary[] }>("/api/v1/integrations/apprise/urls", { url: newUrl.trim() });
      setNewUrl("");
      setAdding(false);
      await onChanged(result.urls);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to add Apprise URL.");
    } finally {
      setSubmitting(false);
    }
  };

  const removeUrl = async (url: AppriseUrlSummary) => {
    setSubmitting(true);
    try {
      await api.delete(`/api/v1/integrations/apprise/urls/${url.index}`);
      await reload();
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
        <button className="primary-button" onClick={() => setAdding((current) => !current)} type="button">
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
            <button className="primary-button" disabled={submitting || !newUrl.trim()} type="submit">
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
              <button className="icon-button danger" onClick={() => removeUrl(url)} disabled={submitting} type="button" aria-label={`Remove ${url.type} URL`}>
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

export function DiscordSettingsFields({
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
    api.get<UserAccount[]>("/api/v1/users").then(setUsers).catch(() => setUsers([]));
  }, []);

  const linkIdentity = async (identity: DiscordIdentity, field: "user_id" | "person_id", value: string) => {
    setSavingIdentityId(identity.id);
    setIdentityError("");
    try {
      await api.patch<DiscordIdentity>(`/api/v1/integrations/discord/identities/${identity.id}`, {
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

      <div className="settings-form-grid">
        {fields.map((field) => (
          <SettingField
            field={field}
            key={field.key}
            isConfiguredSecret={isConfiguredSecret(field.key)}
            value={form[field.key] ?? ""}
            onChange={(value) => onChange(field.key, value)}
          />
        ))}
      </div>

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

export function WhatsAppSettingsFields({
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

      <div className="settings-form-grid">
        {fields.map((field) => (
          <SettingField
            field={field}
            key={field.key}
            isConfiguredSecret={isConfiguredSecret(field.key)}
            value={form[field.key] ?? ""}
            onChange={(value) => onChange(field.key, value)}
          />
        ))}
      </div>

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

export function HomeAssistantSettingsFields({
  discovery,
  discoveryError,
  discoveryLoading,
  form,
  onChange,
  onReload,
  schedules
}: {
  discovery: HomeAssistantDiscovery | null;
  discoveryError: string;
  discoveryLoading: boolean;
  form: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onReload: () => Promise<void>;
  schedules: Schedule[];
}) {
  type HomeAssistantTab = "setup" | "gates" | "garages";
  const [activeTab, setActiveTab] = React.useState<HomeAssistantTab>("setup");
  const gateEntities = parseManagedCovers(form.home_assistant_gate_entities);
  const garageDoorEntities = parseManagedCovers(form.home_assistant_garage_door_entities);
  const tabs: Array<{ key: HomeAssistantTab; label: string; meta: string; icon: React.ElementType }> = [
    { key: "setup", label: "Setup", meta: discovery ? "Discovery ready" : "Credentials", icon: Home },
    { key: "gates", label: "Gates", meta: `${gateEntities.length} configured`, icon: DoorOpen },
    { key: "garages", label: "Garage doors", meta: `${garageDoorEntities.length} configured`, icon: Warehouse }
  ];

  const updateGateEntities = (entities: HomeAssistantManagedCover[]) => {
    onChange("home_assistant_gate_entities", JSON.stringify(normalizeManagedCoversForSave(entities), null, 2));
  };

  const updateGarageDoorEntities = (entities: HomeAssistantManagedCover[]) => {
    onChange("home_assistant_garage_door_entities", JSON.stringify(normalizeManagedCoversForSave(entities), null, 2));
  };

  const autoDetectGateEntities = () => {
    const suggestions = discovery?.gate_suggestions?.length
      ? discovery.gate_suggestions
      : (discovery?.cover_entities ?? []).filter(isGateCandidate).map(managedCoverFromEntity);
    updateGateEntities(mergeManagedCovers(gateEntities, suggestions));
  };

  const autoDetectGarageDoors = () => {
    const suggestions = discovery?.garage_door_suggestions?.length
      ? discovery.garage_door_suggestions
      : (discovery?.cover_entities ?? []).filter(isGarageDoorCandidate).map(managedCoverFromEntity);
    updateGarageDoorEntities(mergeManagedCovers(garageDoorEntities, suggestions));
  };

  return (
    <div className="ha-config-shell">
      <div className="ha-tabs" role="tablist" aria-label="Home Assistant settings">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              aria-selected={activeTab === tab.key}
              className={activeTab === tab.key ? "ha-tab active" : "ha-tab"}
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              role="tab"
              type="button"
            >
              <Icon size={16} />
              <span>
                <strong>{tab.label}</strong>
                <small>{tab.meta}</small>
              </span>
            </button>
          );
        })}
      </div>
      {discoveryError ? <div className="auth-error inline-error">{discoveryError}</div> : null}

      <div className="ha-tab-panel" role="tabpanel">
        {activeTab === "setup" ? (
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
        ) : null}

        {activeTab === "gates" ? (
        <HomeAssistantCoverTable
          addLabel="Add Gate"
          autoDetectLabel="Auto Detect"
          emptyLabel="No gate entities configured"
          entities={gateEntities}
          icon={DoorOpen}
          availableEntities={discovery?.cover_entities ?? []}
          description="Gates opened when access is granted."
          onAutoDetect={autoDetectGateEntities}
          onChange={updateGateEntities}
          schedules={schedules}
          title="Gate entities"
        />
        ) : null}

        {activeTab === "garages" ? (
        <HomeAssistantCoverTable
          addLabel="Add Door"
          autoDetectLabel="Auto Detect"
          emptyLabel="No garage doors configured"
          entities={garageDoorEntities}
          icon={Warehouse}
          availableEntities={discovery?.cover_entities ?? []}
          description="Garage doors available in each person profile."
          onAutoDetect={autoDetectGarageDoors}
          onChange={updateGarageDoorEntities}
          schedules={schedules}
          title="Garage doors"
        />
        ) : null}
      </div>
    </div>
  );
}

export function HomeAssistantCoverTable({
  addLabel,
  autoDetectLabel,
  availableEntities,
  emptyLabel,
  entities,
  icon: Icon,
  description,
  onAutoDetect,
  onChange,
  schedules,
  title
}: {
  addLabel: string;
  autoDetectLabel: string;
  availableEntities: HomeAssistantEntity[];
  emptyLabel: string;
  entities: HomeAssistantManagedCover[];
  icon: React.ElementType;
  description: string;
  onAutoDetect: () => void;
  onChange: (entities: HomeAssistantManagedCover[]) => void;
  schedules: Schedule[];
  title: string;
}) {
  const [selectedEntityId, setSelectedEntityId] = React.useState("");
  const defaultPolicyOptionLabel = useScheduleDefaultPolicyOptionLabel();
  const selectedIds = React.useMemo(() => new Set(entities.map((entity) => entity.entity_id)), [entities]);
  const addableEntities = availableEntities.filter((entity) => entity.entity_id.startsWith("cover.") && !selectedIds.has(entity.entity_id));

  const addSelectedEntity = () => {
    if (!selectedEntityId) return;
    const entity = availableEntities.find((item) => item.entity_id === selectedEntityId);
    if (!entity) return;
    onChange([...entities, managedCoverFromEntity(entity)]);
    setSelectedEntityId("");
  };

  const updateEntity = (entityId: string, updates: Partial<HomeAssistantManagedCover>) => {
    onChange(entities.map((entity) => entity.entity_id === entityId ? { ...entity, ...updates } : entity));
  };

  const removeEntity = (entityId: string) => {
    onChange(entities.filter((entity) => entity.entity_id !== entityId));
  };

  return (
    <section className="ha-device-panel">
      <div className="ha-device-title">
        <span className="ha-device-icon"><Icon size={17} /></span>
        <div>
          <strong>{title}</strong>
          <span>{entities.length} configured - {description}</span>
        </div>
        <button className="secondary-button ha-auto-button" onClick={onAutoDetect} type="button">
          <RefreshCcw size={15} /> {autoDetectLabel}
        </button>
      </div>

      <div className="ha-entity-composer">
        <select value={selectedEntityId} onChange={(event) => setSelectedEntityId(event.target.value)}>
          <option value="">Select discovered cover entity</option>
          {addableEntities.map((entity) => (
            <option key={entity.entity_id} value={entity.entity_id}>
              {entity.name ? `${entity.name} - ${entity.entity_id}` : entity.entity_id}
            </option>
          ))}
        </select>
        <button className="primary-button ha-add-button" onClick={addSelectedEntity} disabled={!selectedEntityId} type="button">
          <Plus size={15} /> {addLabel}
        </button>
      </div>

      <div className="ha-cover-list">
        {entities.length ? entities.map((entity) => (
          <div className="ha-cover-row" key={entity.entity_id}>
            <div className="ha-cover-identity">
              <input
                value={entity.name}
                onChange={(event) => updateEntity(entity.entity_id, { name: event.target.value })}
                aria-label={`${entity.entity_id} name`}
              />
              <code>{entity.entity_id}</code>
            </div>
            <select
              aria-label={`${entity.name || entity.entity_id} schedule`}
              className="ha-cover-schedule-select"
              value={entity.schedule_id ?? ""}
              onChange={(event) => updateEntity(entity.entity_id, { schedule_id: event.target.value || null })}
            >
              <option value="">{defaultPolicyOptionLabel}</option>
              {schedules.map((schedule) => (
                <option key={schedule.id} value={schedule.id}>{schedule.name}</option>
              ))}
            </select>
            <label className={entity.enabled === false ? "entity-toggle" : "entity-toggle active"}>
              <input
                checked={entity.enabled !== false}
                onChange={(event) => updateEntity(entity.entity_id, { enabled: event.target.checked })}
                type="checkbox"
              />
              <span>{entity.enabled === false ? "Disabled" : "Enabled"}</span>
            </label>
            <button className="icon-button danger" onClick={() => removeEntity(entity.entity_id)} type="button" aria-label={`Remove ${entity.name || entity.entity_id}`}>
              <Trash2 size={15} />
            </button>
          </div>
        )) : (
          <div className="ha-entity-empty">
            <Icon size={18} />
            <span>{emptyLabel}</span>
          </div>
        )}
      </div>
    </section>
  );
}

export function EntitySelectField({
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

export function integrationInitialValues(definition: IntegrationDefinition, values: SettingsMap) {
  const defaults: Record<string, string> = {
    openai_model: "gpt-4o",
    gemini_model: "gemini-1.5-pro",
    anthropic_model: "claude-3-5-sonnet-latest",
    ollama_model: "llama3",
    openai_base_url: "https://api.openai.com/v1",
    gemini_base_url: "https://generativelanguage.googleapis.com/v1beta",
    anthropic_base_url: "https://api.anthropic.com/v1",
    ollama_base_url: "http://host.docker.internal:11434",
    dvla_vehicle_enquiry_url: "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles",
    dvla_test_registration_number: "AA19AAA",
    dvla_timeout_seconds: "10",
    unifi_protect_port: "443",
    unifi_protect_verify_ssl: "false",
    unifi_protect_snapshot_width: "1280",
    unifi_protect_snapshot_height: "720",
    home_assistant_gate_entities: "[]",
    home_assistant_garage_door_entities: "[]",
    discord_guild_allowlist: "",
    discord_channel_allowlist: "",
    discord_user_allowlist: "",
    discord_role_allowlist: "",
    discord_admin_role_ids: "",
    discord_allow_direct_messages: "false",
    discord_require_mention: "true",
    whatsapp_enabled: "false",
    whatsapp_graph_api_version: "v25.0",
    whatsapp_visitor_pass_template_name: "iacs_visitor_welcome",
    whatsapp_visitor_pass_template_language: "en"
  };
  return definition.fields.reduce<Record<string, string>>((acc, field) => {
    const current = values[field.key];
    const currentOrDefault = current !== undefined && current !== null ? current : defaults[field.key] || "";
    if (secretSettingKeys.has(field.key)) {
      acc[field.key] = "";
    } else if (discordListSettingKeys.has(field.key)) {
      acc[field.key] = Array.isArray(current) ? current.map(String).join("\n") : stringifySetting(currentOrDefault);
    } else if (["home_assistant_gate_entities", "home_assistant_garage_door_entities"].includes(field.key) && typeof current === "object") {
      acc[field.key] = JSON.stringify(current ?? {}, null, 2);
    } else {
      acc[field.key] = stringifySetting(currentOrDefault);
    }
    return acc;
  }, {});
}

export function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function parseManagedCovers(value: unknown): HomeAssistantManagedCover[] {
  const raw = parseJsonArray(value);
  const seen = new Set<string>();
  const covers: HomeAssistantManagedCover[] = [];
  for (const item of raw) {
    const cover = normalizeManagedCover(item);
    if (!cover || seen.has(cover.entity_id)) continue;
    covers.push(cover);
    seen.add(cover.entity_id);
  }
  return covers;
}

export function parseJsonArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value.trim()) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function normalizeManagedCover(value: unknown): HomeAssistantManagedCover | null {
  if (typeof value === "string") {
    const entityId = value.trim();
    return entityId.startsWith("cover.")
      ? { entity_id: entityId, name: titleFromEntityId(entityId), enabled: true, open_service: "cover.open_cover", close_service: "cover.close_cover" }
      : null;
  }
  if (!value || typeof value !== "object") return null;
  const raw = value as Partial<HomeAssistantManagedCover>;
  const entityId = String(raw.entity_id ?? "").trim();
  if (!entityId.startsWith("cover.")) return null;
  return {
    entity_id: entityId,
    name: String(raw.name || titleFromEntityId(entityId)),
    enabled: raw.enabled !== false,
    schedule_id: raw.schedule_id ? String(raw.schedule_id) : null,
    open_service: String(raw.open_service || "cover.open_cover"),
    close_service: String(raw.close_service || "cover.close_cover"),
    state: raw.state ?? null
  };
}

export function normalizeManagedCoversForSave(entities: HomeAssistantManagedCover[]) {
  return entities.map((entity) => ({
    entity_id: entity.entity_id,
    name: entity.name || titleFromEntityId(entity.entity_id),
    enabled: entity.enabled !== false,
    schedule_id: entity.schedule_id || null
  }));
}

export function managedCoverFromEntity(entity: HomeAssistantEntity): HomeAssistantManagedCover {
  return {
    entity_id: entity.entity_id,
    name: entity.name || titleFromEntityId(entity.entity_id),
    enabled: true,
    open_service: "cover.open_cover",
    close_service: "cover.close_cover",
    state: entity.state
  };
}

export function mergeManagedCovers(current: HomeAssistantManagedCover[], incoming: HomeAssistantManagedCover[]) {
  const byEntityId = new Map(current.map((entity) => [entity.entity_id, entity]));
  for (const entity of incoming) {
    if (!byEntityId.has(entity.entity_id)) {
      byEntityId.set(entity.entity_id, entity);
    }
  }
  return Array.from(byEntityId.values());
}

export function isGarageDoorCandidate(entity: HomeAssistantEntity) {
  const label = `${entity.entity_id} ${entity.name ?? ""}`.toLowerCase();
  return entity.device_class === "garage" || label.includes("garage");
}

export function isGateCandidate(entity: HomeAssistantEntity) {
  const label = `${entity.entity_id} ${entity.name ?? ""}`.toLowerCase();
  return entity.device_class === "gate" || label.includes("gate");
}
