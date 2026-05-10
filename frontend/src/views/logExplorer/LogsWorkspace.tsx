import React from "react";

import {
  api,
  AuditLog,
  createActionConfirmation,
  RealtimeMessage
} from "../../shared";
import {
  ClearLogsConfirmModal,
  exportVisible,
  LiveStreamDrawer,
  LogsDetailInspector,
  LogsExplorerTable,
  LogsFilterBar,
  LogsMetricStrip,
  LogsSourceTabs,
  LogsTopBar,
  SaveFilterModal
} from "./components";
import { defaultLogsFilters } from "./constants";
import {
  GateMalfunctionRecord,
  LogRecord,
  LogsFilters,
  LogSourceKey,
  PaginatedResponse,
  SavedLogsFilter,
  TelemetrySummary,
  TelemetryTrace,
  TelemetryTraceDetail,
  TraceDetailState
} from "./types";
import {
  applyLocalFilters,
  auditLogFromRealtimePayload,
  auditRecord,
  buildAuditParams,
  buildSummaryParams,
  buildTraceParams,
  firstRecord,
  gateMalfunctionRecordToTrace,
  gateMalfunctionRecordToTraceDetail,
  liveRecord,
  loadSavedFilters,
  realtimeLogKey,
  saveSavedFilters,
  sourceUsesAudit,
  sourceUsesTraces,
  timeRangeFrom,
  traceRecord
} from "./utils";

