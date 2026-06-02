import { isRecord, nullableString, stringPayload } from "../../lib/format";
import { lprWaterfallPhaseLabels,lprWaterfallSlowThresholdsMs } from "./constants";
import type {
  LprTimingObservation,
  LprWaterfallModel,
  LprWaterfallPhase,
  LprWaterfallResponse,
  LprWaterfallStep,
  LprWaterfallStepStatus,
  SlowStepWarning,
  TelemetrySpan,
  TelemetryTraceDetail
} from "./types";

const friendlyStepLabels: Record<string, string> = {
  "Camera Capture to Webhook Receipt": "Camera read reached IACS",
  "Webhook Receipt to Debounce Finalization": "Choose best plate read",
  "Plate Verification against Vehicle DB": "Match plate to vehicle",
  "Schedule & Access Rule Evaluation": "Check access rules",
  "Direction Classification": "Decide entry or exit",
  "Presence Timing Classification": "Check arrival timing",
  "DVLA Vehicle Enrichment": "Refresh DVLA details",
  "Vehicle Visual Attribute Match": "Compare camera vehicle details",
  "Persist Access Event, Presence, and Anomalies": "Save access decision",
  "Capture Access Event Snapshot": "Save camera snapshot",
  "Gate Command Saga - Open": "Open gate",
  "Garage Door Command": "Open garage door",
  "Notification Action Sent": "Send notification"
};

export function deriveLprWaterfallModel(
  detail: TelemetryTraceDetail | null | undefined,
  response?: LprWaterfallResponse | null
): LprWaterfallModel | null {
  const observations = observationsFromResponse(response);
  const rawSteps = [
    ...(detail ? stepsFromTelemetryDetail(detail) : []),
    ...(response ? stepsFromResponse(response) : []),
    ...observations.map((observation, index) => stepFromObservation(observation, index))
  ];
  const steps = attachWarnings(normalizeStepOffsets(rawSteps, detail?.started_at || nullableString(response?.started_at)));
  if (!detail && !steps.length && !observations.length) return null;

  const traceId = detail?.trace_id || nullableString(response?.trace_id);
  const registrationNumber = detail?.registration_number || nullableString(response?.registration_number) || firstObservationPlate(observations);
  const startedAt = detail?.started_at || nullableString(response?.started_at) || firstStepTime(steps);
  const endedAt = detail?.ended_at || nullableString(response?.ended_at) || lastStepTime(steps);
  const totalDurationMs = Math.max(
    numberOrNull(detail?.duration_ms) || 0,
    numberOrNull(response?.duration_ms) || 0,
    ...steps.map((step) => Math.max(0, step.offsetMs + (step.durationMs || 0))),
    durationBetween(startedAt, endedAt) || 0
  );
  const warnings = steps.map((step) => step.warning).filter((warning): warning is SlowStepWarning => Boolean(warning));
  const decision = stringOrNull(detail?.context.decision);
  const direction = stringOrNull(detail?.context.direction);
  const summary = detail?.summary || nullableString(response?.summary) || "";

  return {
    id: traceId ? `lpr-waterfall:${traceId}` : `lpr-waterfall:${registrationNumber || "diagnostic"}`,
    traceId,
    registrationNumber,
    title: nullableString(response?.title) || `LPR waterfall${registrationNumber ? ` - ${registrationNumber}` : ""}`,
    reason: nullableString(response?.reason) || waterfallReason(detail, steps, warnings, summary),
    source: detail?.source || firstText(response?.source, observations[0]?.source, "LPR"),
    decision,
    direction,
    status: detail?.status || firstStepStatus(steps),
    startedAt,
    endedAt,
    totalDurationMs,
    steps,
    warnings,
    observations,
    summary
  };
}

export function deriveLprWaterfallSteps(detail: TelemetryTraceDetail): LprWaterfallStep[] {
  return attachWarnings(normalizeStepOffsets(stepsFromTelemetryDetail(detail), detail.started_at));
}

