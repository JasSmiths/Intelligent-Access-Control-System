import { AlertTriangle, Clock3, Terminal } from "lucide-react";
import React from "react";

import {
  api,
  Badge,
  EmptyState,
  isAbortError,
  numberPayload,
  stringPayload,
  titleCase
} from "../../shared";
import type {
  LprWaterfallResponse,
  LogRecord,
  TelemetrySpan,
  TelemetryTraceDetail,
  TraceDetailState
} from "./types";
import {
  formatDuration,
  formatSecondsDuration,
  formattedTimestamp,
  gateMalfunctionCountdownLabel,
  gateTimelineSummary
} from "./utils";
import { deriveLprWaterfallModel } from "./lprWaterfall";

export function LprWaterfall({
  record,
  traceDetail
}: {
  record: LogRecord;
  traceDetail: TraceDetailState;
}) {
  if (record.kind !== "trace") {
    return <EmptyState icon={Terminal} label="Waterfall is available for telemetry trace records." />;
  }
  if (traceDetail.loading) return <div className="telemetry-detail-loading">Loading trace spans...</div>;
  if (traceDetail.error) return <div className="error-banner">{traceDetail.error}</div>;
  if (!traceDetail.detail) return <EmptyState icon={Clock3} label="No trace details loaded." />;
  if (traceDetail.detail.category === "gate_malfunction") {
    return <GateWaterfall trace={traceDetail.detail} />;
  }
  return <TraceWaterfall record={record} trace={traceDetail.detail} />;
}

