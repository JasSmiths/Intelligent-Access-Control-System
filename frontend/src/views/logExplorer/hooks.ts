import React from "react";

import {
  api,
  AuditLog,
  isAbortError,
  RealtimeMessage
} from "../../shared";
import {
  GateMalfunctionRecord,
  LogRecord,
  LogsFilters,
  LogSourceKey,
  PaginatedResponse,
  TelemetrySummary,
  TelemetryTrace,
  TelemetryTraceDetail,
  TraceDetailState
} from "./types";
import {
  auditLogFromRealtimePayload,
  auditRecord,
  buildAuditParams,
  buildSummaryParams,
  buildTraceParams,
  gateMalfunctionRecordToTrace,
  gateMalfunctionRecordToTraceDetail,
  liveRecord,
  realtimeLogKey,
  sourceUsesAudit,
  sourceUsesTraces,
  timeRangeFrom,
  traceRecord
} from "./utils";

const emptyTraceDetailState: TraceDetailState = { loading: false, error: "", detail: null };

type LoadMode = "reset" | "append";

export function useDisplayedLiveRecords(logs: RealtimeMessage[]) {
  const [liveOpen, setLiveOpen] = React.useState(true);
  const [livePaused, setLivePaused] = React.useState(false);
  const [displayedLiveRecords, setDisplayedLiveRecords] = React.useState<LogRecord[]>([]);

  const normalizedLiveRecords = React.useMemo(
    () => logs.map((log, index) => liveRecord(log, index)),
    [logs]
  );

  React.useEffect(() => {
    if (!livePaused) setDisplayedLiveRecords(normalizedLiveRecords);
  }, [livePaused, normalizedLiveRecords]);

  const clearDisplayedLiveRecords = React.useCallback(() => {
    setDisplayedLiveRecords([]);
  }, []);

  const liveSourceCounts = React.useMemo(() => {
    const counts = {} as Partial<Record<LogSourceKey, number>>;
    displayedLiveRecords.forEach((record) => {
      counts[record.source] = (counts[record.source] || 0) + 1;
    });
    counts.all = displayedLiveRecords.length;
    counts.live = displayedLiveRecords.length;
    return counts;
  }, [displayedLiveRecords]);

  return {
    clearDisplayedLiveRecords,
    displayedLiveRecords,
    liveOpen,
    livePaused,
    liveSourceCounts,
    setLiveOpen,
    setLivePaused
  };
}