export function lprWaterfallFromResponse(response: LprWaterfallResponse | null | undefined): LprWaterfallModel | null {
  return deriveLprWaterfallModel(null, response);
}

export function lprWaterfallStepsFromResponse(response: LprWaterfallResponse | null | undefined): LprWaterfallStep[] {
  if (!response) return [];
  return attachWarnings(normalizeStepOffsets([
    ...stepsFromResponse(response),
    ...observationsFromResponse(response).map((observation, index) => stepFromObservation(observation, index))
  ], nullableString(response.started_at)));
}

function stepsFromTelemetryDetail(detail: TelemetryTraceDetail): LprWaterfallStep[] {
  return [...detail.spans]
    .sort((left, right) => (left.step_order - right.step_order) || Date.parse(left.started_at) - Date.parse(right.started_at))
    .map(stepFromSpan);
}

function stepFromSpan(span: TelemetrySpan): LprWaterfallStep {
  const phase = phaseForText(`${span.name} ${span.category}`);
  const output = span.output_payload || {};
  const status = statusFromSpan(span, output);
  return {
    id: span.span_id || span.id,
    phase,
    label: friendlySpanLabel(span.name, phase),
    source: span.category || "telemetry",
    startedAt: span.started_at,
    endedAt: span.ended_at,
    offsetMs: 0,
    durationMs: numberOrNull(span.duration_ms),
    status,
    tone: toneForStatus(status),
    reason: reasonForSpan(span, output, phase),
    detail: detailForSpan(span, output),
    input: span.input_payload,
    output,
    raw: span
  };
}

function stepsFromResponse(response: LprWaterfallResponse): LprWaterfallStep[] {
  const waterfall = isRecord(response.waterfall) ? response.waterfall : null;
  const rawSteps = [
    ...(Array.isArray(response.steps) ? response.steps : []),
    ...(waterfall && Array.isArray(waterfall.steps) ? waterfall.steps : [])
  ];
  return rawSteps
    .map((step, index) => isRecord(step) ? stepFromResponseStep(step, index) : null)
    .filter((step): step is LprWaterfallStep => Boolean(step));
}

function stepFromResponseStep(step: Record<string, unknown>, index: number): LprWaterfallStep {
  const phase = phaseForText(firstText(step.phase, step.kind, step.name, step.label, step.source_detail, step.source));
  const status = normalizeStatus(firstText(step.status, step.outcome, "ok"));
  const startedAt = firstText(step.startedAt, step.started_at, step.captured_at, step.received_at) || null;
  const endedAt = firstText(step.endedAt, step.ended_at, step.finished_at, step.received_at) || null;
  const rawLabel = firstText(step.label, step.title, step.name, lprWaterfallPhaseLabels[phase]);
  return {
    id: firstText(step.id, step.step_id, step.span_id, `${phase}:${index}`),
    phase,
    label: friendlySpanLabel(rawLabel, phase),
    source: firstText(step.source, step.source_detail, "diagnostic"),
    startedAt,
    endedAt,
    offsetMs: numberOrNull(step.offsetMs) || numberOrNull(step.offset_ms) || 0,
    durationMs: firstNumber(step.durationMs, step.duration_ms, step.captured_to_received_ms),
    status,
    tone: toneForStatus(status),
    reason: reasonForResponseStep(step, phase),
    detail: firstText(step.detail, step.description, step.source_detail, step.payload_path),
    input: isRecord(step.input) ? step.input : isRecord(step.input_payload) ? step.input_payload : undefined,
    output: isRecord(step.output) ? step.output : isRecord(step.output_payload) ? step.output_payload : undefined,
    raw: step
  };
}

function reasonForResponseStep(step: Record<string, unknown>, phase: LprWaterfallPhase) {
  if (phase === "persistence") {
    return "Saved the access decision, presence update, movement state, and anomaly checks.";
  }
  return firstText(step.reason, step.summary, step.detail, step.message);
}

