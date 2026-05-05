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
  AuditLog,
  Badge,
  BadgeTone,
  EmptyState,
  formatDate,
  formatFileSize,
  isRecord,
  levelTone,
  nullableString,
  numberPayload,
  RealtimeMessage,
  stringPayload,
  titleCase,
  Toolbar
} from "../shared";



export const MonacoDiffEditor = React.lazy(() => import("@monaco-editor/react").then((module) => ({ default: module.DiffEditor })));

export type TelemetrySpan = {
  id: string;
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  category: string;
  step_order: number;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  status: string;
  attributes: Record<string, unknown>;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  error: string | null;
};

export type TelemetryTrace = {
  trace_id: string;
  name: string;
  category: string;
  status: string;
  level: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  actor: string | null;
  source: string | null;
  registration_number: string | null;
  access_event_id: string | null;
  summary: string | null;
  context: Record<string, unknown>;
  error: string | null;
};

export type TelemetryTraceDetail = TelemetryTrace & {
  spans: TelemetrySpan[];
};

export type TelemetryStorageSummary = {
  total_size_bytes: number;
  database_size_bytes: number;
  log_file_size_bytes: number;
  artifact_size_bytes: number;
  file_count: number;
  updated_at: string;
};

export type GateMalfunctionTimelineEvent = {
  id: string;
  kind: string;
  occurred_at: string;
  title: string;
  details: Record<string, unknown>;
  attempt_number: number | null;
  notification_trigger: string | null;
  notification_channel: string | null;
  telemetry_span_id: string | null;
  status?: string | null;
};

export type GateMalfunctionRecord = {
  id: string;
  gate_entity_id: string;
  gate_name: string | null;
  status: "active" | "resolved" | "fubar" | string;
  opened_at: string;
  declared_at: string;
  resolved_at: string | null;
  fubar_at: string | null;
  fix_attempts_count: number;
  next_attempt_scheduled_at: string | null;
  last_known_vehicle_event_id: string | null;
  last_known_vehicle: string;
  telemetry_trace_id: string | null;
  last_gate_state: string | null;
  last_checked_at: string | null;
  total_downtime_seconds: number;
  summary: string;
  timeline?: GateMalfunctionTimelineEvent[];
};

export type PaginatedResponse<T> = {
  items: T[];
  next_cursor: string | null;
};

export function formatLogMegabytes(size: number) {
  return `${(Math.max(0, size) / (1024 * 1024)).toFixed(1)}MB`;
}

export function auditLogFromRealtimePayload(payload: Record<string, unknown>): AuditLog | null {
  const candidate = isRecord(payload.log) ? payload.log : payload;
  if (
    typeof candidate.id !== "string" ||
    typeof candidate.timestamp !== "string" ||
    typeof candidate.category !== "string" ||
    typeof candidate.action !== "string" ||
    typeof candidate.actor !== "string" ||
    typeof candidate.outcome !== "string" ||
    typeof candidate.level !== "string"
  ) {
    return null;
  }
  return {
    id: candidate.id,
    timestamp: candidate.timestamp,
    category: candidate.category,
    action: candidate.action,
    actor: candidate.actor,
    actor_user_id: nullableString(candidate.actor_user_id),
    target_entity: nullableString(candidate.target_entity),
    target_id: nullableString(candidate.target_id),
    target_label: nullableString(candidate.target_label),
    diff: isRecord(candidate.diff) ? candidate.diff : {},
    metadata: isRecord(candidate.metadata) ? candidate.metadata : {},
    outcome: candidate.outcome,
    level: candidate.level,
    trace_id: nullableString(candidate.trace_id),
    request_id: nullableString(candidate.request_id)
  };
}

export type LogsTabKey = "lpr" | "access" | "gate" | "maintenance" | "ai" | "crud" | "api" | "integrations" | "updates" | "live";

export const logsTabs: Array<{ key: LogsTabKey; label: string; icon: React.ElementType; description: string }> = [
  { key: "lpr", label: "LPR Telemetry", icon: Car, description: "Plate reads, access decisions, and gate timing." },
  { key: "access", label: "Access & Presence", icon: ShieldCheck, description: "Access event backfills, alert actions, presence, and anomalies." },
  { key: "gate", label: "Gate Events", icon: DoorOpen, description: "Malfunctions, recovery attempts, notifications, and resolution." },
  { key: "maintenance", label: "Maintenance Mode", icon: Construction, description: "Kill-switch changes, actor, duration, and HA sync." },
  { key: "ai", label: "AI Audit", icon: Bot, description: "Alfred tools, provider use, and outcomes." },
  { key: "crud", label: "System CRUD", icon: Database, description: "Directory, schedules, notification rules, users, and settings." },
  { key: "api", label: "Webhooks & API", icon: GitBranch, description: "Inbound requests and webhook execution times." },
  { key: "integrations", label: "Integrations", icon: PlugZap, description: "Home Assistant, notifications, DVLA, and provider actions." },
  { key: "updates", label: "Updates & Rollbacks", icon: RefreshCcw, description: "Enrollment, analysis, backups, update jobs, and restores." },
  { key: "live", label: "Live Stream", icon: Terminal, description: "Current websocket event stream." }
];

