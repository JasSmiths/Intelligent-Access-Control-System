import { AlertTriangle, CheckCircle2, DoorOpen, LogIn, LogOut, RefreshCcw, Terminal } from "lucide-react";

import {
  AuditLog,
  BadgeTone,
  formatDate,
  formatFileSize,
  isRecord,
  levelTone,
  nullableString,
  numberPayload,
  RealtimeMessage,
  stringPayload,
  titleCase
} from "../../shared";
import {
  auditCategorySources,
  auditCategories,
  sourceTabs,
  statusOptions,
  timeRangeOptions,
  traceCategories,
  traceCategorySources
} from "./constants";
import {
  GateMalfunctionRecord,
  LogRecord,
  LogsFilters,
  LogSourceKey,
  SavedLogsFilter,
  TelemetrySpan,
  TelemetryStorageSummary,
  TelemetrySummary,
  TelemetryTrace,
  TelemetryTraceDetail
} from "./types";

export function formatLogMegabytes(size: number) {
  const value = Math.max(0, size);
  if (value >= 1024 * 1024 * 1024) return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDuration(value: number | null | undefined) {
  const ms = Math.max(0, Number(value || 0));
  if (!ms) return "-";
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

export function levelLabel(level: string | null | undefined) {
  const normalized = String(level || "info").toLowerCase();
  if (normalized === "warning") return "WARN";
  if (normalized === "purple") return "AI ACTION";
  return normalized.toUpperCase();
}

export function outcomeTone(outcome: string): BadgeTone {
  if (outcome === "success" || outcome === "ok") return "green";
  if (outcome === "failed" || outcome === "error") return "red";
  if (outcome === "pending_confirmation" || outcome === "warning") return "amber";
  return "gray";
}

export function timeRangeFrom(filters: LogsFilters) {
  const selected = timeRangeOptions.find((option) => option.value === filters.timeRange);
  if (!selected || selected.minutes === null) return null;
  return new Date(Date.now() - selected.minutes * 60 * 1000).toISOString();
}

export function sourceLabel(source: LogSourceKey) {
  return sourceTabs.find((tab) => tab.key === source)?.label ?? titleCase(source);
}

export function statusLabel(value: string) {
  return statusOptions.find((option) => option.value === value)?.label ?? titleCase(value);
}

export function buildTraceParams(source: LogSourceKey, filters: LogsFilters, cursor: string | null, limit = 60) {
  const params = new URLSearchParams({ limit: String(limit) });
  const category = source === "all" ? "" : traceCategories[source];
  const from = timeRangeFrom(filters);
  if (category) params.set("category", category);
  if (from) params.set("from", from);
  if (filters.query.trim()) params.set("q", filters.query.trim());
  if (filters.level !== "all") params.set("level", filters.level);
  const status = traceStatusParam(filters.status);
  if (status) params.set("status", status);
  if (filters.subject.trim()) params.set("registration_number", filters.subject.trim());
  if (cursor) params.set("cursor", cursor);
  return params;
}

export function buildAuditParams(source: LogSourceKey, filters: LogsFilters, cursor: string | null, limit = 60) {
  const params = new URLSearchParams({ limit: String(limit) });
  const category = source === "all" ? "" : auditCategories[source];
  const from = timeRangeFrom(filters);
  if (category) params.set("category", category);
  if (from) params.set("from", from);
  if (filters.query.trim()) params.set("q", filters.query.trim());
  if (filters.level !== "all") params.set("level", filters.level);
  if (filters.actor.trim()) params.set("actor", filters.actor.trim());
  const outcome = auditOutcomeParam(filters.status);
  if (outcome) params.set("outcome", outcome);
  if (cursor) params.set("cursor", cursor);
  return params;
}

export function buildSummaryParams(filters: LogsFilters) {
  const params = new URLSearchParams();
  const from = timeRangeFrom(filters);
  if (from) params.set("from", from);
  return params;
}

export function traceStatusParam(status: string) {
  if (["ok", "error", "active", "resolved", "fubar"].includes(status)) return status;
  if (status === "warning") return "";
  return "";
}

export function auditOutcomeParam(status: string) {
  if (status === "ok") return "success";
  if (status === "error") return "failed";
  if (status === "pending_confirmation") return "pending_confirmation";
  return "";
}

export function traceRecord(trace: TelemetryTrace): LogRecord {
  const source = traceCategorySources[trace.category] ?? "all";
  const display = traceDisplay(trace);
  const subject = trace.registration_number || stringPayload(trace.context.target_label) || stringPayload(trace.context.vehicle) || trace.source || "System";
  const subjectDetail =
    stringPayload(trace.context.display_vehicle) ||
    stringPayload(trace.context.person_name) ||
    stringPayload(trace.context.direction) ||
    trace.access_event_id ||
    "";
  const sourceName = trace.source || sourceLabel(source);
  const summary = trace.summary || trace.error || trace.name;
  return {
    id: `trace:${trace.trace_id}`,
    kind: "trace",
    source,
    timestamp: trace.started_at,
    category: trace.category,
    sourceLabel: sourceName,
    sourceDetail: sourceLabel(source),
    action: display.title,
    actionDetail: trace.name,
    subject,
    subjectDetail,
    status: trace.status,
    level: trace.level,
    outcome: trace.status,
    durationMs: trace.duration_ms,
    actor: trace.actor || "System",
    traceId: trace.trace_id,
    requestId: null,
    summary,
    searchText: [
      sourceName,
      trace.name,
      summary,
      subject,
      subjectDetail,
      trace.actor,
      trace.source,
      trace.registration_number,
      trace.category,
      JSON.stringify(trace.context)
    ].join(" ").toLowerCase(),
    tone: display.tone,
    rawTrace: trace
  };
}

export function auditRecord(log: AuditLog): LogRecord {
  const source = auditSource(log);
  const actionLabel = titleCase(log.action.replace(/\./g, " "));
  const target = log.target_label || log.target_id || log.target_entity || "System";
  const auditSummary = typeof log.metadata.summary === "string" ? log.metadata.summary : "";
  const tone = log.category === "alfred_ai"
    ? "purple"
    : log.category === "access_presence"
      ? "green"
      : log.action.startsWith("maintenance_mode.")
        ? "amber"
        : outcomeTone(log.outcome);
  return {
    id: `audit:${log.id}`,
    kind: "audit",
    source,
    timestamp: log.timestamp,
    category: log.category,
    sourceLabel: log.actor,
    sourceDetail: sourceLabel(source),
    action: actionLabel,
    actionDetail: log.action,
    subject: target,
    subjectDetail: log.target_entity || log.target_id || "",
    status: log.outcome,
    level: log.level,
    outcome: log.outcome,
    durationMs: null,
    actor: log.actor,
    traceId: log.trace_id,
    requestId: log.request_id,
    summary: auditSummary || `${actionLabel} by ${log.actor}`,
    searchText: [
      log.action,
      actionLabel,
      log.actor,
      target,
      log.target_entity,
      log.target_id,
      log.target_label,
      log.category,
      log.outcome,
      log.level,
      JSON.stringify(log.diff),
      JSON.stringify(log.metadata)
    ].join(" ").toLowerCase(),
    tone,
    rawAudit: log
  };
}

export function liveRecord(message: RealtimeMessage, index: number): LogRecord {
  const action = message.type;
  const timestamp = message.created_at || new Date().toISOString();
  const payload = message.payload || {};
  const category = stringPayload(payload.category) || liveCategory(message.type);
  const source = liveSource(message.type, category);
  const subject =
    stringPayload(payload.target_label) ||
    stringPayload(payload.target_id) ||
    stringPayload(payload.registration_number) ||
    stringPayload(payload.id) ||
    "Realtime";
  const level = stringPayload(payload.level) || (message.type.includes("failed") || message.type.includes("error") ? "error" : "info");
  const outcome = stringPayload(payload.outcome) || (level === "error" ? "failed" : "success");
  return {
    id: `live:${message.type}:${message.created_at || index}:${stringPayload(payload.id)}`,
    kind: "live",
    source,
    timestamp,
    category,
    sourceLabel: sourceLabel(source),
    sourceDetail: "Realtime",
    action: titleCase(action.replace(/\./g, " ")),
    actionDetail: action,
    subject,
    subjectDetail: stringPayload(payload.action) || stringPayload(payload.actor),
    status: outcome,
    level,
    outcome,
    durationMs: null,
    actor: stringPayload(payload.actor) || "System",
    traceId: nullableString(payload.trace_id),
    requestId: nullableString(payload.request_id),
    summary: JSON.stringify(payload),
    searchText: [action, category, subject, JSON.stringify(payload)].join(" ").toLowerCase(),
    tone: levelTone(level),
    rawRealtime: message
  };
}

export function applyLocalFilters(records: LogRecord[], filters: LogsFilters, source: LogSourceKey) {
  const query = filters.query.trim().toLowerCase();
  const actor = filters.actor.trim().toLowerCase();
  const subject = filters.subject.trim().toLowerCase();
  return records.filter((record) => {
    if (source !== "all" && source !== record.source && source !== "live") return false;
    if (source === "live" && record.kind !== "live") return false;
    if (query && !record.searchText.includes(query)) return false;
    if (actor && !record.actor.toLowerCase().includes(actor) && !record.sourceLabel.toLowerCase().includes(actor)) return false;
    if (
      subject &&
      !record.subject.toLowerCase().includes(subject) &&
      !record.subjectDetail.toLowerCase().includes(subject) &&
      !(record.traceId || "").toLowerCase().includes(subject) &&
      !(record.requestId || "").toLowerCase().includes(subject)
    ) {
      return false;
    }
    if (filters.status !== "all" && !recordMatchesStatus(record, filters.status)) return false;
    return true;
  });
}

export function recordMatchesStatus(record: LogRecord, status: string) {
  if (status === "ok") return ["ok", "success"].includes(record.status) || record.outcome === "success";
  if (status === "warning") return record.level === "warning";
  if (status === "error") return record.status === "error" || record.level === "error" || record.outcome === "failed";
  return record.status === status || record.outcome === status;
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

export function realtimeLogKey(log: RealtimeMessage) {
  return [
    log.type,
    log.created_at || "",
    stringPayload(log.payload.id),
    stringPayload(log.payload.action),
    stringPayload(log.payload.category)
  ].join("|");
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

export function traceDisplay(trace: TelemetryTrace): { title: string; tone: BadgeTone } {
  if (trace.category === "gate_malfunction") {
    const status = stringPayload(trace.context.status || trace.status).toLowerCase();
    const gate = stringPayload(trace.context.gate_name || trace.source || "Primary gate");
    if (status === "fubar") return { title: `Gate Malfunction - ${gate}`, tone: "red" };
    if (status === "resolved") return { title: `Gate Resolved - ${gate}`, tone: "green" };
    return { title: `Gate Malfunction - ${gate}`, tone: "amber" };
  }
  if (trace.category === "dependency_updates") {
    return { title: trace.summary || trace.name, tone: trace.status === "error" ? "red" : "blue" };
  }
  if (trace.category === "automation_engine") {
    return { title: trace.summary || trace.name, tone: trace.status === "error" ? "red" : "purple" };
  }
  const decision = stringPayload(trace.context.decision).toLowerCase();
  const direction = stringPayload(trace.context.direction).toLowerCase();
  const plate = trace.registration_number || "unknown plate";
  if (decision === "denied") return { title: `Entry Denied - Plate ${plate}`, tone: "red" };
  if (direction === "exit") return { title: `Exit Granted - Plate ${plate}`, tone: "gray" };
  if (decision === "granted") return { title: `Entry Granted - Plate ${plate}`, tone: "green" };
  if (trace.status === "error") return { title: trace.name, tone: "red" };
  return { title: trace.name, tone: "blue" };
}

export function traceIcon(record: LogRecord) {
  if (record.source === "gate") return DoorOpen;
  if (record.source === "updates") return RefreshCcw;
  if (record.action.toLowerCase().includes("denied") || record.level === "error") return AlertTriangle;
  if (record.action.toLowerCase().includes("exit")) return LogOut;
  if (record.action.toLowerCase().includes("entry")) return LogIn;
  if (record.outcome === "success" || record.status === "ok") return CheckCircle2;
  return Terminal;
}

export function artifactFromSpan(span: TelemetrySpan): Record<string, unknown> | null {
  const artifact = span.output_payload.artifact;
  return isRecord(artifact) && typeof artifact.url === "string" ? artifact : null;
}

export function gateTimelineSummary(kind: string, details: Record<string, unknown>) {
  if (kind === "preceding_event") return "Closest entry or exit event before the gate was declared malfunctioning.";
  if (kind === "declared") return `Declared after the gate remained ${stringPayload(details.gate_state || "open")}.`;
  if (kind === "attempt" || kind === "manual_attempt") {
    const accepted = details.accepted === true ? "accepted" : "failed";
    return `Recovery command ${accepted}; gate state reported ${stringPayload(details.state || "unknown")}.`;
  }
  if (kind.startsWith("notification")) {
    const channel = stringPayload(details.channel || details.trigger || "workflow");
    return `Notification workflow update via ${channel}.`;
  }
  if (kind === "resolved") return `Resolved after ${formatSecondsDuration(numberPayload(details.total_downtime_seconds))}.`;
  if (kind === "fubar") return "Automated recovery stopped and manual intervention is required.";
  return stringPayload(details.detail || details.reason || "Gate malfunction timeline event.");
}

export function gateMalfunctionCountdownLabel(scheduledAt: string, now: number) {
  const target = Date.parse(scheduledAt);
  if (!Number.isFinite(target)) return "Next attempt pending";
  const remainingSeconds = Math.ceil((target - now) / 1000);
  if (remainingSeconds <= 0) return "Attempt due now";
  return `Next attempt in ${formatSecondsDuration(remainingSeconds)}`;
}

export function copyText(value: string | null | undefined) {
  if (!value) return Promise.resolve();
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  return Promise.resolve();
}

export function exportRecords(records: LogRecord[], format: "json" | "csv") {
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const filename = `iacs-logs-${timestamp}.${format}`;
  const content = format === "json" ? stringifyJson(records.map(exportableRecord)) : recordsToCsv(records);
  const type = format === "json" ? "application/json" : "text/csv";
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function exportableRecord(record: LogRecord) {
  return {
    timestamp: record.timestamp,
    source: record.source,
    category: record.category,
    action: record.actionDetail || record.action,
    subject: record.subject,
    status: record.status,
    level: record.level,
    outcome: record.outcome,
    duration_ms: record.durationMs,
    actor: record.actor,
    trace_id: record.traceId,
    request_id: record.requestId,
    summary: record.summary
  };
}

export function recordsToCsv(records: LogRecord[]) {
  const headers = ["timestamp", "source", "category", "action", "subject", "status", "level", "outcome", "duration_ms", "actor", "trace_id", "request_id", "summary"];
  const rows = records.map((record) => exportableRecord(record));
  return [
    headers.join(","),
    ...rows.map((row) => headers.map((header) => csvCell((row as Record<string, unknown>)[header])).join(","))
  ].join("\n");
}

export function csvCell(value: unknown) {
  const text = String(value ?? "");
  return `"${text.replaceAll("\"", "\"\"")}"`;
}

export function loadSavedFilters(): SavedLogsFilter[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem("iacs.logs.savedFilters") || "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isSavedFilter);
  } catch {
    return [];
  }
}

export function saveSavedFilters(filters: SavedLogsFilter[]) {
  window.localStorage.setItem("iacs.logs.savedFilters", JSON.stringify(filters));
}

export function isSavedFilter(value: unknown): value is SavedLogsFilter {
  return Boolean(
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.source === "string" &&
    isRecord(value.filters)
  );
}

export function liveCategory(type: string) {
  if (type.startsWith("telemetry.")) return "telemetry";
  if (type.startsWith("audit.")) return "audit";
  if (type.includes("gate")) return "gate_malfunction";
  if (type.includes("maintenance")) return "maintenance_mode";
  if (type.includes("whatsapp") || type.includes("discord") || type.includes("notification")) return "integrations";
  return "realtime";
}

export function liveSource(type: string, category: string): LogSourceKey {
  if (category in traceCategorySources) return traceCategorySources[category];
  if (category in auditCategorySources) return auditCategorySources[category];
  if (type.includes("chat") || type.includes("alfred")) return "ai";
  return "live";
}

export function auditSource(log: AuditLog): LogSourceKey {
  if (log.action.startsWith("maintenance_mode.")) return "maintenance";
  return auditCategorySources[log.category] ?? "crud";
}

export function metricFromSummary(summary: TelemetrySummary | null, key: LogSourceKey) {
  if (!summary) return 0;
  if (key === "all") return summary.traces.total + summary.audit.total;
  if (key === "live") return 0;
  const traceCategory = Object.entries(traceCategorySources).find(([, source]) => source === key)?.[0];
  const auditCategory = Object.entries(auditCategorySources).find(([, source]) => source === key)?.[0];
  return (traceCategory ? summary.traces.by_category[traceCategory] || 0 : 0) + (auditCategory ? summary.audit.by_category[auditCategory] || 0 : 0);
}

export function summaryMetricCards(summary: TelemetrySummary | null, storage: TelemetryStorageSummary | null, liveCount: number) {
  const errors = (summary?.traces.by_level.error || 0) + (summary?.audit.by_level.error || 0) + (summary?.audit.by_outcome.failed || 0);
  const warnings = (summary?.traces.by_level.warning || 0) + (summary?.audit.by_level.warning || 0);
  return {
    live: liveCount,
    errors,
    warnings,
    storage: formatLogMegabytes(storage?.total_size_bytes || summary?.storage.total_size_bytes || 0),
    audit: summary?.audit.total || 0
  };
}

export function formattedTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return { time: value, date: "" };
  return {
    time: date.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    date: formatDate(value)
  };
}

export function sourceUsesTraces(source: LogSourceKey) {
  return source === "all" || source === "gate" || Boolean(traceCategories[source]);
}

export function sourceUsesAudit(source: LogSourceKey) {
  return source === "all" || Boolean(auditCategories[source]);
}

export function firstRecord(records: LogRecord[], selectedId: string | null) {
  return records.find((record) => record.id === selectedId) || records[0] || null;
}
