import { Bot, Camera, CheckCircle2, ChevronDown, ChevronRight, Download, Play, RefreshCcw, ShieldCheck, Trash2 } from "lucide-react";
import React from "react";
import { formatDate, titleCase } from "../../lib/format";
import { Badge } from "../../ui/primitives";
import type { UnifiProtectCamera } from "../../api/types";
import {
  applyProtectUpdate,
  createProtectBackup,
  deleteProtectBackup,
  integrationsApi,
  restoreProtectBackup,
  UnifiProtectAnalysis,
  UnifiProtectBackup,
  UnifiProtectEvent,
  UnifiProtectStatus,
  UnifiProtectUpdateAnalysis,
  UnifiProtectUpdateApplyResult,
  UnifiProtectUpdateStatus
} from "../../api/integrations";
export function UnifiProtectCameraSection({
  cameras,
  error,
  loaded,
  loading,
  onLoad,
  onRefresh,
  refreshToken,
  status
}: {
  cameras: UnifiProtectCamera[];
  error: string;
  loaded: boolean;
  loading: boolean;
  onLoad: () => Promise<void>;
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
      const events = await integrationsApi.getProtectEvents(cameraId);
      setEventsByCamera((current) => ({ ...current, [cameraId]: events }));
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
      const result = await integrationsApi.analyzeProtectSnapshot(camera.id, prompt);
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
  const realtimeDegraded = connected && (status?.realtime_connected === false || Boolean(status?.realtime_error));
  return (
    <section className="protect-section">
      <div className="protect-section-header">
        <div className="card-title">
          <Camera size={18} />
          <h2>UniFi Protect Cameras</h2>
        </div>
        <div className="protect-section-actions">
          <Badge tone={realtimeDegraded ? "red" : connected ? "green" : configured ? "blue" : "gray"}>
            {realtimeDegraded ? "Realtime Degraded" : connected ? "Connected" : configured ? "Configured" : "Not Configured"}
          </Badge>
          <button className="secondary-button" onClick={onRefresh} disabled={loading} type="button">
            <RefreshCcw size={15} /> {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {!configured ? (
        <div className="empty-state">Configure UniFi Protect to load cameras</div>
      ) : !loaded ? (
        <div className="empty-state">
          <button className="secondary-button" onClick={onLoad} disabled={loading} type="button">
            <Camera size={15} /> {loading ? "Loading cameras..." : "Load cameras"}
          </button>
        </div>
      ) : loading && !cameras.length ? (
        <div className="empty-state">Loading cameras</div>
      ) : cameras.length ? (
        <div className="protect-camera-grid">
          {cameras.map((camera) => {
            const events = eventsByCamera[camera.id] ?? [];
            const analysis = analysisByCamera[camera.id];
            const detectionLabels = camera.detections.active.length ? camera.detections.active : camera.is_motion_detected ? ["motion"] : [];
            const snapshotUrl = `${camera.snapshot_url}?width=320&height=180&_=${snapshotNonce[camera.id] ?? refreshToken}`;
            return (
              <article className="protect-camera-card" key={camera.id}>
                <div className="protect-camera-media">
                  <img alt="" decoding="async" loading="lazy" src={snapshotUrl} />
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
                          <img alt="" decoding="async" loading="lazy" src={`${event.thumbnail_url}?width=96&height=54`} />
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
type ProtectExposeRow = {
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
function ProtectExposeTable({
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
function buildProtectExposeRows(status: UnifiProtectStatus | null, cameras: UnifiProtectCamera[]) {
  const consoleRows: ProtectExposeRow[] = [
    { name: "Connection", value: status?.connected ? "Connected" : status?.configured ? "Configured" : "Not configured" },
    { name: "Realtime", value: status?.realtime_connected ? "Connected" : status?.configured ? "Degraded" : "Not configured" },
    { name: "Private websocket", value: titleCase(status?.websocket_states?.private || "unknown") },
    { name: "Events websocket", value: titleCase(status?.websocket_states?.events || "unknown") },
    { name: "Devices websocket", value: titleCase(status?.websocket_states?.devices || "unknown") },
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
function formatExposeValue(value: boolean | string | number | null | undefined) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === undefined || value === "") return "Unknown";
  return String(value);
}
type ProtectUpdateConfirmAction =
  | { kind: "apply" }
  | { kind: "restore"; backup: UnifiProtectBackup }
  | { kind: "delete"; backup: UnifiProtectBackup };
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
      const result = await integrationsApi.getProtectUpdateData();
      setUpdateStatus(result.status);
      setTargetVersion((current) => current || result.status.latest_version);
      setBackups(result.backups);
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
      setAnalysis(await integrationsApi.analyzeProtectUpdate(targetVersion));
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
      const backup = await createProtectBackup();
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
      const applied = await applyProtectUpdate(analysis.target_version);
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
      await restoreProtectBackup(backup);
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
      await deleteProtectBackup(backup);
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
function ProtectAnalysisReview({ analysis }: { analysis: string }) {
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
type ProtectAnalysisSection = {
  title: string;
  body: string[];
};
function parseProtectAnalysisSections(markdown: string): ProtectAnalysisSection[] {
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
function findAnalysisSection(sections: ProtectAnalysisSection[], title: string) {
  return sections.find((section) => section.title.toLowerCase().includes(title));
}
function firstMeaningfulAnalysisLine(lines: string[] | undefined) {
  return cleanInlineMarkdown(lines?.find((line) => line.trim()) ?? "");
}
function cleanInlineMarkdown(value: string) {
  return value.replace(/^[-*]\s+/, "").replace(/\*\*/g, "").replace(/`/g, "").trim();
}
function analysisTone(value: string): "green" | "amber" | "red" | "blue" {
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
function ProtectUpdateConfirmModal({
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