export const traceCategories: Partial<Record<LogsTabKey, string>> = {
  lpr: "lpr_telemetry",
  gate: "gate_malfunction",
  api: "webhooks_api",
  updates: "dependency_updates"
};

export const auditCategories: Partial<Record<LogsTabKey, string>> = {
  access: "access_presence",
  ai: "alfred_ai",
  crud: "entity_management",
  integrations: "integrations"
};

export const auditActionPrefixes: Partial<Record<LogsTabKey, string>> = {
  maintenance: "maintenance_mode."
};

export function auditLogBelongsToTab(log: AuditLog, tab: LogsTabKey) {
  const category = auditCategories[tab];
  const actionPrefix = auditActionPrefixes[tab];
  return Boolean((category && log.category === category) || (actionPrefix && log.action.startsWith(actionPrefix)));
}

export function auditLogMatchesFilters(log: AuditLog, query: string, level: string) {
  if (level !== "all" && log.level !== level) return false;
  const trimmedQuery = query.trim().toLowerCase();
  if (!trimmedQuery) return true;
  return [
    log.action,
    log.actor,
    log.target_entity,
    log.target_id,
    log.target_label,
    JSON.stringify(log.diff),
    JSON.stringify(log.metadata)
  ].some((value) => String(value || "").toLowerCase().includes(trimmedQuery));
}

export function realtimeLogKey(log: RealtimeMessage) {
  return [
    log.type,
    log.created_at || "",
    stringPayload(log.payload.id),
    stringPayload(log.payload.action),
    stringPayload(log.payload.category)
  ].join("|");
}