export function LogsWorkspace({
  logs,
  onClearRealtime,
  refreshToken
}: {
  logs: RealtimeMessage[];
  onClearRealtime: () => void;
  refreshToken: number;
}) {
  const [activeSource, setActiveSource] = React.useState<LogSourceKey>("all");
  const [filters, setFilters] = React.useState<LogsFilters>(defaultLogsFilters);
  const [traceRecords, setTraceRecords] = React.useState<LogRecord[]>([]);
  const [auditRecords, setAuditRecords] = React.useState<LogRecord[]>([]);
  const [traceCursor, setTraceCursor] = React.useState<string | null>(null);
  const [auditCursor, setAuditCursor] = React.useState<string | null>(null);
  const [summary, setSummary] = React.useState<TelemetrySummary | null>(null);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [traceDetails, setTraceDetails] = React.useState<Record<string, TraceDetailState>>({});
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [error, setError] = React.useState("");
  const [notice, setNotice] = React.useState("");
  const [clearing, setClearing] = React.useState(false);
  const [clearConfirmOpen, setClearConfirmOpen] = React.useState(false);
  const [clearError, setClearError] = React.useState("");
  const [savedFilters, setSavedFilters] = React.useState<SavedLogsFilter[]>(() => loadSavedFilters());
  const [saveFilterOpen, setSaveFilterOpen] = React.useState(false);
  const [saveFilterError, setSaveFilterError] = React.useState("");
  const [liveOpen, setLiveOpen] = React.useState(true);
  const [livePaused, setLivePaused] = React.useState(false);
  const [displayedLiveRecords, setDisplayedLiveRecords] = React.useState<LogRecord[]>([]);
  const reloadTimerRef = React.useRef<number | null>(null);
  const processedRealtimeKeysRef = React.useRef<Set<string>>(new Set());
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const normalizedLiveRecords = React.useMemo(
    () => logs.map((log, index) => liveRecord(log, index)),
    [logs]
  );

  React.useEffect(() => {
    if (!livePaused) setDisplayedLiveRecords(normalizedLiveRecords);
  }, [livePaused, normalizedLiveRecords]);

  const combinedRecords = React.useMemo(() => {
    const relevantLiveRecords = activeSource === "live" || activeSource === "all"
      ? displayedLiveRecords
      : displayedLiveRecords.filter((record) => record.source === activeSource);
    const records = activeSource === "live"
      ? relevantLiveRecords
      : [...traceRecords, ...auditRecords, ...relevantLiveRecords];
    return [...records].sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
  }, [activeSource, auditRecords, displayedLiveRecords, traceRecords]);

  const liveSourceCounts = React.useMemo(() => {
    const counts = {} as Partial<Record<LogSourceKey, number>>;
    displayedLiveRecords.forEach((record) => {
      counts[record.source] = (counts[record.source] || 0) + 1;
    });
    counts.all = displayedLiveRecords.length;
    counts.live = displayedLiveRecords.length;
    return counts;
  }, [displayedLiveRecords]);

  const visibleRecords = React.useMemo(
    () => applyLocalFilters(combinedRecords, filters, activeSource),
    [activeSource, combinedRecords, filters]
  );

  const selectedRecord = React.useMemo(
    () => firstRecord(visibleRecords, selectedId),
    [selectedId, visibleRecords]
  );

  const selectedTraceDetail = selectedRecord?.traceId
    ? traceDetails[selectedRecord.traceId] || { loading: false, error: "", detail: null }
    : { loading: false, error: "", detail: null };

  const hasNextCursor = activeSource !== "live" && Boolean(
    (sourceUsesTraces(activeSource) && traceCursor) ||
    (sourceUsesAudit(activeSource) && auditCursor)
  );

  const clearScheduledReload = React.useCallback(() => {
    if (reloadTimerRef.current === null) return;
    window.clearTimeout(reloadTimerRef.current);
    reloadTimerRef.current = null;
  }, []);

  const loadSummary = React.useCallback(async () => {
    const params = buildSummaryParams(filters);
    const path = params.toString() ? `/api/v1/telemetry/summary?${params}` : "/api/v1/telemetry/summary";
    setSummary(await api.get<TelemetrySummary>(path));
  }, [filters.timeRange]);

  const loadLogs = React.useCallback(async (mode: "reset" | "append" = "reset") => {
    if (activeSource === "live") {
      setLoading(false);
      setLoadingMore(false);
      return;
    }
    setError("");
    mode === "reset" ? setLoading(true) : setLoadingMore(true);
    try {
      const nextTraceRecords: LogRecord[] = [];
      const nextAuditRecords: LogRecord[] = [];
      let nextTraceCursor = mode === "append" ? traceCursor : null;
      let nextAuditCursor = mode === "append" ? auditCursor : null;

      if (sourceUsesTraces(activeSource) && (mode === "reset" || traceCursor)) {
        if (activeSource === "gate") {
          const params = new URLSearchParams({ limit: "60" });
          const from = timeRangeFrom(filters);
          if (from) params.set("from", from);
          if (filters.status !== "all") params.set("status", filters.status);
          if (mode === "append" && traceCursor) params.set("cursor", traceCursor);
          const response = await api.get<PaginatedResponse<GateMalfunctionRecord>>(`/api/v1/gate-malfunctions/history?${params}`);
          nextTraceRecords.push(...response.items.map((record) => traceRecord(gateMalfunctionRecordToTrace(record))));
          nextTraceCursor = response.next_cursor;
        } else {
          const response = await api.get<PaginatedResponse<TelemetryTrace>>(`/api/v1/telemetry/traces?${buildTraceParams(activeSource, filters, mode === "append" ? traceCursor : null)}`);
          nextTraceRecords.push(...response.items.map(traceRecord));
          nextTraceCursor = response.next_cursor;
        }
      }

      if (sourceUsesAudit(activeSource) && (mode === "reset" || auditCursor)) {
          const response = await api.get<PaginatedResponse<AuditLog>>(`/api/v1/telemetry/audit?${buildAuditParams(activeSource, filters, mode === "append" ? auditCursor : null)}`);
        nextAuditRecords.push(...response.items.map(auditRecord));
        nextAuditCursor = response.next_cursor;
      }

      setTraceRecords((current) => mode === "append" ? mergeRecords(current, nextTraceRecords) : nextTraceRecords);
      setAuditRecords((current) => mode === "append" ? mergeRecords(current, nextAuditRecords) : nextAuditRecords);
      setTraceCursor(nextTraceCursor);
      setAuditCursor(nextAuditCursor);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load logs");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [activeSource, auditCursor, filters, traceCursor]);

  const refreshLogs = React.useCallback(() => {
    loadLogs("reset").catch(() => undefined);
    loadSummary().catch(() => undefined);
  }, [loadLogs, loadSummary]);

  React.useEffect(() => {
    setSelectedId(null);
    setTraceDetails({});
    clearScheduledReload();
    loadLogs("reset").catch(() => undefined);
    loadSummary().catch(() => undefined);
  }, [activeSource, filters, clearScheduledReload]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    loadLogs("reset").catch(() => undefined);
    loadSummary().catch(() => undefined);
  }, [refreshToken]);

  React.useEffect(() => () => clearScheduledReload(), [clearScheduledReload]);

  React.useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(""), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  React.useEffect(() => {
    if (!visibleRecords.length) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !visibleRecords.some((record) => record.id === selectedId)) {
      setSelectedId(visibleRecords[0].id);
    }
  }, [selectedId, visibleRecords]);

  React.useEffect(() => {
    if (!selectedRecord || selectedRecord.kind !== "trace" || !selectedRecord.traceId || traceDetails[selectedRecord.traceId]) return;
    const traceId = selectedRecord.traceId;
    setTraceDetails((current) => ({ ...current, [traceId]: { loading: true, error: "", detail: null } }));
    const malfunctionId = selectedRecord.rawTrace?.category === "gate_malfunction"
      ? String(selectedRecord.rawTrace.context.malfunction_id || selectedRecord.rawTrace.context.id || "")
      : "";
    const request = malfunctionId
      ? api.get<GateMalfunctionRecord>(`/api/v1/gate-malfunctions/${malfunctionId}/trace`).then(gateMalfunctionRecordToTraceDetail)
      : api.get<TelemetryTraceDetail>(`/api/v1/telemetry/traces/${traceId}`);
    request
      .then((detail) => {
        setTraceDetails((current) => ({ ...current, [traceId]: { loading: false, error: "", detail } }));
      })
      .catch((detailError) => {
        setTraceDetails((current) => ({
          ...current,
          [traceId]: {
            loading: false,
            error: detailError instanceof Error ? detailError.message : "Unable to load trace detail",
            detail: null
          }
        }));
      });
  }, [selectedRecord, traceDetails]);

  React.useEffect(() => {
    if (!logs.length || activeSource === "live") return;
    let shouldReload = false;
    const nextAuditLogs: LogRecord[] = [];
    const recentLogs = logs.slice(0, 20).reverse();
    for (const realtimeLog of recentLogs) {
      const realtimeKey = realtimeLogKey(realtimeLog);
      if (processedRealtimeKeysRef.current.has(realtimeKey)) continue;
      processedRealtimeKeysRef.current.add(realtimeKey);
      if (
        realtimeLog.type.startsWith("telemetry.") ||
        realtimeLog.type.startsWith("gate_malfunction.") ||
        realtimeLog.type === "maintenance_mode.changed"
      ) {
        shouldReload = true;
      }
      if (realtimeLog.type === "audit.log.created") {
        shouldReload = true;
        const liveAuditLog = auditLogFromRealtimePayload(realtimeLog.payload);
        if (liveAuditLog) nextAuditLogs.push(auditRecord(liveAuditLog));
      }
    }
    if (processedRealtimeKeysRef.current.size > 240) {
      const staleKeys = Array.from(processedRealtimeKeysRef.current).slice(0, processedRealtimeKeysRef.current.size - 240);
      staleKeys.forEach((key) => processedRealtimeKeysRef.current.delete(key));
    }
    if (nextAuditLogs.length && sourceUsesAudit(activeSource)) {
      setAuditRecords((current) => mergeRecords(nextAuditLogs, current).slice(0, 120));
    }
    if (!shouldReload) return;
    clearScheduledReload();
    reloadTimerRef.current = window.setTimeout(() => {
      reloadTimerRef.current = null;
      loadLogs("reset").catch(() => undefined);
      loadSummary().catch(() => undefined);
    }, 900);
  }, [activeSource, clearScheduledReload, logs]);

  async function clearLogs() {
    setClearing(true);
    setError("");
    setClearError("");
    setNotice("");
    try {
      const payload = { scope: "telemetry" };
      const confirmation = await createActionConfirmation("telemetry.purge", payload, {
        target_entity: "Telemetry",
        target_label: "Telemetry traces and artifacts",
        reason: "Clear telemetry traces and artifacts"
      });
      await api.delete(`/api/v1/telemetry/purge?confirmation_token=${encodeURIComponent(confirmation.confirmation_token)}`);
      setTraceRecords([]);
      setAuditRecords([]);
      setTraceCursor(null);
      setAuditCursor(null);
      setTraceDetails({});
      processedRealtimeKeysRef.current.clear();
      onClearRealtime();
      await loadLogs("reset");
      await loadSummary();
      setClearConfirmOpen(false);
      setNotice("Telemetry traces and artifacts cleared. Audit history was preserved.");
    } catch (clearError) {
      const message = clearError instanceof Error ? clearError.message : "Unable to clear logs";
      setClearError(message);
      setError(message);
    } finally {
      setClearing(false);
    }
  }

  function saveCurrentFilter(name: string) {
    if (!name) {
      setSaveFilterError("Give this filter a name.");
      return;
    }
    const next: SavedLogsFilter = {
      id: `filter_${Date.now()}`,
      name,
      source: activeSource,
      filters
    };
    const updated = [next, ...savedFilters.filter((filter) => filter.name.toLowerCase() !== name.toLowerCase())].slice(0, 12);
    setSavedFilters(updated);
    saveSavedFilters(updated);
    setSaveFilterError("");
    setSaveFilterOpen(false);
    setNotice(`Saved filter "${name}".`);
  }

  return (
    <section className="view-stack logs-workspace">
      <LogsTopBar
        clearing={clearing}
        onClear={() => {
          setClearError("");
          setClearConfirmOpen(true);
        }}
        onExportCsv={() => exportVisible(visibleRecords, "csv")}
        onExportJson={() => exportVisible(visibleRecords, "json")}
        onRefresh={refreshLogs}
        storage={summary?.storage || null}
      />

      <LogsMetricStrip liveCount={displayedLiveRecords.length} storage={summary?.storage || null} summary={summary} />
      <LogsSourceTabs
        activeSource={activeSource}
        liveCount={displayedLiveRecords.length}
        liveSourceCounts={liveSourceCounts}
        onChange={setActiveSource}
        summary={summary}
      />
      <LogsFilterBar
        activeSource={activeSource}
        filters={filters}
        onApplySaved={(filter) => {
          setActiveSource(filter.source);
          setFilters(filter.filters);
        }}
        onChange={setFilters}
        onReset={() => setFilters(defaultLogsFilters)}
        onSave={() => {
          setSaveFilterError("");
          setSaveFilterOpen(true);
        }}
        savedFilters={savedFilters}
      />

      {error ? <div className="error-banner">{error}</div> : null}
      {notice ? <div className="success-banner">{notice}</div> : null}

      <div className="logs-main-grid">
        <LogsExplorerTable
          loading={loading}
          loadingMore={loadingMore}
          nextCursor={hasNextCursor}
          onLoadMore={() => loadLogs("append").catch(() => undefined)}
          onSelect={(record) => setSelectedId(record.id)}
          records={visibleRecords}
          selectedId={selectedRecord?.id || null}
        />
        <LogsDetailInspector
          onClose={() => setSelectedId(null)}
          record={selectedRecord}
          traceDetail={selectedTraceDetail}
        />
      </div>

      <LiveStreamDrawer
        logs={displayedLiveRecords}
        onClear={onClearRealtime}
        onToggleOpen={() => setLiveOpen((current) => !current)}
        onTogglePaused={() => setLivePaused((current) => !current)}
        open={liveOpen}
        paused={livePaused}
      />

      {clearConfirmOpen ? (
        <ClearLogsConfirmModal
          error={clearError}
          loading={clearing}
          onCancel={() => {
            if (clearing) return;
            setClearConfirmOpen(false);
            setClearError("");
          }}
          onConfirm={clearLogs}
        />
      ) : null}

      {saveFilterOpen ? (
        <SaveFilterModal
          error={saveFilterError}
          onCancel={() => setSaveFilterOpen(false)}
          onSave={saveCurrentFilter}
        />
      ) : null}
    </section>
  );
}

function mergeRecords(left: LogRecord[], right: LogRecord[]) {
  const byId = new Map<string, LogRecord>();
  [...left, ...right].forEach((record) => byId.set(record.id, record));
  return Array.from(byId.values()).sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp));
}
