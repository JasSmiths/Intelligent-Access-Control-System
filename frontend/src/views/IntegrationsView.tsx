import { useCoordinatedRead } from "../lib/useCoordinatedRead";
import { ReadSignalsContext } from "../lib/readSignals";
import { integrationEventMatches } from "../features/integrations/invalidation";
import "../styles/integrations.css";
import { Bell, PlugZap } from "lucide-react";
import React from "react";

import { isRecord } from "../lib/format";
import { notificationChannelMeta } from "../lib/notifications";
import { useSettings } from "../lib/settings";
import { Badge, Toolbar } from "../ui/primitives";
import type { IntegrationStatus, UnifiProtectCamera, UserAccount } from "../api/types";
import {
  ICloudCalendarAccount,
  ICloudCalendarPayload,
  ICloudCalendarSyncRun,
  integrationsApi,
  UnifiProtectStatus
} from "../api/integrations";
import {
  integrationCategories,
  integrationDefinitions,
  IntegrationDefinition,
} from "../features/integrations/catalog";
import { CameraAiProviderSettings } from "../features/integrations/providers/CameraAiProviderSettings";
import { IntegrationModal } from "../features/integrations/IntegrationModal";
import { UnifiProtectCameraSection } from "../features/integrations/unifiProtect";

const ICLOUD_REALTIME_PROCESSED_LIMIT = 60;