function observationsFromResponse(response: LprWaterfallResponse | null | undefined): LprTimingObservation[] {
  if (!response) return [];
  const rows = Array.isArray(response.observations)
    ? response.observations
    : Array.isArray(response.recent_lpr_timing_observations)
      ? response.recent_lpr_timing_observations
      : [];
  return rows.map(normalizeObservation).filter((row): row is LprTimingObservation => Boolean(row));
}

function normalizeObservation(value: unknown): LprTimingObservation | null {
  if (!isRecord(value)) return null;
  return {
    ...value,
    id: nullableString(value.id),
    source: nullableString(value.source),
    source_detail: nullableString(value.source_detail),
    registration_number: nullableString(value.registration_number),
    raw_value: nullableString(value.raw_value),
    candidate_kind: nullableString(value.candidate_kind),
    received_at: nullableString(value.received_at),
    captured_at: nullableString(value.captured_at),
    captured_to_received_ms: numberOrNull(value.captured_to_received_ms),
    ms_from_access_event_time: numberOrNull(value.ms_from_access_event_time),
    event_id: nullableString(value.event_id),
    camera_id: nullableString(value.camera_id),
    camera_name: nullableString(value.camera_name),
    confidence: numberOrNull(value.confidence),
    confidence_scale: nullableString(value.confidence_scale),
    protect_action: nullableString(value.protect_action),
    protect_model: nullableString(value.protect_model),
    payload_path: nullableString(value.payload_path)
  };
}

function stepFromObservation(observation: LprTimingObservation, index: number): LprWaterfallStep {
  const source = firstText(observation.source, observation.source_detail, "diagnostic");
  const phase = observation.source === "webhook" ? "webhook" : "capture";
  const durationMs = firstNumber(
    observation.captured_to_received_ms,
    durationBetween(observation.captured_at || null, observation.received_at || null)
  );
  const status = durationMs !== null && durationMs >= lprWaterfallSlowThresholdsMs.observation ? "warning" : "ok";
  const plate = firstText(observation.registration_number, observation.raw_value, "plate");
  return {
    id: observation.id || `observation:${source}:${index}`,
    phase,
    label: phase === "webhook" ? "Webhook observed plate" : "Protect observed plate",
    source,
    startedAt: observation.captured_at || observation.received_at || null,
    endedAt: observation.received_at || observation.captured_at || null,
    offsetMs: 0,
    durationMs,
    status,
    tone: toneForStatus(status),
    reason: durationMs !== null
      ? `${source} reported ${plate} after ${Math.round(durationMs)}ms.`
      : `${source} reported ${plate}.`,
    detail: firstText(observation.camera_name, observation.source_detail, observation.payload_path, observation.candidate_kind),
    raw: observation
  };
}

function normalizeStepOffsets(steps: LprWaterfallStep[], preferredStart?: string | null): LprWaterfallStep[] {
  const base = parseTime(preferredStart) ?? Math.min(
    ...steps
      .map((step) => parseTime(step.startedAt) ?? parseTime(step.endedAt))
      .filter((value): value is number => Number.isFinite(value))
  );
  const baseTime = Number.isFinite(base) ? base : 0;
  return [...steps]
    .sort((left, right) => (parseTime(left.startedAt) ?? parseTime(left.endedAt) ?? 0) - (parseTime(right.startedAt) ?? parseTime(right.endedAt) ?? 0))
    .map((step) => {
      const parsedStart = parseTime(step.startedAt);
      const parsedEnd = parseTime(step.endedAt);
      const durationMs = step.durationMs ?? (
        parsedStart !== null && parsedEnd !== null
          ? Math.max(0, parsedEnd - parsedStart)
          : null
      );
      return {
        ...step,
        offsetMs: parsedStart !== null ? Math.max(0, parsedStart - baseTime) : Math.max(0, step.offsetMs),
        durationMs
      };
    });
}