export function useLogsData({
  activeSource,
  filters,
  refreshToken
}: {
  activeSource: LogSourceKey;
  filters: LogsFilters;
  refreshToken: number;
}) {
  const [traceRecords, setTraceRecords] = React.useState<LogRecord[]>([]);
  const [auditRecords, setAuditRecords] = React.useState<LogRecord[]>([]);
  const [traceCursor, setTraceCursor] = React.useState<string | null>(null);
  const [auditCursor, setAuditCursor] = React.useState<string | null>(null);
  const [summary, setSummary] = React.useState<TelemetrySummary | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [error, setError] = React.useState("");
  const lastRefreshTokenRef = React.useRef(refreshToken);
  const summaryLoadSequenceRef = React.useRef(0);
  const summaryLoadAbortRef = React.useRef<AbortController | null>(null);
  const logsLoadSequenceRef = React.useRef(0);
  const logsLoadAbortRef = React.useRef<AbortController | null>(null);
  const traceCursorRef = React.useRef<string | null>(null);
  const auditCursorRef = React.useRef<string | null>(null);

  const setTraceCursorValue = React.useCallback((value: string | null) => {
    traceCursorRef.current = value;
    setTraceCursor(value);
  }, []);

  const setAuditCursorValue = React.useCallback((value: string | null) => {
    auditCursorRef.current = value;
    setAuditCursor(value);
  }, []);

  const loadSummary = React.useCallback(async () => {
    const sequence = summaryLoadSequenceRef.current + 1;
    summaryLoadSequenceRef.current = sequence;
    summaryLoadAbortRef.current?.abort();
    const controller = new AbortController();
    summaryLoadAbortRef.current = controller;
    const params = buildSummaryParams(filters);
    const path = params.toString() ? `/api/v1/telemetry/summary?${params}` : "/api/v1/telemetry/summary";

    try {
      const nextSummary = await api.get<TelemetrySummary>(path, { signal: controller.signal });
      if (summaryLoadSequenceRef.current !== sequence) return;
      setSummary(nextSummary);
    } catch (summaryError) {
      if (isAbortError(summaryError)) return;
      if (summaryLoadSequenceRef.current !== sequence) return;
      throw summaryError;
    } finally {
      if (summaryLoadSequenceRef.current === sequence && summaryLoadAbortRef.current === controller) {
        summaryLoadAbortRef.current = null;
      }
    }
  }, [filters]);

  const loadLogs = React.useCallback(async (mode: LoadMode = "reset") => {
    const sequence = logsLoadSequenceRef.current + 1;
    logsLoadSequenceRef.current = sequence;
    logsLoadAbortRef.current?.abort();
    const controller = new AbortController();
    logsLoadAbortRef.current = controller;

    if (activeSource === "live") {
      setLoading(false);
      setLoadingMore(false);
      if (logsLoadAbortRef.current === controller) logsLoadAbortRef.current = null;
      return;
    }

    setError("");
    mode === "reset" ? setLoading(true) : setLoadingMore(true);

    try {
      const nextTraceRecords: LogRecord[] = [];
      const nextAuditRecords: LogRecord[] = [];
      const currentTraceCursor = mode === "append" ? traceCursorRef.current : null;
      const currentAuditCursor = mode === "append" ? auditCursorRef.current : null;
      let nextTraceCursor = currentTraceCursor;
      let nextAuditCursor = currentAuditCursor;

      if (sourceUsesTraces(activeSource) && (mode === "reset" || currentTraceCursor)) {
        if (activeSource === "gate") {
          const params = new URLSearchParams({ limit: "60" });
          const from = timeRangeFrom(filters);
          if (from) params.set("from", from);
          if (filters.status !== "all") params.set("status", filters.status);
          if (currentTraceCursor) params.set("cursor", currentTraceCursor);
          const response = await api.get<PaginatedResponse<GateMalfunctionRecord>>(
            `/api/v1/gate-malfunctions/history?${params}`,
            { signal: controller.signal }
          );
          nextTraceRecords.push(...response.items.map((record) => traceRecord(gateMalfunctionRecordToTrace(record))));
          nextTraceCursor = response.next_cursor;
        } else {
          const response = await api.get<PaginatedResponse<TelemetryTrace>>(
            `/api/v1/telemetry/traces?${buildTraceParams(activeSource, filters, currentTraceCursor)}`,
            { signal: controller.signal }
          );
          nextTraceRecords.push(...response.items.map(traceRecord));
          nextTraceCursor = response.next_cursor;
        }
      }

      if (sourceUsesAudit(activeSource) && (mode === "reset" || currentAuditCursor)) {
        const response = await api.get<PaginatedResponse<AuditLog>>(
          `/api/v1/telemetry/audit?${buildAuditParams(activeSource, filters, currentAuditCursor)}`,
          { signal: controller.signal }
        );
        nextAuditRecords.push(...response.items.map(auditRecord));
        nextAuditCursor = response.next_cursor;
      }

      if (logsLoadSequenceRef.current !== sequence) return;
      setTraceRecords((current) => mode === "append" ? mergeRecords(current, nextTraceRecords) : nextTraceRecords);
      setAuditRecords((current) => mode === "append" ? mergeRecords(current, nextAuditRecords) : nextAuditRecords);
      setTraceCursorValue(nextTraceCursor);
      setAuditCursorValue(nextAuditCursor);
    } catch (loadError) {
      if (isAbortError(loadError)) return;
      if (logsLoadSequenceRef.current !== sequence) return;
      setError(loadError instanceof Error ? loadError.message : "Unable to load logs");
    } finally {
      if (logsLoadSequenceRef.current === sequence) {
        setLoading(false);
        setLoadingMore(false);
        if (logsLoadAbortRef.current === controller) {
          logsLoadAbortRef.current = null;
        }
      }
    }
  }, [activeSource, filters, setAuditCursorValue, setTraceCursorValue]);

  const refresh = React.useCallback(async () => {
    const results = await Promise.allSettled([loadLogs("reset"), loadSummary()]);
    const failed = results.find((result) => result.status === "rejected");
    if (failed?.status === "rejected") throw failed.reason;
  }, [loadLogs, loadSummary]);

  const loadMore = React.useCallback(() => loadLogs("append"), [loadLogs]);

  const clearRecords = React.useCallback(() => {
    setTraceRecords([]);
    setAuditRecords([]);
    setTraceCursorValue(null);
    setAuditCursorValue(null);
  }, [setAuditCursorValue, setTraceCursorValue]);

  const prependAuditRecords = React.useCallback((records: LogRecord[]) => {
    if (!records.length) return;
    setAuditRecords((current) => mergeRecords(records, current).slice(0, 120));
  }, []);

  React.useEffect(() => {
    refresh().catch(() => undefined);
  }, [refresh]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    refresh().catch(() => undefined);
  }, [refresh, refreshToken]);

  React.useEffect(() => () => {
    summaryLoadSequenceRef.current += 1;
    summaryLoadAbortRef.current?.abort();
    logsLoadSequenceRef.current += 1;
    logsLoadAbortRef.current?.abort();
  }, []);

  const hasNextCursor = activeSource !== "live" && Boolean(
    (sourceUsesTraces(activeSource) && traceCursor) ||
    (sourceUsesAudit(activeSource) && auditCursor)
  );

  return {
    auditRecords,
    clearRecords,
    error,
    hasNextCursor,
    loading,
    loadingMore,
    loadMore,
    prependAuditRecords,
    refresh,
    setError,
    summary,
    traceRecords
  };
}