export function IntegrationsView({ currentUser }: { currentUser: UserAccount }) {
  const { values, loading, error: settingsError, reload } = useSettings(undefined, integrationEventMatches);
  const [homeAssistantStatus, setHomeAssistantStatus] = React.useState<IntegrationStatus | null>(null);
  const [homeAssistantError, setHomeAssistantError] = React.useState("");
  const [loadedStatuses, setLoadedStatuses] = React.useState<Set<string>>(() => new Set());
  const initialStatusesLoaded = loadedStatuses.size === 3;
  const markLoaded = React.useCallback((key: string) => setLoadedStatuses((current) => new Set(current).add(key)), []);
  const signals = React.useContext(ReadSignalsContext);
  const [active, setActive] = React.useState<IntegrationDefinition | null>(null);
  const [protectStatus, setProtectStatus] = React.useState<UnifiProtectStatus | null>(null);
  const [protectCameras, setProtectCameras] = React.useState<UnifiProtectCamera[]>([]);
  const [protectSnapshotRefreshToken, setProtectSnapshotRefreshToken] = React.useState(0);
  const [protectLoading, setProtectLoading] = React.useState(false);
  const [protectError, setProtectError] = React.useState("");
  const [icloudPayload, setIcloudPayload] = React.useState<ICloudCalendarPayload>({ accounts: [], recent_sync_runs: [] });
  const [icloudError, setIcloudError] = React.useState("");
  const [protectCamerasLoaded, setProtectCamerasLoaded] = React.useState(false);
  const protectCamerasLoadedRef = React.useRef(false);
  const processedIcloudRealtimeRef = React.useRef(new Set<string>());
  const protectOptions = React.useRef({ force: false, includeCameras: false });
  const readProtect = React.useCallback(async (signal: AbortSignal) => {
    const { force, includeCameras } = protectOptions.current;
    setProtectLoading(true); setProtectError("");
    try {
      const nextStatus = await integrationsApi.getProtectStatus({ signal });
      if (signal.aborted) return;
      setProtectStatus(nextStatus);
      if (nextStatus.configured && includeCameras) {
        const cameras = await integrationsApi.getProtectCameras(force, { signal });
        if (signal.aborted) return;
        setProtectCameras(cameras); protectCamerasLoadedRef.current = true; setProtectCamerasLoaded(true); setProtectSnapshotRefreshToken(Date.now());
      } else if (!nextStatus.configured) {
        setProtectCameras([]); protectCamerasLoadedRef.current = true; setProtectCamerasLoaded(true);
      }
    } catch (error) {
      if (!signal.aborted) {
        if (includeCameras) { protectCamerasLoadedRef.current = true; setProtectCamerasLoaded(true); }
        setProtectError(error instanceof Error ? error.message : "Unable to load UniFi Protect cameras.");
      }
      throw error;
    } finally { if (!signal.aborted) { setProtectLoading(false); markLoaded("protect"); protectOptions.current.force = true; } }
  }, [markLoaded]);
  const refreshProtect = useCoordinatedRead(readProtect, integrationEventMatches);
  const loadProtect = React.useCallback((force = false, includeCameras = protectCamerasLoadedRef.current) => {
    protectOptions.current = { force, includeCameras }; return refreshProtect();
  }, [refreshProtect]);
  const readICloudCalendar = React.useCallback(async (signal: AbortSignal) => {
    setIcloudError("");
    try { const payload = await integrationsApi.getICloudCalendar({ signal }); if (!signal.aborted) setIcloudPayload(payload); }
    catch (error) { if (!signal.aborted) setIcloudError(error instanceof Error ? error.message : "Unable to load iCloud Calendar accounts."); throw error; }
    finally { if (!signal.aborted) { markLoaded("icloud"); } }
  }, [markLoaded]);
  const loadICloudCalendar = useCoordinatedRead(readICloudCalendar, integrationEventMatches);
  const readHomeAssistantStatus = React.useCallback(async (signal: AbortSignal) => {
    setHomeAssistantError("");
    try { const result = await integrationsApi.getHomeAssistantStatus({ signal }); if (!signal.aborted) setHomeAssistantStatus(result); }
    catch (error) { if (!signal.aborted) { setHomeAssistantStatus(null); setHomeAssistantError(error instanceof Error ? error.message : "Connection status unavailable."); } throw error; }
    finally { if (!signal.aborted) markLoaded("home_assistant"); }
  }, [markLoaded]);
  const loadHomeAssistantStatus = useCoordinatedRead(readHomeAssistantStatus, integrationEventMatches);
  React.useLayoutEffect(() => signals?.subscribe((signal) => {
    if (signal.type !== "realtime") return;
    const message = signal.message;
    if (message.type === "home_assistant.status") setHomeAssistantStatus((current) => current ? { ...current, ...message.payload } : current);
    if (message.type !== "icloud_calendar.accounts_changed" && message.type !== "icloud_calendar.sync_completed") return;
    const key = `${message.type}-${message.created_at ?? ""}`;
    if (processedIcloudRealtimeRef.current.has(key)) return;
    processedIcloudRealtimeRef.current.add(key);
    if (processedIcloudRealtimeRef.current.size > ICLOUD_REALTIME_PROCESSED_LIMIT) {
      Array.from(processedIcloudRealtimeRef.current).slice(0, processedIcloudRealtimeRef.current.size - ICLOUD_REALTIME_PROCESSED_LIMIT).forEach((key) => processedIcloudRealtimeRef.current.delete(key));
    }
    if (message.type === "icloud_calendar.accounts_changed" && Array.isArray(message.payload.accounts)) setIcloudPayload((current) => ({ ...current, accounts: message.payload.accounts as ICloudCalendarAccount[] }));
    if (message.type === "icloud_calendar.sync_completed" && isRecord(message.payload.sync)) {
      const run = message.payload.sync as ICloudCalendarSyncRun;
      setIcloudPayload((current) => ({ ...current, recent_sync_runs: [run, ...current.recent_sync_runs.filter((item) => item.id !== run.id)].slice(0, 5) }));
    }
  }), [signals]);
  const editorLeaveGuard = React.useRef<(() => boolean) | null>(null);
  const setEditorLeaveGuard = React.useCallback((guard: (() => boolean) | null) => { editorLeaveGuard.current = guard; }, []);
  const refreshProviderCard = React.useCallback(async (provider: string) => {
    // Accepted writes refresh only their own card and saved settings. Read errors
    // remain in those owners and cannot reverse a provider mutation receipt.
    const reads = [reload()];
    if (provider === "home_assistant") reads.push(loadHomeAssistantStatus());
    if (provider === "unifi_protect") reads.push(loadProtect(true));
    if (provider === "icloud_calendar") reads.push(loadICloudCalendar());
    await Promise.allSettled(reads);
  }, [reload, loadHomeAssistantStatus, loadProtect, loadICloudCalendar]);

  const tiles = integrationDefinitions(homeAssistantStatus, values, protectStatus, icloudPayload.accounts, icloudError);
  const groupedTiles = integrationCategories
    .map((category) => ({
      ...category,
      tiles: tiles.filter((tile) => tile.category === category.key)
    }))
    .filter((category) => category.tiles.length);
  return (
    <section className="view-stack integrations-page">
      <Toolbar title="API & Integrations" count={tiles.length} icon={PlugZap} />
      <p className="integration-state-note">Configured means credentials or a service address are saved. Connected means a status read verified the connection.</p>
      {settingsError ? <div className="error-banner" role="alert">Configuration unavailable: {settingsError}</div> : null}
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
                  <CameraAiProviderSettings currentUser={currentUser} loading={loading} values={values} onChanged={reload} />
                ) : null}
                <Badge tone="gray">{category.tiles.length}</Badge>
              </div>
            </div>
            <div className="integration-tile-grid">
              {category.tiles.map((tile) => {
                const Icon = tile.icon;
                const checking = loading || !initialStatusesLoaded;
                const unavailable = Boolean(settingsError || (tile.key === "home_assistant" && homeAssistantError) || (tile.key === "unifi_protect" && protectError) || (tile.key === "icloud_calendar" && icloudError));
                return (
                  <article className="card integration-tile" key={tile.key}>
                    <button
                      className="integration-tile-main"
                      disabled={loading || Boolean(settingsError)}
                      onClick={() => {
                        if (active?.key !== tile.key && editorLeaveGuard.current && !editorLeaveGuard.current()) return;
                        setActive(tile);
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
                      <Badge tone={checking ? "gray" : unavailable ? "amber" : tile.statusTone}>{checking ? "Checking" : unavailable ? "Unavailable" : tile.statusLabel}</Badge>
                    </button>
                  </article>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      {active ? (
        <IntegrationModal
          definition={active}
          currentUser={currentUser}
          initialTab="general"
          loading={loading}
          values={values}
          onClose={() => setActive(null)}
          onGuardChange={setEditorLeaveGuard}
          onChanged={() => refreshProviderCard(active.key)}
        />
      ) : null}
      <UnifiProtectCameraSection
        cameras={protectCameras}
        error={protectError || protectStatus?.last_error || protectStatus?.realtime_error || ""}
        loaded={protectCamerasLoaded}
        loading={protectLoading}
        onLoad={() => loadProtect(false, true).catch(() => undefined)}
        onRefresh={() => loadProtect(true, true).catch(() => undefined)}
        refreshToken={protectSnapshotRefreshToken}
        status={protectStatus}
      />
    </section>
  );
}