function attachWarnings(steps: LprWaterfallStep[]): LprWaterfallStep[] {
  return steps.map((step) => {
    const warning = slowWarningForStep(step, steps);
    const status = warning && step.status === "ok" ? "warning" : step.status;
    return {
      ...step,
      status,
      tone: toneForStatus(status),
      warning
    };
  });
}

function slowWarningForStep(step: LprWaterfallStep, steps: LprWaterfallStep[]): SlowStepWarning | undefined {
  if (step.durationMs === null) return undefined;
  const thresholdMs = slowThresholdForPhase(step.phase);
  if (step.durationMs < thresholdMs) return undefined;
  const severity = step.durationMs >= lprWaterfallSlowThresholdsMs.critical ? "critical" : "warning";
  return {
    stepId: step.id,
    severity,
    title: `${step.label} was slower than usual`,
    reason: slowWarningReasonForStep(step, thresholdMs, steps),
    durationMs: step.durationMs,
    thresholdMs
  };
}

function slowWarningReasonForStep(step: LprWaterfallStep, thresholdMs: number, steps: LprWaterfallStep[]) {
  const duration = humanDuration(step.durationMs);
  const target = humanDuration(thresholdMs);
  if (step.phase === "webhook" || step.phase === "capture") {
    return `The camera-to-IACS handoff took ${duration}. Target is under ${target}. This measures the gap between the camera reading the plate and IACS receiving it; slow results point to the camera, network, or UniFi Protect delivery.`;
  }
  if (step.phase === "identity") {
    return `Matching the plate to a person, vehicle, or visitor pass took ${duration}. Target is under ${target}. Slow results here usually point to database lookup time.`;
  }
  if (step.phase === "schedule") {
    return `Checking whether this vehicle was allowed in took ${duration}. Target is under ${target}. This step applies schedules, overrides, and access rules.`;
  }
  if (step.phase === "persistence") {
    const child = largestNestedStep(step, steps);
    const childText = child
      ? ` The largest recorded part inside that window was ${child.label.toLowerCase()} (${humanDuration(child.durationMs)}).`
      : " This trace did not record a smaller sub-step as the only cause.";
    return `Finalising the entry took ${duration}. Target is under ${target}. This saves the access record, presence update, movement state, and anomaly checks after the plate decision.${childText}`;
  }
  if (step.phase === "snapshot") {
    return `Saving the camera snapshot took ${duration}. Target is under ${target}. Slow results here usually point to camera image retrieval or file storage.`;
  }
  if (step.phase === "gate") {
    return `Opening the gate took ${duration}. Target is under ${target}. This is the command handoff to the configured gate provider and its accepted response.`;
  }
  if (step.phase === "garage") {
    return `Opening the garage door took ${duration}. Target is under ${target}. This is the command handoff to the configured garage-door provider and its accepted response.`;
  }
  if (step.phase === "notification") {
    return `Sending the notification took ${duration}. Target is under ${target}. Slow results here point to the notification provider or channel.`;
  }
  return `${step.label} took ${duration}. Target is under ${target}.`;
}

function largestNestedStep(step: LprWaterfallStep, steps: LprWaterfallStep[]) {
  if (step.durationMs === null) return null;
  const start = step.offsetMs;
  const end = step.offsetMs + step.durationMs;
  const nestedPhases = new Set<LprWaterfallPhase>(["snapshot", "gate", "garage", "notification"]);
  return steps
    .filter((candidate) => {
      if (candidate.id === step.id || candidate.durationMs === null || !nestedPhases.has(candidate.phase)) return false;
      const candidateStart = candidate.offsetMs;
      const candidateEnd = candidate.offsetMs + candidate.durationMs;
      return candidateStart >= start && candidateEnd <= end + 25;
    })
    .sort((left, right) => (right.durationMs || 0) - (left.durationMs || 0))[0] || null;
}

