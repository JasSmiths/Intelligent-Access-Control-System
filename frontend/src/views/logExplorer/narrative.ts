import { isRecord, stringPayload, titleCase } from "../../lib/format";
import { sourceTabs,timeRangeOptions } from "./constants";
import type {
  LogRecord,
  LogsFilters,
  LogSourceKey,
  NarrativeLogItem,
  SimplifiedLogsFilters
} from "./types";

export function simplifyLogsFilters(filters: LogsFilters, source: LogSourceKey, now = Date.now()): SimplifiedLogsFilters {
  const selectedRange = timeRangeOptions.find((option) => option.value === filters.timeRange);
  const from = selectedRange?.minutes === null || selectedRange?.minutes === undefined
    ? null
    : new Date(now - selectedRange.minutes * 60 * 1000).toISOString();
  return {
    source,
    query: filters.query.trim(),
    timeRange: filters.timeRange,
    level: filters.level,
    status: filters.status,
    actor: filters.actor.trim(),
    subject: filters.subject.trim(),
    slowOnly: filters.slowOnly,
    from
  };
}

export function deriveNarrativeLogItems(records: LogRecord[]): NarrativeLogItem[] {
  return records.map(deriveNarrativeLogItem);
}

export function deriveNarrativeLogItem(record: LogRecord): NarrativeLogItem {
  const title = narrativeTitleForRecord(record);
  const what = ledgerWhatForRecord(record, title);
  const reason = narrativeReasonForRecord(record);
  const why = ledgerWhyForRecord(record, reason);
  const supportingDetail = narrativeSupportingDetailForRecord(record);
  const details = ledgerDetailsForRecord(record, supportingDetail);
  return {
    id: `narrative:${record.id}`,
    recordId: record.id,
    kind: record.kind,
    source: record.source,
    timestamp: record.timestamp,
    title,
    what,
    reason,
    why,
    supportingDetail,
    details,
    summary: record.summary,
    subject: record.subject,
    actor: record.actor,
    status: record.status,
    level: record.level,
    outcome: record.outcome,
    tone: record.tone,
    durationMs: record.durationMs,
    traceId: record.traceId,
    requestId: record.requestId,
    sourceLabel: record.sourceLabel,
    sourceDetail: record.sourceDetail,
    searchText: [record.searchText, title, what, reason, why, supportingDetail, details].join(" ").toLowerCase(),
    raw: record
  };
}

export function narrativeTitleForRecord(record: LogRecord): string {
  if (record.rawTrace) return traceNarrativeTitle(record);
  if (record.rawAudit) {
    const target = record.subject && record.subject !== "System" ? ` - ${record.subject}` : "";
    return `${record.action}${target}`;
  }
  if (record.rawRealtime) return record.action;
  return record.action || record.summary || "Log event";
}

export function narrativeReasonForRecord(record: LogRecord): string {
  if (record.rawTrace) return traceNarrativeReason(record);
  if (record.rawAudit) {
    const metadata = record.rawAudit.metadata;
    return firstText(
      metadata.summary,
      metadata.reason,
      metadata.detail,
      metadata.message,
      `${sourceLabel(record.source)} audit event recorded as ${titleCase(record.outcome || "unknown")}.`
    );
  }
  if (record.rawRealtime) {
    const payload = record.rawRealtime.payload;
    return firstText(
      payload.reason,
      payload.detail,
      payload.message,
      payload.summary,
      `Realtime ${record.rawRealtime.type} event from ${sourceLabel(record.source)}.`
    );
  }
  return record.summary || "Operational log event.";
}

export function narrativeSupportingDetailForRecord(record: LogRecord): string {
  if (record.rawTrace) {
    const context = record.rawTrace.context;
    return firstText(
      context.display_vehicle,
      context.person_name,
      context.visitor_name,
      context.timing_classification,
      record.subjectDetail,
      record.rawTrace.access_event_id,
      record.rawTrace.trace_id
    );
  }
  if (record.rawAudit) {
    return firstText(
      record.rawAudit.target_label,
      record.rawAudit.target_entity,
      record.rawAudit.target_id,
      record.rawAudit.request_id,
      record.subjectDetail
    );
  }
  return firstText(record.subjectDetail, record.traceId, record.requestId, record.sourceDetail);
}