export function useLogsRealtimeRefresh({
  activeSource,
  logs,
  prependAuditRecords,
  refresh,
  resetKey
}: {
  activeSource: LogSourceKey;
  logs: RealtimeMessage[];
  prependAuditRecords: (records: LogRecord[]) => void;
  refresh: () => Promise<void>;
  resetKey: string;
}) {
  const reloadTimerRef = React.useRef<number | null>(null);
  const processedRealtimeKeysRef = React.useRef<Set<string>>(new Set());

  const clearScheduledReload = React.useCallback(() => {
    if (reloadTimerRef.current === null) return;
    window.clearTimeout(reloadTimerRef.current);
    reloadTimerRef.current = null;
  }, []);

  const clearRealtimeState = React.useCallback(() => {
    clearScheduledReload();
    processedRealtimeKeysRef.current.clear();
  }, [clearScheduledReload]);

  React.useEffect(() => {
    clearScheduledReload();
  }, [clearScheduledReload, resetKey]);

  React.useEffect(() => () => {
    clearScheduledReload();
  }, [clearScheduledReload]);

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

    if (sourceUsesAudit(activeSource)) {
      prependAuditRecords(nextAuditLogs);
    }

    if (!shouldReload) return;
    clearScheduledReload();
    reloadTimerRef.current = window.setTimeout(() => {
      reloadTimerRef.current = null;
      refresh().catch(() => undefined);
    }, 900);
  }, [activeSource, clearScheduledReload, logs, prependAuditRecords, refresh]);

  return { clearRealtimeState };
}

export function useSelectedLogRecord(records: LogRecord[], resetKey: string) {
  const [selectedId, setSelectedId] = React.useState<string | null>(null);

  const selectedRecord = React.useMemo(
    () => records.find((record) => record.id === selectedId) || null,
    [records, selectedId]
  );

  React.useEffect(() => {
    setSelectedId(null);
  }, [resetKey]);

  React.useEffect(() => {
    if (!records.length) {
      setSelectedId(null);
      return;
    }
    if (selectedId && !records.some((record) => record.id === selectedId)) {
      setSelectedId(null);
    }
  }, [records, selectedId]);

  return {
    selectedId,
    selectedRecord,
    setSelectedId
  };
}

export function useTraceDetail(record: LogRecord | null, resetKey: string) {
  const [traceDetails, setTraceDetails] = React.useState<Record<string, TraceDetailState>>({});
  const traceDetailsRef = React.useRef<Record<string, TraceDetailState>>({});
  const selectedTraceId = record?.kind === "trace" ? record.traceId : null;
  const selectedMalfunctionId = record?.kind === "trace" && record.rawTrace?.category === "gate_malfunction"
    ? String(record.rawTrace.context.malfunction_id || record.rawTrace.context.id || "")
    : "";

  const updateTraceDetails = React.useCallback((updater: (current: Record<string, TraceDetailState>) => Record<string, TraceDetailState>) => {
    setTraceDetails((current) => {
      const next = updater(current);
      traceDetailsRef.current = next;
      return next;
    });
  }, []);

  React.useEffect(() => {
    updateTraceDetails(() => ({}));
  }, [resetKey, updateTraceDetails]);

  React.useEffect(() => {
    if (!selectedTraceId || traceDetailsRef.current[selectedTraceId]) return;

    const traceId = selectedTraceId;
    updateTraceDetails((current) => ({ ...current, [traceId]: { loading: true, error: "", detail: null } }));
    const controller = new AbortController();
    const request = selectedMalfunctionId
      ? api.get<GateMalfunctionRecord>(`/api/v1/gate-malfunctions/${selectedMalfunctionId}/trace`, {
        signal: controller.signal
      }).then(gateMalfunctionRecordToTraceDetail)
      : api.get<TelemetryTraceDetail>(`/api/v1/telemetry/traces/${traceId}`, { signal: controller.signal });

    request
      .then((detail) => {
        updateTraceDetails((current) => ({ ...current, [traceId]: { loading: false, error: "", detail } }));
      })
      .catch((detailError) => {
        if (isAbortError(detailError)) return;
        updateTraceDetails((current) => ({
          ...current,
          [traceId]: {
            loading: false,
            error: detailError instanceof Error ? detailError.message : "Unable to load trace detail",
            detail: null
          }
        }));
      });

    return () => controller.abort();
  }, [resetKey, selectedMalfunctionId, selectedTraceId, updateTraceDetails]);

  const selectedTraceDetail = selectedTraceId
    ? traceDetails[selectedTraceId] || emptyTraceDetailState
    : emptyTraceDetailState;

  const clearTraceDetails = React.useCallback(() => {
    updateTraceDetails(() => ({}));
  }, [updateTraceDetails]);

  return { clearTraceDetails, selectedTraceDetail };
}

export function mergeRecords(left: LogRecord[], right: LogRecord[]) {
  const byId = new Map<string, LogRecord>();
  [...left, ...right].forEach((record) => byId.set(record.id, record));
  return sortRecordsByNewest(Array.from(byId.values()));
}

export function sortRecordsByNewest(records: LogRecord[]) {
  return records
    .map((record, index) => ({ record, index, timestamp: Date.parse(record.timestamp) || 0 }))
    .sort((left, right) => right.timestamp - left.timestamp || left.index - right.index)
    .map((item) => item.record);
}