function TraceWaterfall({ record, trace }: { record: LogRecord; trace: TelemetryTraceDetail }) {
  const [waterfallResponse, setWaterfallResponse] = React.useState<LprWaterfallResponse | null>(null);
  const [waterfallLoading, setWaterfallLoading] = React.useState(false);
  const [waterfallError, setWaterfallError] = React.useState("");
  const canLoadWaterfall = trace.category === "lpr_telemetry" && Boolean(record.traceId || trace.access_event_id);

  React.useEffect(() => {
    if (!canLoadWaterfall) {
      setWaterfallResponse(null);
      setWaterfallError("");
      setWaterfallLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    const traceOrEventId = record.traceId || trace.access_event_id || "";
    setWaterfallLoading(true);
    setWaterfallError("");
    api.get<LprWaterfallResponse>(`/api/v1/telemetry/lpr-waterfall/${encodeURIComponent(traceOrEventId)}`, {
      signal: controller.signal
    })
      .then((response) => setWaterfallResponse(response))
      .catch((error) => {
        if (isAbortError(error)) return;
        setWaterfallError(error instanceof Error ? error.message : "Unable to load LPR waterfall.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setWaterfallLoading(false);
      });
    return () => controller.abort();
  }, [canLoadWaterfall, record.traceId, trace.access_event_id]);

  const model = deriveLprWaterfallModel(trace, waterfallResponse);
  if (!model) return <RawTraceWaterfall record={record} trace={trace} />;

  return (
    <div className="logs-timeline">
      {waterfallLoading ? <div className="telemetry-detail-loading">Loading durable LPR timing...</div> : null}
      {waterfallError ? <div className="error-banner">{waterfallError}</div> : null}
      {model.warnings.length ? (
        <div className="logs-warning-strip">
          <AlertTriangle size={15} />
          <span>{model.warnings[0].reason}</span>
        </div>
      ) : null}
      <div className="logs-timeline-summary">
        <MiniFact label="Arrived" value={identityLabel(record, trace)} />
        <MiniFact label="Plate" value={model.registrationNumber || record.subject || "n/a"} mono />
        <MiniFact label="Decision" value={titleCase(model.decision || trace.status)} />
        <MiniFact label="Total" value={formatDuration(model.totalDurationMs)} mono />
      </div>
      {model.steps.map((step, index) => {
        const totalMs = Math.max(model.totalDurationMs, 1);
        const offset = Math.max(0, (step.offsetMs / totalMs) * 100);
        const width = Math.max(1.5, ((step.durationMs || 0) / totalMs) * 100);
        return (
          <div className={step.status === "error" ? "logs-span-row error" : "logs-span-row"} key={step.id}>
            <span className="logs-span-index">{index + 1}</span>
            <div>
              <div className="logs-span-head">
                <strong>{step.label}</strong>
                <span className="logs-span-badges">
                  <Badge tone={step.tone}>{titleCase(step.status)}</Badge>
                  <code>{formatDuration(step.durationMs)}</code>
                </span>
              </div>
              <div className="logs-waterfall-track" aria-label={`${step.label} timing`}>
                <span style={{ left: `${offset}%`, width: `${Math.min(width, 100 - offset)}%` }} />
              </div>
              {step.warning ? <p className="logs-span-error">{step.warning.reason}</p> : null}
              <p>{step.reason || step.detail || "Step completed."}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RawTraceWaterfall({ record, trace }: { record: LogRecord; trace: TelemetryTraceDetail }) {
  const sortedSpans = sortSpans(trace.spans);
  const traceStart = Date.parse(trace.started_at);
  const totalMs = Math.max(trace.duration_ms || 0, ...sortedSpans.map((span) => spanEndOffset(span, traceStart)), 1);
  const decision = stringPayload(trace.context.decision || trace.status) || trace.status;
  const direction = stringPayload(trace.context.direction) || "unknown";
  const confidence = typeof trace.context.confidence === "number"
    ? `${Math.round(numberPayload(trace.context.confidence) * (numberPayload(trace.context.confidence) <= 1 ? 100 : 1))}%`
    : "n/a";

  return (
    <div className="logs-timeline">
      <div className="logs-timeline-summary">
        <MiniFact label="Plate" value={trace.registration_number || record.subject || "n/a"} mono />
        <MiniFact label="Decision" value={titleCase(decision)} />
        <MiniFact label="Direction" value={titleCase(direction)} />
        <MiniFact label="Confidence" value={confidence} mono />
      </div>
      {sortedSpans.map((span, index) => {
        const started = Date.parse(span.started_at);
        const offset = Math.max(0, ((started - traceStart) / totalMs) * 100);
        const width = Math.max(1.5, ((span.duration_ms || 0) / totalMs) * 100);
        return (
          <div className={span.status === "error" ? "logs-span-row error" : "logs-span-row"} key={span.span_id}>
            <span className="logs-span-index">{index + 1}</span>
            <div>
              <div className="logs-span-head">
                <strong>{span.name}</strong>
                <code>{formatDuration(span.duration_ms)}</code>
              </div>
              <div className="logs-waterfall-track" aria-label={`${span.name} timing`}>
                <span style={{ left: `${offset}%`, width: `${Math.min(width, 100 - offset)}%` }} />
              </div>
              {span.error ? <p className="logs-span-error">{span.error}</p> : null}
              {spanSummary(span) ? <p>{spanSummary(span)}</p> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function identityLabel(record: LogRecord, trace: TelemetryTraceDetail) {
  return stringPayload(
    trace.context.person_name ||
    trace.context.visitor_name ||
    trace.context.owner ||
    trace.context.display_vehicle ||
    record.subjectDetail ||
    record.subject
  ) || "Unknown identity";
}

function GateWaterfall({ trace }: { trace: TelemetryTraceDetail }) {
  const context = trace.context;
  const status = stringPayload(context.status || trace.status);
  const nextAttempt = stringPayload(context.next_attempt_scheduled_at);
  const downtimeSeconds = numberPayload(context.total_downtime_seconds);
  const attempts = String(numberPayload(context.fix_attempts_count));
  const gateName = stringPayload(context.gate_name || trace.source || "Primary gate");
  const sortedSpans = sortSpans(trace.spans);

  return (
    <div className="logs-timeline">
      <div className="logs-timeline-summary">
        <MiniFact label="Gate" value={gateName} />
        <MiniFact label="Status" value={titleCase(status || "unknown")} />
        <MiniFact label="Attempts" value={attempts} mono />
        <MiniFact label={status === "active" ? "Open Duration" : "Total Downtime"} value={formatSecondsDuration(downtimeSeconds)} mono />
      </div>
      {status === "active" && nextAttempt ? (
        <div className="logs-countdown">
          <Clock3 size={15} />
          <span>{gateMalfunctionCountdownLabel(nextAttempt, Date.now())}</span>
        </div>
      ) : null}
      {sortedSpans.map((span, index) => {
        const kind = stringPayload(span.attributes.kind || "");
        return (
          <div className={span.status === "error" ? "logs-span-row error" : "logs-span-row"} key={span.span_id}>
            <span className="logs-span-index">{index + 1}</span>
            <div>
              <div className="logs-span-head">
                <strong>{span.name}</strong>
                <time>{formattedTimestamp(span.started_at).time}</time>
              </div>
              <p>{gateTimelineSummary(kind, span.output_payload || {})}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MiniFact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="logs-fact">
      <span>{label}</span>
      <strong className={mono ? "mono" : undefined}>{value}</strong>
    </div>
  );
}

function sortSpans(spans: TelemetrySpan[]) {
  return [...spans].sort((left, right) => {
    const leftOrder = Number.isFinite(left.step_order) ? left.step_order : 0;
    const rightOrder = Number.isFinite(right.step_order) ? right.step_order : 0;
    return leftOrder - rightOrder || Date.parse(left.started_at) - Date.parse(right.started_at);
  });
}

function spanEndOffset(span: TelemetrySpan, traceStart: number) {
  const end = span.ended_at ? Date.parse(span.ended_at) : Date.parse(span.started_at);
  return Math.max(0, end - traceStart);
}

function spanSummary(span: TelemetrySpan) {
  return stringPayload(span.output_payload.summary)
    || stringPayload(span.output_payload.detail)
    || stringPayload(span.attributes.decision)
    || "";
}
