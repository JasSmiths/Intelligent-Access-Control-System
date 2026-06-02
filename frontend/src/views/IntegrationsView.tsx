import { Bell, PlugZap, RefreshCcw } from "lucide-react";
import React from "react";

import { isRecord } from "../lib/format";
import { notificationChannelMeta } from "../lib/notifications";
import { useSettings } from "../lib/settings";
import { Badge, Toolbar } from "../ui/primitives";
import type { IntegrationStatus, Person, RealtimeMessage, UnifiProtectCamera, UserAccount } from "../api/types";
import {
  DependencyPackage,
  DependencyStorageStatus,
  DiscordChannel,
  DiscordIdentity,
  DiscordStatus,
  ICloudCalendarAccount,
  ICloudCalendarPayload,
  ICloudCalendarSyncRun,
  integrationsApi,
  UnifiProtectStatus,
  UnifiProtectUpdateStatus,
  WhatsAppStatus,
  confirmIntegrationAction
} from "../api/integrations";
import {
  dependenciesForIntegration,
  integrationCategories,
  integrationDefinitions,
  IntegrationDefinition,
  IntegrationsPageTab,
  LlmProviderSelector,
  ProtectIntegrationTab
} from "../features/integrations/catalog";
import { DependencyUpdatesHub, DependencyUpdateModal, dependencyIsActionableUpdate } from "../features/integrations/dependencyUpdates";
import { IntegrationModal } from "../features/integrations/providerPanels";
import { UnifiProtectCameraSection } from "../features/integrations/unifiProtect";

const ICLOUD_REALTIME_PROCESSED_LIMIT = 60;