export function LogsView({ logs, onClearRealtime, refreshToken }: { logs: RealtimeMessage[]; onClearRealtime: () => void; refreshToken: number }) {
  const [tab, setTab] = React.useState<LogsTabKey>("lpr");
  const [query, setQuery] = React.useState("");
  const [level, setLevel] = React.useState("all");
  const [status, setStatus] = React.useState("all");
  const [traces, setTraces] = React.useState<TelemetryTrace[]>([]);
  const [traceCursor, setTraceCursor] = React.useState<string | null>(null);
  const [traceDetails, setTraceDetails] = React.useState<Record<string, TelemetryTraceDetail>>({});
  const [expandedTraceId, setExpandedTraceId] = React.useState<string | null>(null);
  const [auditLogs, setAuditLogs] = React.useState<AuditLog[]>([]);
  const [auditCursor, setAuditCursor] = React.useState<string | null>(null);
  const [expandedAuditId, setExpandedAuditId] = React.useState<string | null>(null);
  const [storageSummary, setStorageSummary] = React.useState<TelemetryStorageSummary | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [error, setError] = React.useState("");
  const [notice, setNotice] = React.useState("");
  const [clearing, setClearing] = React.useState(false);
  const [liveFilter, setLiveFilter] = React.useState("all");
  const [countdownNow, setCountdownNow] = React.useState(() => Date.now());
  const reloadTimerRef = React.useRef<number | null>(null);
  const processedRealtimeKeysRef = React.useRef<Set<string>>(new Set());
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const isTraceTab = Boolean(traceCategories[tab]);
  const isAuditTab = Boolean(auditCategories[tab] || auditActionPrefixes[tab]);
  const visibleLiveLogs = logs.filter((log) => liveFilter === "all" || log.type.includes(liveFilter));
  const hasActiveGateMalfunction = tab === "gate" && traces.some((trace) => trace.status === "active");

  const clearScheduledTelemetryReload = React.useCallback(() => {
    if (reloadTimerRef.current === null) return;
    window.clearTimeout(reloadTimerRef.current);
    reloadTimerRef.current = null;
  }, []);

  async function loadTelemetry(mode: "reset" | "append" = "reset") {
    if (tab === "live") return;
    setError("");
    mode === "reset" ? setLoading(true) : setLoadingMore(true);
    try {
      if (isTraceTab) {
        if (tab === "gate") {
          const params = new URLSearchParams({ limit: "40" });
          if (status !== "all") params.set("status", status);
          if (mode === "append" && traceCursor) params.set("cursor", traceCursor);
          const response = await api.get<PaginatedResponse<GateMalfunctionRecord>>(`/api/v1/gate-malfunctions/history?${params}`);
          const items = response.items
            .map(gateMalfunctionRecordToTrace)
            .filter((trace) => gateTraceMatchesFilters(trace, query, level, status));
          setTraces((current) => mode === "append" ? [...current, ...items] : items);
          setTraceCursor(response.next_cursor);
        } else {
          const category = traceCategories[tab];
          const params = new URLSearchParams({ limit: "40" });
          if (category) params.set("category", category);
          if (query.trim()) params.set("q", query.trim());
          if (level !== "all") params.set("level", level);
          if (status !== "all") params.set("status", status);
          if (mode === "append" && traceCursor) params.set("cursor", traceCursor);
          const response = await api.get<PaginatedResponse<TelemetryTrace>>(`/api/v1/telemetry/traces?${params}`);
          setTraces((current) => mode === "append" ? [...current, ...response.items] : response.items);
          setTraceCursor(response.next_cursor);
        }
      } else if (isAuditTab) {
        const category = auditCategories[tab];
        const actionPrefix = auditActionPrefixes[tab];
        const params = new URLSearchParams({ limit: "40" });
        if (category) params.set("category", category);
        if (actionPrefix) params.set("action_prefix", actionPrefix);
        if (query.trim()) params.set("q", query.trim());
        if (level !== "all") params.set("level", level);
        if (mode === "append" && auditCursor) params.set("cursor", auditCursor);
        const response = await api.get<PaginatedResponse<AuditLog>>(`/api/v1/telemetry/audit?${params}`);
        setAuditLogs((current) => mode === "append" ? [...current, ...response.items] : response.items);
        setAuditCursor(response.next_cursor);
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load telemetry");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }

  const loadTelemetryStorage = React.useCallback(async () => {
    try {
      setStorageSummary(await api.get<TelemetryStorageSummary>("/api/v1/telemetry/storage"));
    } catch {
      setStorageSummary(null);
    }
  }, []);

  function refreshLogs() {
    loadTelemetry("reset").catch(() => undefined);
    loadTelemetryStorage().catch(() => undefined);
  }

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    refreshLogs();
  }, [refreshToken]);

  async function clearLogs() {
    if (!window.confirm("Clear all telemetry, audit, and live log records?")) return;
    setClearing(true);
    setError("");
    setNotice("");
    try {
      await api.delete("/api/v1/telemetry/purge");
      setTraces([]);
      setTraceCursor(null);
      setTraceDetails({});
      setExpandedTraceId(null);
      setAuditLogs([]);
      setAuditCursor(null);
      setExpandedAuditId(null);
      processedRealtimeKeysRef.current.clear();
      onClearRealtime();
      await loadTelemetryStorage();
      setNotice("Logs cleared");
    } catch (clearError) {
      setError(clearError instanceof Error ? clearError.message : "Unable to clear logs");
    } finally {
      setClearing(false);
    }
  }

  React.useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(""), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  React.useEffect(() => {
    setExpandedTraceId(null);
    setExpandedAuditId(null);
    clearScheduledTelemetryReload();
    loadTelemetry("reset").catch(() => undefined);
  }, [tab, query, level, status, clearScheduledTelemetryReload]);

  React.useEffect(() => {
    loadTelemetryStorage().catch(() => undefined);
  }, [loadTelemetryStorage]);

  React.useEffect(() => {
    const gateStatuses = new Set(["all", "active", "resolved", "fubar"]);
    const traceStatuses = new Set(["all", "ok", "error"]);
    if (tab === "gate" && !gateStatuses.has(status)) setStatus("all");
    if (tab !== "gate" && isTraceTab && !traceStatuses.has(status)) setStatus("all");
  }, [tab, status, isTraceTab]);

  React.useEffect(() => () => clearScheduledTelemetryReload(), [clearScheduledTelemetryReload]);

  React.useEffect(() => {
    if (!hasActiveGateMalfunction) return undefined;
    const timer = window.setInterval(() => setCountdownNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveGateMalfunction]);

  React.useEffect(() => {
    if (!logs.length || tab === "live") return;
    let shouldReload = false;
    const actionPrefix = auditActionPrefixes[tab];
    const nextAuditLogs: AuditLog[] = [];
    const recentLogs = logs.slice(0, 20).reverse();
    for (const realtimeLog of recentLogs) {
      const realtimeKey = realtimeLogKey(realtimeLog);
      if (processedRealtimeKeysRef.current.has(realtimeKey)) continue;
      processedRealtimeKeysRef.current.add(realtimeKey);

      if (realtimeLog.type === "telemetry.trace.created" && realtimeLog.payload.category === traceCategories[tab]) {
        shouldReload = true;
      }
      if (realtimeLog.type === "audit.log.created" && realtimeLog.payload.category === auditCategories[tab]) {
        shouldReload = true;
      }
      if (
        realtimeLog.type === "audit.log.created" &&
        actionPrefix &&
        stringPayload(realtimeLog.payload.action).startsWith(actionPrefix)
      ) {
        shouldReload = true;
      }
      if (tab === "maintenance" && realtimeLog.type === "maintenance_mode.changed") {
        shouldReload = true;
      }
      if (tab === "gate" && realtimeLog.type.startsWith("gate_malfunction.")) {
        shouldReload = true;
      }

      if (realtimeLog.type === "audit.log.created") {
        const liveAuditLog = auditLogFromRealtimePayload(realtimeLog.payload);
        if (
          liveAuditLog &&
          auditLogBelongsToTab(liveAuditLog, tab) &&
          auditLogMatchesFilters(liveAuditLog, query, level)
        ) {
          nextAuditLogs.push(liveAuditLog);
        }
      }
    }
    if (processedRealtimeKeysRef.current.size > 200) {
      const staleKeys = Array.from(processedRealtimeKeysRef.current).slice(0, processedRealtimeKeysRef.current.size - 200);
      staleKeys.forEach((key) => processedRealtimeKeysRef.current.delete(key));
    }
    if (!shouldReload) return;
    if (nextAuditLogs.length) {
      setAuditLogs((current) => {
        let merged = current;
        nextAuditLogs.forEach((liveAuditLog) => {
          merged = [liveAuditLog, ...merged.filter((item) => item.id !== liveAuditLog.id)];
        });
        return merged.slice(0, 40);
      });
    }
    clearScheduledTelemetryReload();
    reloadTimerRef.current = window.setTimeout(() => {
      reloadTimerRef.current = null;
      loadTelemetry("reset").catch(() => undefined);
      loadTelemetryStorage().catch(() => undefined);
    }, 900);
  }, [logs, tab, query, level, status, clearScheduledTelemetryReload, loadTelemetryStorage]);

  const toggleTrace = async (trace: TelemetryTrace) => {
    const traceId = trace.trace_id;
    if (expandedTraceId === traceId) {
      setExpandedTraceId(null);
      return;
    }
    setExpandedTraceId(traceId);
    if (!traceDetails[traceId]) {
      const malfunctionId = trace.category === "gate_malfunction"
        ? stringPayload(trace.context.malfunction_id || trace.context.id)
        : "";
      const detail = malfunctionId
        ? gateMalfunctionRecordToTraceDetail(
            await api.get<GateMalfunctionRecord>(`/api/v1/gate-malfunctions/${malfunctionId}/trace`)
          )
        : await api.get<TelemetryTraceDetail>(`/api/v1/telemetry/traces/${traceId}`);
      setTraceDetails((current) => ({ ...current, [traceId]: detail }));
    }
  };

  const activeTab = logsTabs.find((item) => item.key === tab) ?? logsTabs[0];
  const logStorageLabel = storageSummary ? formatLogMegabytes(storageSummary.total_size_bytes) : "...";

  return (
    <section className="view-stack telemetry-workspace">
      <Toolbar title="Telemetry & Audit" badge={logStorageLabel} icon={activeTab.icon}>
        <button className="danger-button" onClick={clearLogs} type="button" disabled={clearing}>
          <Trash2 size={15} /> {clearing ? "Clearing..." : "Clear Logs"}
        </button>
        <button className="secondary-button" onClick={refreshLogs} type="button">
          <RefreshCcw size={15} /> Refresh
        </button>
      </Toolbar>

      <div className="telemetry-layout">
        <aside className="telemetry-tabs" aria-label="Log categories">
          {logsTabs.map((item) => {
            const Icon = item.icon;
            return (
              <button className={tab === item.key ? "telemetry-tab active" : "telemetry-tab"} key={item.key} onClick={() => setTab(item.key)} type="button">
                <Icon size={17} />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </span>
              </button>
            );
          })}
        </aside>

        <div className="telemetry-panel">
          <div className="telemetry-filterbar">
            <label className="search telemetry-search">
              <Search size={16} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search telemetry, actors, plates, payloads..." disabled={tab === "live"} />
            </label>
            {tab === "live" ? (
              <select value={liveFilter} onChange={(event) => setLiveFilter(event.target.value)}>
                <option value="all">All live events</option>
                <option value="event">Access events</option>
                <option value="chat">Chat</option>
                <option value="whatsapp">WhatsApp</option>
                <option value="gate">Gate</option>
                <option value="maintenance">Maintenance</option>
                <option value="telemetry">Telemetry</option>
              </select>
            ) : (
              <>
                <select value={level} onChange={(event) => setLevel(event.target.value)}>
                  <option value="all">All levels</option>
                  <option value="info">INFO</option>
                  <option value="warning">WARN</option>
                  <option value="error">ERROR</option>
                  <option value="purple">AI ACTION</option>
                </select>
	                {isTraceTab ? (
	                  <select value={status} onChange={(event) => setStatus(event.target.value)}>
	                    <option value="all">All statuses</option>
	                    {tab === "gate" ? (
	                      <>
	                        <option value="active">Active</option>
	                        <option value="resolved">Resolved</option>
	                        <option value="fubar">FUBAR</option>
	                      </>
	                    ) : (
	                      <>
	                        <option value="ok">OK</option>
	                        <option value="error">Error</option>
	                      </>
	                    )}
	                  </select>
	                ) : null}
              </>
            )}
          </div>

          {error ? <div className="error-banner">{error}</div> : null}
          {notice ? <div className="success-banner">{notice}</div> : null}
          {loading ? <div className="loading-panel">Loading telemetry</div> : null}

          {!loading && tab === "live" ? <LiveLogStream logs={visibleLiveLogs} /> : null}
          {!loading && isTraceTab ? (
            <TraceList
              details={traceDetails}
              expandedTraceId={expandedTraceId}
              now={countdownNow}
              onToggle={toggleTrace}
              traces={traces}
            />
          ) : null}
          {!loading && isAuditTab ? (
            <AuditLogList
              expandedAuditId={expandedAuditId}
              logs={auditLogs}
              onToggle={(id) => setExpandedAuditId((current) => current === id ? null : id)}
            />
          ) : null}

          {!loading && tab !== "live" && ((isTraceTab && !traces.length) || (isAuditTab && !auditLogs.length)) ? (
            <EmptyState icon={Terminal} label="No telemetry records match these filters." />
          ) : null}

          {tab !== "live" && ((isTraceTab && traceCursor) || (isAuditTab && auditCursor)) ? (
            <div className="telemetry-load-more">
              <button className="secondary-button" disabled={loadingMore} onClick={() => loadTelemetry("append")} type="button">
                {loadingMore ? "Loading..." : "Load More"}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

export function TraceList({
  traces,
  details,
  expandedTraceId,
  now,
  onToggle
}: {
  traces: TelemetryTrace[];
  details: Record<string, TelemetryTraceDetail>;
  expandedTraceId: string | null;
  now: number;
  onToggle: (trace: TelemetryTrace) => void;
}) {
  const parentRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: traces.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 126,
    overscan: 6
  });
  return (
    <div className="telemetry-virtual-list" ref={parentRef}>
      <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const trace = traces[virtualRow.index];
          const expanded = expandedTraceId === trace.trace_id;
          return (
            <div
              className="telemetry-virtual-row"
              data-index={virtualRow.index}
              key={trace.trace_id}
              ref={rowVirtualizer.measureElement}
              style={{ transform: `translateY(${virtualRow.start}px)` }}
            >
              <TraceRow detail={details[trace.trace_id]} expanded={expanded} now={now} onToggle={() => onToggle(trace)} trace={trace} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function TraceRow({
  trace,
  detail,
  expanded,
  now,
  onToggle
}: {
  trace: TelemetryTrace;
  detail?: TelemetryTraceDetail;
  expanded: boolean;
  now: number;
  onToggle: () => void;
}) {
  const display = traceDisplay(trace);
  const Icon = display.icon;
  const countdown = gateMalfunctionCountdown(trace, now);
  return (
    <article className={expanded ? "telemetry-card expanded" : "telemetry-card"}>
      <button className="telemetry-row-button" onClick={onToggle} type="button">
        <span className={`telemetry-row-icon ${display.tone}`}>
          <Icon size={18} />
        </span>
        <span className="telemetry-row-main">
          <strong>{display.title}</strong>
          <small>{trace.summary || trace.name}</small>
        </span>
        <span className="telemetry-row-meta">
          <Badge tone={levelTone(trace.level)}>{levelLabel(trace.level)}</Badge>
          {countdown ? <Badge tone={countdown.overdue ? "red" : "amber"}>{countdown.label}</Badge> : null}
          <code>{formatDuration(trace.duration_ms)}</code>
          <time>{formatDate(trace.started_at)}</time>
        </span>
        {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
      </button>
      {expanded ? (
        detail ? <TraceWaterfall trace={detail} /> : <div className="telemetry-detail-loading">Loading trace spans...</div>
      ) : null}
    </article>
  );
}

export function TraceWaterfall({ trace }: { trace: TelemetryTraceDetail }) {
  if (trace.category === "gate_malfunction") {
    return <GateMalfunctionTimeline trace={trace} />;
  }
  const traceStart = Date.parse(trace.started_at);
  const totalMs = Math.max(trace.duration_ms || 0, ...trace.spans.map((span) => {
    const end = span.ended_at ? Date.parse(span.ended_at) : Date.parse(span.started_at);
    return Math.max(0, end - traceStart);
  }), 1);
  return (
    <div className="trace-waterfall">
      <div className="trace-summary-grid">
        <TelemetryFact label="Trace ID" value={trace.trace_id} mono />
        <TelemetryFact label="Source" value={trace.source || "unknown"} />
        <TelemetryFact label="Plate" value={trace.registration_number || "n/a"} mono />
        <TelemetryFact label="Total Duration" value={formatDuration(totalMs)} mono />
      </div>
      <div className="waterfall-steps">
        {trace.spans.map((span, index) => {
          const started = Date.parse(span.started_at);
          const offset = Math.max(0, ((started - traceStart) / totalMs) * 100);
          const width = Math.max(1.5, ((span.duration_ms || 0) / totalMs) * 100);
          const artifact = artifactFromSpan(span);
          return (
            <div className="waterfall-step" key={span.span_id}>
              <div className="waterfall-step-marker">
                <span>{index + 1}</span>
              </div>
              <div className="waterfall-step-body">
                <div className="waterfall-step-header">
                  <strong>{span.name}</strong>
                  <code>{formatDuration(span.duration_ms)}</code>
                </div>
                <div className="waterfall-bar-track">
                  <span className={span.status === "error" ? "waterfall-bar error" : "waterfall-bar"} style={{ left: `${offset}%`, width: `${Math.min(width, 100 - offset)}%` }} />
                </div>
                {artifact ? (
                  <div className="trace-artifact">
                    <img alt="Camera snapshot used for direction analysis" src={String(artifact.url)} />
                    <span>
                      <strong>Vision Snapshot</strong>
                      <small>{String(artifact.content_type || "image")} · {formatFileSize(numberPayload(artifact.size_bytes))}</small>
                    </span>
                  </div>
                ) : null}
                <TraceSpanPayload span={span} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function GateMalfunctionTimeline({ trace }: { trace: TelemetryTraceDetail }) {
  const context = trace.context;
  const status = stringPayload(context.status || trace.status);
  const nextAttempt = stringPayload(context.next_attempt_scheduled_at);
  const downtimeSeconds = numberPayload(context.total_downtime_seconds);
  const attempts = stringPayload(context.fix_attempts_count || "0");
  const gateName = stringPayload(context.gate_name || trace.source || "Primary gate");
  const sortedSpans = [...trace.spans].sort((left, right) => Date.parse(left.started_at) - Date.parse(right.started_at));
  return (
    <div className="gate-trace">
      <div className="trace-summary-grid">
        <TelemetryFact label="Gate" value={gateName} />
        <TelemetryFact label="Status" value={titleCase(status || "unknown")} />
        <TelemetryFact label="Attempts" value={attempts} mono />
        <TelemetryFact label={status === "active" ? "Open Duration" : "Total Downtime"} value={formatSecondsDuration(downtimeSeconds)} mono />
      </div>
      {status === "active" && nextAttempt ? (
        <div className="gate-trace-countdown">
          <Clock3 size={16} />
          <span>
            <strong>{gateMalfunctionCountdownLabel(nextAttempt, Date.now())}</strong>
            <small>Next automated recovery attempt</small>
          </span>
        </div>
      ) : null}
      <div className="gate-timeline">
        {sortedSpans.map((span, index) => {
          const details = span.output_payload || {};
          const kind = stringPayload(span.attributes.kind || "");
          return (
            <div className={`gate-timeline-step ${span.status === "error" ? "error" : ""}`} key={span.span_id}>
              <div className="gate-timeline-marker">
                <span>{index + 1}</span>
              </div>
              <div className="gate-timeline-body">
                <div className="gate-timeline-header">
                  <strong>{span.name}</strong>
                  <time>{formatDate(span.started_at)}</time>
                </div>
                <p>{gateTimelineSummary(kind, details)}</p>
                {Object.keys(details).length ? <JsonBlock value={details} /> : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function TraceSpanPayload({ span }: { span: TelemetrySpan }) {
  const payload = {
    attributes: span.attributes,
    input: span.input_payload,
    output: span.output_payload,
    error: span.error
  };
  if (!Object.values(payload).some((value) => value && (typeof value !== "object" || Object.keys(value as Record<string, unknown>).length))) {
    return null;
  }
  return <JsonBlock value={payload} />;
}

export function AuditLogList({
  logs,
  expandedAuditId,
  onToggle
}: {
  logs: AuditLog[];
  expandedAuditId: string | null;
  onToggle: (id: string) => void;
}) {
  const parentRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: logs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 116,
    overscan: 6
  });
  return (
    <div className="telemetry-virtual-list" ref={parentRef}>
      <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const log = logs[virtualRow.index];
          return (
            <div
              className="telemetry-virtual-row"
              data-index={virtualRow.index}
              key={log.id}
              ref={rowVirtualizer.measureElement}
              style={{ transform: `translateY(${virtualRow.start}px)` }}
            >
              <AuditLogRow expanded={expandedAuditId === log.id} log={log} onToggle={() => onToggle(log.id)} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function AuditLogRow({ log, expanded, onToggle }: { log: AuditLog; expanded: boolean; onToggle: () => void }) {
  const [showEditor, setShowEditor] = React.useState(false);
  const oldValue = isRecord(log.diff.old) ? log.diff.old : {};
  const newValue = isRecord(log.diff.new) ? log.diff.new : {};
  const auditSummary = typeof log.metadata.summary === "string" ? log.metadata.summary : "";
  const delta = React.useMemo(() => jsonDiff(oldValue, newValue) || {}, [log.diff]);
  const original = stringifyJson(oldValue);
  const modified = stringifyJson(newValue);
  const AuditIcon = log.category === "alfred_ai"
    ? Bot
    : log.category === "integrations"
      ? PlugZap
      : log.category === "access_presence"
        ? ShieldCheck
        : log.action.startsWith("maintenance_mode.")
          ? Construction
          : Database;
  const auditTone = log.category === "alfred_ai"
    ? "purple"
    : log.category === "access_presence"
      ? "green"
      : log.action.startsWith("maintenance_mode.")
        ? "amber"
        : levelTone(log.level);
  return (
    <article className={expanded ? "telemetry-card expanded" : "telemetry-card"}>
      <button className="telemetry-row-button" onClick={onToggle} type="button">
        <span className={`telemetry-row-icon ${auditTone}`}>
          <AuditIcon size={18} />
        </span>
        <span className="telemetry-row-main">
          <strong>{titleCase(log.action.replace(/\./g, " "))}</strong>
          <small>{log.target_label || log.target_id || log.target_entity || "System"} · {log.actor}</small>
          {auditSummary ? <small className="telemetry-row-summary">{auditSummary}</small> : null}
        </span>
        <span className="telemetry-row-meta">
          <Badge tone={auditTone}>{log.category === "alfred_ai" ? "AI ACTION" : log.category === "access_presence" ? "ACCESS" : log.action.startsWith("maintenance_mode.") ? "MAINTENANCE" : levelLabel(log.level)}</Badge>
          <Badge tone={outcomeTone(log.outcome)}>{titleCase(log.outcome)}</Badge>
          <time>{formatDate(log.timestamp)}</time>
        </span>
        {expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}
      </button>
      {expanded ? (
        <div className="audit-detail">
          <div className="trace-summary-grid">
            <TelemetryFact label="Actor" value={log.actor} />
            <TelemetryFact label="Target" value={log.target_entity || "System"} />
            <TelemetryFact label="Request" value={log.request_id || "n/a"} mono />
            <TelemetryFact label="Trace" value={log.trace_id || "n/a"} mono />
          </div>
          <div className="audit-diff-toolbar">
            <strong>JSON Diff</strong>
            <button className="secondary-button" onClick={() => setShowEditor((current) => !current)} type="button">
              <FileText size={15} /> {showEditor ? "Show Summary" : "Open Diff Editor"}
            </button>
          </div>
          {showEditor ? (
            <React.Suspense fallback={<div className="telemetry-detail-loading">Loading diff editor...</div>}>
              <MonacoDiffEditor
                height="280px"
                language="json"
                modified={modified}
                options={{ readOnly: true, minimap: { enabled: false }, renderSideBySide: false, scrollBeyondLastLine: false }}
                original={original}
              />
            </React.Suspense>
          ) : (
            <div className="audit-diff-grid">
              <JsonBlock label="Changed Fields" value={delta} />
              <JsonBlock label="Metadata" value={log.metadata} />
            </div>
          )}
        </div>
      ) : null}
    </article>
  );
}

export function LiveLogStream({ logs }: { logs: RealtimeMessage[] }) {
  return (
    <div className="log-console telemetry-live-console">
      {logs.map((log, index) => (
        <div className="log-line" key={`${log.type}-${log.created_at}-${index}`}>
          <time>{log.created_at ? formatDate(log.created_at) : "now"}</time>
          <strong>{log.type}</strong>
          <code>{JSON.stringify(log.payload)}</code>
        </div>
      ))}
      {!logs.length ? <EmptyState icon={Terminal} label="No live events in this filter." /> : null}
    </div>
  );
}

export function TelemetryFact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="telemetry-fact">
      <span>{label}</span>
      <strong className={mono ? "mono" : undefined}>{value}</strong>
    </div>
  );
}

export function JsonBlock({ value, label }: { value: unknown; label?: string }) {
  return (
    <div className="json-block">
      {label ? <strong>{label}</strong> : null}
      <pre>{stringifyJson(value)}</pre>
    </div>
  );
}

export function gateMalfunctionTraceId(record: GateMalfunctionRecord) {
  return record.telemetry_trace_id || record.id;
}

export function gateMalfunctionLevel(status: string) {
  if (status === "fubar") return "error";
  if (status === "active") return "warning";
  return "info";
}

export function gateMalfunctionRecordToTrace(record: GateMalfunctionRecord): TelemetryTrace {
  return {
    trace_id: gateMalfunctionTraceId(record),
    name: `Gate Malfunction - ${record.gate_name || record.gate_entity_id}`,
    category: "gate_malfunction",
    status: record.status,
    level: gateMalfunctionLevel(record.status),
    started_at: record.opened_at,
    ended_at: record.resolved_at || record.fubar_at,
    duration_ms: Math.max(0, record.total_downtime_seconds || 0) * 1000,
    actor: "System",
    source: record.gate_entity_id,
    registration_number: null,
    access_event_id: record.last_known_vehicle_event_id,
    summary: record.summary,
    context: {
      ...record,
      malfunction_id: record.id,
      status: record.status,
      gate_entity_id: record.gate_entity_id,
      gate_name: record.gate_name,
      next_attempt_scheduled_at: record.next_attempt_scheduled_at,
      fix_attempts_count: record.fix_attempts_count,
      total_downtime_seconds: record.total_downtime_seconds
    },
    error: null
  };
}

export function gateMalfunctionRecordToTraceDetail(record: GateMalfunctionRecord): TelemetryTraceDetail {
  const trace = gateMalfunctionRecordToTrace(record);
  return {
    ...trace,
    spans: (record.timeline || []).map((event, index) => {
      const failed = event.status === "error" || event.kind === "fubar" || event.kind === "notification_failed" || event.details.accepted === false;
      return {
        id: event.id,
        span_id: event.telemetry_span_id || event.id.replace(/-/g, "").slice(0, 16),
        trace_id: trace.trace_id,
        parent_span_id: null,
        name: event.title,
        category: "gate_malfunction",
        step_order: index + 1,
        started_at: event.occurred_at,
        ended_at: event.occurred_at,
        duration_ms: 0,
        status: failed ? "error" : "ok",
        attributes: { kind: event.kind, attempt_number: event.attempt_number },
        input_payload: {},
        output_payload: {
          ...event.details,
          notification_trigger: event.notification_trigger,
          notification_channel: event.notification_channel
        },
        error: null
      };
    })
  };
}

export function gateTraceMatchesFilters(trace: TelemetryTrace, query: string, level: string, status: string) {
  if (level !== "all" && trace.level !== level) return false;
  if (status !== "all" && trace.status !== status) return false;
  const trimmedQuery = query.trim().toLowerCase();
  if (!trimmedQuery) return true;
  return [
    trace.name,
    trace.summary,
    trace.source,
    trace.context.gate_name,
    trace.context.last_known_vehicle,
    JSON.stringify(trace.context)
  ].some((value) => String(value || "").toLowerCase().includes(trimmedQuery));
}

export function traceDisplay(trace: TelemetryTrace): { title: string; icon: React.ElementType; tone: BadgeTone } {
  if (trace.category === "gate_malfunction") {
    const status = stringPayload(trace.context.status || trace.status).toLowerCase();
    const gate = stringPayload(trace.context.gate_name || trace.source || "Primary gate");
    if (status === "fubar") return { title: `Gate Malfunction - ${gate}`, icon: AlertTriangle, tone: "red" };
    if (status === "resolved") return { title: `Gate Resolved - ${gate}`, icon: CheckCircle2, tone: "green" };
    return { title: `Gate Malfunction - ${gate}`, icon: DoorOpen, tone: "amber" };
  }
  if (trace.category === "dependency_updates") {
    return { title: trace.summary || trace.name, icon: RefreshCcw, tone: trace.status === "error" ? "red" : "blue" };
  }
  const decision = stringPayload(trace.context.decision).toLowerCase();
  const direction = stringPayload(trace.context.direction).toLowerCase();
  const plate = trace.registration_number || "unknown plate";
  if (decision === "denied") return { title: `Entry Denied - Plate ${plate}`, icon: AlertTriangle, tone: "red" };
  if (direction === "exit") return { title: `Exit Granted - Plate ${plate}`, icon: LogOut, tone: "gray" };
  if (decision === "granted") return { title: `Entry Granted - Plate ${plate}`, icon: LogIn, tone: "green" };
  if (trace.status === "error") return { title: trace.name, icon: AlertTriangle, tone: "red" };
  return { title: trace.name, icon: Activity, tone: "blue" };
}

export function gateMalfunctionCountdown(trace: TelemetryTrace, now: number): { label: string; overdue: boolean } | null {
  if (trace.category !== "gate_malfunction" || trace.status !== "active") return null;
  const scheduledAt = stringPayload(trace.context.next_attempt_scheduled_at);
  if (!scheduledAt) return null;
  return {
    label: gateMalfunctionCountdownLabel(scheduledAt, now),
    overdue: Date.parse(scheduledAt) <= now
  };
}

export function gateMalfunctionCountdownLabel(scheduledAt: string, now: number) {
  const target = Date.parse(scheduledAt);
  if (!Number.isFinite(target)) return "Next attempt pending";
  const remainingSeconds = Math.ceil((target - now) / 1000);
  if (remainingSeconds <= 0) return "Attempt due now";
  return `Next attempt in ${formatSecondsDuration(remainingSeconds)}`;
}

export function gateTimelineSummary(kind: string, details: Record<string, unknown>) {
  if (kind === "preceding_event") {
    return "Closest entry or exit event before the gate was declared malfunctioning.";
  }
  if (kind === "declared") {
    return `Declared after the gate remained ${stringPayload(details.gate_state || "open")}.`;
  }
  if (kind === "attempt" || kind === "manual_attempt") {
    const accepted = details.accepted === true ? "accepted" : "failed";
    return `Recovery command ${accepted}; gate state reported ${stringPayload(details.state || "unknown")}.`;
  }
  if (kind.startsWith("notification")) {
    const channel = stringPayload(details.channel || details.trigger || "workflow");
    return `Notification workflow update via ${channel}.`;
  }
  if (kind === "resolved") {
    return `Resolved after ${formatSecondsDuration(numberPayload(details.total_downtime_seconds))}.`;
  }
  if (kind === "fubar") {
    return "Automated recovery has stopped and manual intervention is required.";
  }
  return stringPayload(details.detail || details.reason || "Gate malfunction timeline event.");
}

export function artifactFromSpan(span: TelemetrySpan): Record<string, unknown> | null {
  const artifact = span.output_payload.artifact;
  return isRecord(artifact) && typeof artifact.url === "string" ? artifact : null;
}

export function outcomeTone(outcome: string): BadgeTone {
  if (outcome === "success") return "green";
  if (outcome === "failed") return "red";
  if (outcome === "pending_confirmation") return "amber";
  return "gray";
}

export function levelLabel(level: string | null | undefined) {
  const normalized = String(level || "info").toLowerCase();
  if (normalized === "warning") return "WARN";
  if (normalized === "purple") return "AI ACTION";
  return normalized.toUpperCase();
}

export function formatDuration(value: number | null | undefined) {
  const ms = Math.max(0, Number(value || 0));
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10_000 ? 1 : 2)}s`;
  return `${ms.toFixed(ms >= 100 ? 0 : 1)}ms`;
}

export function formatSecondsDuration(value: number | null | undefined) {
  const totalSeconds = Math.max(0, Math.floor(Number(value || 0)));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

export function stringifyJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}