function traceNarrativeTitle(record: LogRecord): string {
  const trace = record.rawTrace;
  if (!trace) return record.action;
  const context = trace.context;
  if (trace.category === "gate_malfunction") {
    const gate = firstText(context.gate_name, trace.source, "Primary gate");
    const status = String(context.status || trace.status || "").toLowerCase();
    if (status === "resolved") return `Gate recovered - ${gate}`;
    if (status === "fubar") return `Gate needs manual intervention - ${gate}`;
    return `Gate malfunction active - ${gate}`;
  }
  if (trace.category === "lpr_telemetry") {
    const plate = trace.registration_number || record.subject || "unknown plate";
    const decision = String(context.decision || trace.status || "").toLowerCase();
    const direction = String(context.direction || "").toLowerCase();
    if (decision === "denied") return `Plate ${plate} denied`;
    if (direction === "exit") return `Plate ${plate} exit recorded`;
    if (decision === "granted") return `Plate ${plate} granted entry`;
    return `Plate ${plate} processed`;
  }
  if (trace.category === "automation_engine") {
    const status = String(context.status || trace.status || "").toLowerCase();
    const name = trace.name.replace(/^Automation Rule:\s*/i, "");
    if (status === "skipped") return `${name} skipped`;
    if (status === "failed" || trace.status === "error") return `${name} failed`;
    return trace.summary || `Automation - ${name}`;
  }
  if (trace.category === "dependency_updates") return trace.summary || `Dependency update - ${trace.name}`;
  return trace.summary || record.action || trace.name;
}

function traceNarrativeReason(record: LogRecord): string {
  const trace = record.rawTrace;
  if (!trace) return record.summary || "Trace event.";
  const context = trace.context;
  if (trace.error) return trace.error;
  if (trace.category === "lpr_telemetry") {
    const decision = String(context.decision || "").toLowerCase();
    const direction = String(context.direction || "").toLowerCase();
    const anomalyCount = numberFrom(context.anomaly_count);
    if (anomalyCount > 0) return `${anomalyCount} anomaly ${anomalyCount === 1 ? "was" : "were"} created for this plate decision.`;
    if (stringPayload(context.visitor_name)) return `Visitor pass matched for ${stringPayload(context.visitor_name)}.`;
    if (decision === "denied") return "Access rules did not grant this plate read.";
    if (decision === "granted" && direction === "exit") return "The movement FSM classified the read as a departure.";
    if (decision === "granted") return "The plate matched an allowed identity or visitor pass.";
  }
  if (trace.category === "gate_malfunction") {
    const status = String(context.status || trace.status || "").toLowerCase();
    if (status === "resolved") return "The gate returned to a healthy state after recovery checks.";
    if (status === "fubar") return "Automated recovery stopped and manual intervention is required.";
    return firstText(context.summary, trace.summary, "Gate recovery is still active.");
  }
  if (trace.category === "automation_engine") {
    const status = String(context.status || "").toLowerCase();
    const skipReason = automationSkipReasonFromContext(context);
    if (status === "skipped" && skipReason) return `Skipped because ${sentenceCase(skipReason)}`;
    if (trace.error) return trace.error;
    return firstText(context.reason, context.detail, trace.summary);
  }
  return trace.summary || `${sourceLabel(record.source)} trace completed with status ${titleCase(trace.status || "unknown")}.`;
}