function slowThresholdForPhase(phase: LprWaterfallPhase) {
  if (phase === "debounce") return lprWaterfallSlowThresholdsMs.debounce;
  if (phase === "gate" || phase === "garage") return lprWaterfallSlowThresholdsMs.gate;
  if (phase === "capture" || phase === "webhook" || phase === "diagnostic") return lprWaterfallSlowThresholdsMs.observation;
  if (phase === "integration" || phase === "snapshot" || phase === "notification") return lprWaterfallSlowThresholdsMs.integration;
  return lprWaterfallSlowThresholdsMs.default;
}

function reasonForSpan(span: TelemetrySpan, output: Record<string, unknown>, phase: LprWaterfallPhase) {
  if (span.error) return span.error;
  if (phase === "webhook") {
    return `Received plate ${firstText(output.registration_number, "read")} from ${firstText(span.attributes.source, span.category, "LPR")}.`;
  }
  if (phase === "debounce") {
    const count = firstText(span.attributes.candidate_count, output.candidate_count);
    const selected = firstText(span.attributes.selected_registration_number, output.selected_registration_number);
    return selected ? `${count || "Candidate"} read window selected ${selected}.` : "Grouped plate reads and selected the strongest candidate.";
  }
  if (phase === "identity") {
    if (output.matched === true) return `Matched ${firstText(output.vehicle, output.owner, "known identity")}.`;
    if (output.matched === false) return "No active vehicle or visitor pass matched this plate.";
  }
  if (phase === "schedule") {
    return firstText(output.reason, output.schedule_name, output.source, "Evaluated schedule and access rules.");
  }
  if (phase === "direction") {
    return `Classified movement as ${firstText(output.direction, "unknown")}.`;
  }
  if (phase === "presence") {
    return `Presence timing classified as ${firstText(output.timing_classification, "unknown")}.`;
  }
  if (phase === "persistence") {
    const followUp = output.gate_command_required === true
      ? " It also prepared the follow-up gate state."
      : "";
    return `Saved the access decision, presence update, movement state, and anomaly checks.${followUp}`;
  }
  if (phase === "snapshot") {
    if (output.captured === false) return firstText(output.reason, "Snapshot capture was skipped.");
    if (output.captured === true) return "Captured or stored access-event media.";
  }
  if (phase === "gate" || phase === "garage") {
    if (output.accepted === true) return "Hardware command was accepted by the provider.";
    if (output.accepted === false) return firstText(output.detail, "Hardware command was rejected or failed.");
  }
  return firstText(output.reason, output.detail, output.status, span.name);
}

function detailForSpan(span: TelemetrySpan, output: Record<string, unknown>) {
  return firstText(
    output.owner,
    output.vehicle,
    output.schedule_name,
    output.state,
    output.source,
    output.snapshot_path,
    output.rule_name,
    output.channel,
    span.category
  );
}

function statusFromSpan(span: TelemetrySpan, output: Record<string, unknown>): LprWaterfallStepStatus {
  if (span.status === "error" || span.error) return "error";
  if (output.accepted === false || output.delivered === false) return "error";
  return normalizeStatus(span.status);
}

function normalizeStatus(status: string): LprWaterfallStepStatus {
  const normalized = status.toLowerCase();
  if (normalized === "error" || normalized === "failed" || normalized === "failure") return "error";
  if (normalized === "warning" || normalized === "warn") return "warning";
  if (normalized === "pending" || normalized === "active") return "pending";
  if (normalized === "skipped" || normalized === "skip") return "skipped";
  return "ok";
}

function toneForStatus(status: LprWaterfallStepStatus) {
  if (status === "error") return "red";
  if (status === "warning" || status === "pending") return "amber";
  if (status === "skipped") return "gray";
  return "green";
}

