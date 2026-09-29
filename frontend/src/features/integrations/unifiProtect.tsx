import { Bot, Camera, ChevronDown, ChevronRight, Play, RefreshCcw } from "lucide-react";
import React from "react";
import { formatDate, titleCase } from "../../lib/format";
import { Badge } from "../../ui/primitives";
import type { UnifiProtectCamera } from "../../api/types";
import { integrationsApi, UnifiProtectAnalysis, UnifiProtectEvent, UnifiProtectStatus } from "../../api/integrations";
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