function ledgerWhatForRecord(record: LogRecord, title: string): string {
  const action = record.actionDetail || record.action || title;
  const context = record.rawTrace?.context || {};
  if (record.rawTrace?.category === "lpr_telemetry") {
    const plate = record.rawTrace.registration_number || record.subject || "unknown plate";
    const identity = firstText(context.person_name, context.visitor_name, context.owner);
    const vehicle = firstText(context.display_vehicle, context.vehicle);
    const decision = String(context.decision || record.status || "").toLowerCase();
    const direction = String(context.direction || "").toLowerCase();
    const vehicleText = vehicle && vehicle !== plate ? `${vehicle} (${plate})` : plate;
    if (decision === "denied") return identity ? `${identity} denied in ${vehicleText}` : `Vehicle ${plate} denied`;
    if (direction === "exit") return identity ? `${identity} departed in ${vehicleText}` : `Vehicle ${plate} departed`;
    if (identity) return `${identity} arrived in ${vehicleText}`;
    return `Vehicle ${plate} arrived`;
  }
  if (record.source === "live") {
    if (action === "connection.ready") return "Live stream connected";
    if (action === "protect.camera.updated") return "Camera status updated";
    if (action === "vehicle_visual_detection.observed") return "Vehicle observed";
    if (action === "protect.event.detected") return "Protect event received";
  }
  if (record.rawAudit) {
    const actionText = readableAction(record.rawAudit.action);
    if (actionText.includes("confirmation consumed")) return "Action confirmation used";
    if (actionText.includes("package check")) return "Dependency package checked";
    return titleCase(actionText);
  }
  return stripNoise(title)
    .replace(/^Real World /, "")
    .replace(/^Access Event Reconciliation Checked - /, "Reconciliation checked - ");
}

function ledgerWhyForRecord(record: LogRecord, reason: string): string {
  if (record.rawRealtime) {
    const type = record.rawRealtime.type;
    if (type === "connection.ready") return "Realtime socket reported ready.";
    if (type === "protect.camera.updated") return "UniFi Protect sent a camera update.";
    if (type === "vehicle_visual_detection.observed") return "Visual detection matched a vehicle near the gate.";
    if (type === "protect.event.detected") return "UniFi Protect reported a camera event.";
  }
  if (record.rawAudit) {
    const actionText = readableAction(record.rawAudit.action);
    if (record.rawAudit.outcome === "success") return `${record.actor} completed ${actionText}.`;
    if (record.rawAudit.outcome === "failed") return `${record.actor} attempted ${actionText} and it failed.`;
  }
  return stripNoise(reason || record.summary || "No reason recorded.");
}

function ledgerDetailsForRecord(record: LogRecord, supportingDetail: string): string {
  const pieces = [
    record.subject && record.subject !== "Realtime" ? record.subject : "",
    supportingDetail,
    record.actor && record.actor !== "System" ? record.actor : "",
    record.sourceLabel
  ].filter(Boolean);
  return pieces.length ? pieces.join(" / ") : sourceLabel(record.source);
}

function sourceLabel(source: LogSourceKey) {
  return sourceTabs.find((tab) => tab.key === source)?.label || titleCase(source);
}

function stripNoise(value: string): string {
  return value
    .replace(/^Realtime /, "")
    .replace(/ event from Live\.?$/i, ".")
    .replace(/\s+/g, " ")
    .trim();
}

function readableAction(value: string): string {
  return value.replace(/[._]+/g, " ").replace(/\s+/g, " ").trim();
}

function automationSkipReasonFromContext(context: Record<string, unknown>): string {
  const direct = firstText(context.skip_reason, context.disabled_reason, context.reason, context.error, context.run_error);
  if (direct) return direct;
  const conditionResults = Array.isArray(context.condition_results) ? context.condition_results : [];
  for (const result of conditionResults) {
    if (isRecord(result) && result.passed === false) {
      const reason = firstText(result.disabled_reason, result.reason, result.error, result.detail, result.message, result.description);
      if (reason) return reason;
    }
  }
  const actionResults = Array.isArray(context.action_results) ? context.action_results : [];
  for (const result of actionResults) {
    if (!isRecord(result)) continue;
    const status = String(result.status || "").toLowerCase();
    if (status !== "skipped" && status !== "failed") continue;
    const reason = firstText(result.disabled_reason, result.reason, result.error, result.detail, result.message, result.description);
    if (reason) return reason;
  }
  return "";
}

function sentenceCase(value: string): string {
  const cleaned = value.trim();
  if (!cleaned) return cleaned;
  return `${cleaned.charAt(0).toLowerCase()}${cleaned.slice(1)}`;
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (isRecord(value)) {
      const nested: string = firstText(value.summary, value.reason, value.detail, value.message, value.label, value.name);
      if (nested) return nested;
    }
  }
  return "";
}

function numberFrom(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}