function friendlySpanLabel(name: string, phase: LprWaterfallPhase) {
  const trimmed = name.trim();
  if (!trimmed) return lprWaterfallPhaseLabels[phase];
  const withoutPrefix = trimmed.replace(/^Home Assistant /, "");
  return friendlyStepLabels[withoutPrefix] || withoutPrefix
    .replace(/^Debounce & Confidence Aggregation$/, "Choose best plate read");
}

function humanDuration(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "-";
  if (value >= 1000) return `${formatCompactNumber(value / 1000)}s`;
  return `${Math.round(value)}ms`;
}

function formatCompactNumber(value: number) {
  return value >= 10
    ? value.toFixed(1).replace(/\.0$/, "")
    : value.toFixed(2).replace(/0$/, "").replace(/\.0$/, "");
}

function phaseForText(value: string): LprWaterfallPhase {
  const text = value.toLowerCase();
  if (text.includes("webhook")) return "webhook";
  if (text.includes("debounce") || text.includes("confidence aggregation")) return "debounce";
  if (text.includes("vehicle db") || text.includes("visitor pass") || text.includes("identity")) return "identity";
  if (text.includes("schedule") || text.includes("access rule")) return "schedule";
  if (text.includes("direction")) return "direction";
  if (text.includes("persist") || text.includes("save access") || text.includes("access decision") || text.includes("anomal")) return "persistence";
  if (text.includes("presence")) return "presence";
  if (text.includes("snapshot") || text.includes("thumbnail") || text.includes("artifact")) return "snapshot";
  if (text.includes("garage")) return "garage";
  if (text.includes("gate") || text.includes("cover")) return "gate";
  if (text.includes("notification")) return "notification";
  if (text.includes("complete") || text.includes("finish") || text.includes("final")) return "complete";
  if (text.includes("protect") || text.includes("camera") || text.includes("dvla") || text.includes("visual") || text.includes("llm")) return "integration";
  return "diagnostic";
}

function waterfallReason(
  detail: TelemetryTraceDetail | null | undefined,
  steps: LprWaterfallStep[],
  warnings: SlowStepWarning[],
  summary: string
) {
  if (detail?.error) return detail.error;
  if (warnings.length) return warnings[0].reason;
  if (summary) return summary;
  const failed = steps.find((step) => step.status === "error");
  if (failed) return failed.reason || `${failed.label} failed.`;
  if (!steps.length) return "No LPR telemetry steps were available.";
  return "LPR telemetry completed without recorded slow-step warnings.";
}

function firstObservationPlate(observations: LprTimingObservation[]) {
  return observations.map((row) => stringOrNull(row.registration_number) || stringOrNull(row.raw_value)).find(Boolean) || null;
}

function firstStepStatus(steps: LprWaterfallStep[]) {
  if (steps.some((step) => step.status === "error")) return "error";
  if (steps.some((step) => step.status === "warning")) return "warning";
  if (steps.some((step) => step.status === "pending")) return "pending";
  return steps.length ? "ok" : "unknown";
}

function firstStepTime(steps: LprWaterfallStep[]) {
  return steps.map((step) => step.startedAt || step.endedAt).find(Boolean) || null;
}

function lastStepTime(steps: LprWaterfallStep[]) {
  return [...steps].reverse().map((step) => step.endedAt || step.startedAt).find(Boolean) || null;
}

function parseTime(value: string | null | undefined) {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function durationBetween(start: string | null | undefined, end: string | null | undefined) {
  const parsedStart = parseTime(start);
  const parsedEnd = parseTime(end);
  return parsedStart !== null && parsedEnd !== null ? Math.max(0, parsedEnd - parsedStart) : null;
}

function numberOrNull(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    const parsed = numberOrNull(value);
    if (parsed !== null) return parsed;
  }
  return null;
}

function stringOrNull(value: unknown) {
  return stringPayload(value) || null;
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (typeof value === "boolean") return value ? "true" : "false";
  }
  return "";
}