export function IntegrationsView({ currentUser, people, latestRealtime, refreshToken, status }: { currentUser: UserAccount; people: Person[]; latestRealtime: RealtimeMessage | null; refreshToken: number; status: IntegrationStatus | null }) {
  const { values, loading, save, reload } = useSettings();
  const isAdmin = currentUser.role === "admin";
  const [homeAssistantStatus, setHomeAssistantStatus] = React.useState<IntegrationStatus | null>(status);
  const [accessDeviceStatus, setAccessDeviceStatus] = React.useState<IntegrationStatus | null>(status);
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
  const [protectCamerasLoaded, setProtectCamerasLoaded] = React.useState(false);
  const dependencyUpdatesLoadedRef = React.useRef(false);
  const protectCamerasLoadedRef = React.useRef(false);
  const processedIcloudRealtimeRef = React.useRef(new Set<string>());
  const lastRefreshTokenRef = React.useRef(refreshToken);
  const loadProtect = React.useCallback(async (forceRefresh = false, includeCameras = protectCamerasLoadedRef.current) => {
    setProtectLoading(true);
    setProtectError("");
    try {
      const nextStatus = await integrationsApi.getProtectStatus();
      setProtectStatus(nextStatus);
      if (nextStatus.configured && includeCameras) {
        setProtectCameras(await integrationsApi.getProtectCameras(forceRefresh));
        protectCamerasLoadedRef.current = true;
        setProtectCamerasLoaded(true);
        setProtectSnapshotRefreshToken(Date.now());
      } else if (!nextStatus.configured) {
        setProtectCameras([]);
        protectCamerasLoadedRef.current = true;
        setProtectCamerasLoaded(true);
      }
    } catch (error) {
      if (includeCameras) {
        protectCamerasLoadedRef.current = true;
        setProtectCamerasLoaded(true);
      }
      setProtectError(error instanceof Error ? error.message : "Unable to load UniFi Protect cameras.");
    } finally {
      setProtectLoading(false);
    }
  }, []);
  const loadProtectUpdateStatus = React.useCallback(async () => {
    try {
      setProtectUpdateStatus(await integrationsApi.getProtectUpdateStatus());
    } catch {
      setProtectUpdateStatus(null);
    }
  }, []);
  const loadICloudCalendar = React.useCallback(async () => {
    setIcloudLoading(true);
    setIcloudError("");
    try {
      setIcloudPayload(await integrationsApi.getICloudCalendar());
    } catch (error) {
      setIcloudError(error instanceof Error ? error.message : "Unable to load iCloud Calendar accounts.");
    } finally {
      setIcloudLoading(false);
    }
  }, []);
  const loadHomeAssistantStatus = React.useCallback(async () => {
    try {
      setHomeAssistantStatus(await integrationsApi.getHomeAssistantStatus());
    } catch {
      setHomeAssistantStatus(null);
    }
  }, []);
  const loadAccessDeviceStatus = React.useCallback(async () => {
    try {
      setAccessDeviceStatus(await integrationsApi.getAccessDeviceStatus());
    } catch {
      setAccessDeviceStatus(null);
    }
  }, []);

  React.useEffect(() => {
    setAccessDeviceStatus(status);
  }, [status]);

  React.useEffect(() => {
    const latest = latestRealtime;
    if (!latest || latest.type !== "access_device.status") return;
    const payload = isRecord(latest.payload.status) ? latest.payload.status : latest.payload;
    setAccessDeviceStatus((current) => current
      ? { ...current, ...(payload as Partial<IntegrationStatus>) }
      : payload as IntegrationStatus);
  }, [latestRealtime]);

  React.useEffect(() => {
    const message = latestRealtime;
    if (!message) return;
    const key = `${message.type}-${message.created_at ?? ""}`;
    if (processedIcloudRealtimeRef.current.has(key)) return;
    if (message.type !== "icloud_calendar.accounts_changed" && message.type !== "icloud_calendar.sync_completed") return;
    processedIcloudRealtimeRef.current.add(key);
    if (processedIcloudRealtimeRef.current.size > ICLOUD_REALTIME_PROCESSED_LIMIT) {
      const staleCount = processedIcloudRealtimeRef.current.size - ICLOUD_REALTIME_PROCESSED_LIMIT;
      Array.from(processedIcloudRealtimeRef.current)
        .slice(0, staleCount)
        .forEach((staleKey) => processedIcloudRealtimeRef.current.delete(staleKey));
    }
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
  }, [latestRealtime]);
  const loadDiscord = React.useCallback(async () => {
    setDiscordLoading(true);
    setDiscordError("");
    try {
      const result = await integrationsApi.getDiscordBundle();
      setDiscordStatus(result.status);
      setDiscordChannels(result.channels);
      setDiscordIdentities(result.identities);
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
      setWhatsappStatus(await integrationsApi.getWhatsAppStatus());
    } catch (error) {
      setWhatsappError(error instanceof Error ? error.message : "Unable to load WhatsApp integration.");
    } finally {
      setWhatsappLoading(false);
    }
  }, []);
  const loadDependencyUpdates = React.useCallback(async () => {
    setDependencyLoading(true);
    setDependencyError("");
    dependencyUpdatesLoadedRef.current = true;
    try {
      const result = await integrationsApi.getDependencyUpdates();
      setDependencyPackages(result.packages);
      setDependencyStorage(result.storage);
    } catch (error) {
      dependencyUpdatesLoadedRef.current = false;
      setDependencyError(error instanceof Error ? error.message : "Unable to load dependency updates.");
    } finally {
      setDependencyLoading(false);
    }
  }, []);
  const reloadSettingsAndProtect = React.useCallback(async () => {
    await reload();
    await loadHomeAssistantStatus();
    await loadAccessDeviceStatus();
    await loadProtect(true);
    await loadProtectUpdateStatus();
    await loadICloudCalendar();
    await loadDiscord();
    await loadWhatsApp();
    if (dependencyUpdatesLoadedRef.current) await loadDependencyUpdates();
  }, [loadAccessDeviceStatus, loadDependencyUpdates, loadDiscord, loadHomeAssistantStatus, loadICloudCalendar, loadProtect, loadProtectUpdateStatus, loadWhatsApp, reload]);

  React.useEffect(() => {
    loadHomeAssistantStatus().catch(() => undefined);
    loadAccessDeviceStatus().catch(() => undefined);
    loadProtect(false).catch(() => undefined);
    loadProtectUpdateStatus().catch(() => undefined);
    loadICloudCalendar().catch(() => undefined);
    loadDiscord().catch(() => undefined);
    loadWhatsApp().catch(() => undefined);
  }, [loadAccessDeviceStatus, loadDiscord, loadHomeAssistantStatus, loadICloudCalendar, loadProtect, loadProtectUpdateStatus, loadWhatsApp]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    reloadSettingsAndProtect().catch(() => undefined);
  }, [refreshToken, reloadSettingsAndProtect]);

  React.useEffect(() => {
    if (pageTab !== "updates" || dependencyUpdatesLoadedRef.current) return;
    loadDependencyUpdates().catch(() => undefined);
  }, [loadDependencyUpdates, pageTab]);

  const actionableDependencyUpdateCount = dependencyPackages.filter(dependencyIsActionableUpdate).length;
  const tiles = integrationDefinitions(homeAssistantStatus, values, protectStatus, protectUpdateStatus, icloudPayload.accounts, icloudError, discordStatus, discordError, whatsappStatus, whatsappError, dependencyPackages);
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
                    saving={!isAdmin || llmProviderSaving || loading}
                    values={values}
                    onChange={async (provider) => {
                      setLlmProviderSaving(true);
                      try {
                        const updates = { llm_provider: provider };
                        const confirmation = await confirmIntegrationAction("settings.update", { values: updates }, {
                          target_entity: "SystemSetting",
                          target_id: "llm_provider",
                          target_label: provider,
                          reason: "Update LLM provider"
                        });
                        await save(updates, { confirmationToken: confirmation.confirmation_token });
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
          currentUser={currentUser}
          initialTab={activeTab}
          dependencyPackages={dependenciesForIntegration(active, dependencyPackages)}
          dependencyStorage={dependencyStorage}
          loading={loading}
          protectCameras={protectCameras}
          protectError={protectError || protectStatus?.last_error || ""}
          protectLoading={protectLoading}
          protectStatus={protectStatus}
          protectUpdateStatus={protectUpdateStatus}
          accessDeviceStatus={accessDeviceStatus}
          homeAssistantStatus={homeAssistantStatus}
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
          values={values}
          onClose={() => setActive(null)}
          onICloudChanged={loadICloudCalendar}
          onDiscordChanged={loadDiscord}
          onWhatsAppChanged={loadWhatsApp}
          onProtectUpdateChanged={async () => {
            await loadProtectUpdateStatus();
            await loadProtect(true, protectCamerasLoadedRef.current);
            await loadDependencyUpdates();
          }}
          onProtectRefresh={() => loadProtect(true, true)}
          onSettingsChanged={reloadSettingsAndProtect}
          onAccessDeviceStatusChanged={setAccessDeviceStatus}
          onSaved={async (updates, confirmationToken) => {
            await save(updates, confirmationToken ? { confirmationToken } : {});
            await loadProtect(true, active?.key === "unifi_protect" || protectCamerasLoadedRef.current);
            await loadWhatsApp();
            if (dependencyUpdatesLoadedRef.current) await loadDependencyUpdates();
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
        loaded={protectCamerasLoaded}
        loading={protectLoading}
        onLoad={() => loadProtect(false, true)}
        onRefresh={() => loadProtect(true, true)}
        refreshToken={protectSnapshotRefreshToken}
        status={protectStatus}
      />
    </section>
  );
}
