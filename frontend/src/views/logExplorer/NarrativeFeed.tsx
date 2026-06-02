import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import {
  Archive,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  Terminal
} from "lucide-react";
import React from "react";

import { formatFileSize, isRecord, levelTone, numberPayload, titleCase } from "../../lib/format";
import { Badge, EmptyState } from "../../ui/primitives";
import type { LogRecord, NarrativeLogItem, TraceDetailState } from "./types";
import { deriveNarrativeLogItem } from "./narrative";
import { LprWaterfall } from "./LprWaterfallPanel";
import {
  artifactFromSpan,
  copyText,
  formatDuration,
  formattedTimestamp,
  levelLabel,
  statusLabel,
  stringifyJson,
  traceIcon
} from "./utils";

type InlineDetailTab = "story" | "timing" | "payload" | "diff" | "artifacts";

export function NarrativeFeed({
  records,
  selectedId,
  loading,
  loadingMore,
  nextCursor,
  onLoadMore,
  onSelect,
  traceDetail
}: {
  records: LogRecord[];
  selectedId: string | null;
  loading: boolean;
  loadingMore: boolean;
  nextCursor: boolean;
  onLoadMore: () => void;
  onSelect: (record: LogRecord) => void;
  traceDetail: TraceDetailState;
}) {
  const parentRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: records.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 78,
    overscan: 8
  });

  if (loading) {
    return <div className="logs-table-card logs-ledger-card"><div className="loading-panel">Loading logs</div></div>;
  }

  return (
    <div className="logs-table-card logs-ledger-card">
      {records.length ? (
        <>
        <div className="logs-ledger-head" aria-hidden="true">
          <span>Time</span>
          <span>What Happened</span>
          <span>Why It Happened</span>
          <span>Outcome</span>
        </div>
        <div className="logs-virtual-body logs-ledger-body" ref={parentRef}>
          <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}>
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const record = records[virtualRow.index];
              const selected = selectedId === record.id;
              return (
                <NarrativeRow
                  key={record.id}
                  onSelect={onSelect}
                  record={record}
                  ref={rowVirtualizer.measureElement}
                  selected={selected}
                  traceDetail={selected ? traceDetail : null}
                  virtualIndex={virtualRow.index}
                  virtualStart={virtualRow.start}
                />
              );
            })}
          </div>
        </div>
        </>
      ) : (
        <div className="logs-table-empty">
          <EmptyState icon={Terminal} label="No log records match these filters." />
        </div>
      )}
      <div className="logs-table-footer">
        <span>Showing {records.length.toLocaleString()} records in narrative order</span>
        <button className="secondary-button" disabled={!nextCursor || loadingMore} onClick={onLoadMore} type="button">
          {loadingMore ? <Loader2 className="spin" size={15} /> : <ChevronDown size={15} />}
          {loadingMore ? "Loading..." : nextCursor ? "Load More" : "All Loaded"}
        </button>
      </div>
    </div>
  );
}

const NarrativeRow = React.forwardRef<HTMLDivElement, {
  record: LogRecord;
  selected: boolean;
  traceDetail: TraceDetailState | null;
  virtualIndex: number;
  virtualStart: number;
  onSelect: (record: LogRecord) => void;
}>(({ record, selected, traceDetail, virtualIndex, virtualStart, onSelect }, ref) => {
  const item = deriveNarrativeLogItem(record);
  const stamp = formattedTimestamp(record.timestamp);
  const Icon = traceIcon(record);
  const traceLabel = record.traceId || record.requestId || "";
  const statusText = record.kind === "audit" ? titleCase(record.outcome) : statusLabel(record.status);

  return (
    <div
      aria-pressed={selected}
      className={selected ? `logs-ledger-row active ${record.tone}` : `logs-ledger-row ${record.tone}`}
      data-index={virtualIndex}
      onClick={() => onSelect(record)}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        onSelect(record);
      }}
      ref={ref}
      role="button"
      style={{
        transform: `translateY(${virtualStart}px)`
      }}
      tabIndex={0}
    >
      <div className="logs-ledger-row-main">
        <time className="logs-ledger-time" dateTime={record.timestamp}>
          <strong>{stamp.time}</strong>
          <span>{stamp.date}</span>
        </time>
        <div className="logs-ledger-what">
          <span className={`logs-source-icon ${record.tone}`}>
            <Icon size={15} />
          </span>
          <div>
            <strong>{item.what}</strong>
            <span className="logs-ledger-evidence">
              <Badge tone={record.tone}>{item.sourceLabel}</Badge>
              <Badge tone={levelTone(record.level)}>{levelLabel(record.level)}</Badge>
              {isLprTrace(record) ? <Badge tone="blue">{lprLatencyLabel(record) || "LPR Waterfall"}</Badge> : null}
              {item.subject && item.subject !== "Realtime" ? <span>{item.subject}</span> : null}
            </span>
          </div>
        </div>
        <div className="logs-ledger-why">
          <p>{item.why}</p>
          {item.details ? <span>{item.details}</span> : null}
        </div>
        <div className="logs-ledger-outcome">
          <span className="logs-status-cell">
            <Badge tone={record.tone}>{statusText}</Badge>
            <Badge tone="gray">{formatDuration(record.durationMs)}</Badge>
          </span>
          <span className="logs-ledger-actions">
            <button
              disabled={!traceLabel}
              onClick={(event) => {
                event.stopPropagation();
                copyText(traceLabel).catch(() => undefined);
              }}
              type="button"
              title="Copy trace or request ID"
            >
              <Copy size={13} />
            </button>
            <ChevronRight className={selected ? "open" : undefined} size={15} />
          </span>
        </div>
      </div>
      {selected ? (
        <ExpandedLogEvidence
          item={item}
          record={record}
          statusText={statusText}
          traceDetail={traceDetail}
          traceLabel={traceLabel}
        />
      ) : null}
    </div>
  );
});

NarrativeRow.displayName = "NarrativeRow";

function ExpandedLogEvidence({
  item,
  record,
  statusText,
  traceDetail,
  traceLabel
}: {
  item: NarrativeLogItem;
  record: LogRecord;
  statusText: string;
  traceDetail: TraceDetailState | null;
  traceLabel: string;
}) {
  const [tab, setTab] = React.useState<InlineDetailTab>(() => defaultInlineDetailTab(record));
  const stamp = formattedTimestamp(record.timestamp);
  const lprTrace = isLprTrace(record);
  const showWaterfall = record.kind === "trace" && (lprTrace || record.rawTrace?.category === "gate_malfunction");
  const oldValue = record.rawAudit && isRecord(record.rawAudit.diff.old) ? record.rawAudit.diff.old : {};
  const newValue = record.rawAudit && isRecord(record.rawAudit.diff.new) ? record.rawAudit.diff.new : {};
  const delta = record.rawAudit ? jsonDiff(oldValue, newValue) || {} : {};
  const spans = traceDetail?.detail?.spans || [];
  const artifacts = spans.map(artifactFromSpan).filter((artifact): artifact is Record<string, unknown> => Boolean(artifact));

  React.useEffect(() => setTab(defaultInlineDetailTab(record)), [record.id]);

  return (
    <div className="logs-ledger-expanded" onClick={(event) => event.stopPropagation()}>
      <div className="logs-reason-panel">
        <span>{record.status === "skipped" ? "Why Skipped" : "Why This Happened"}</span>
        <strong>{item.why}</strong>
        {item.details ? <p>{item.details}</p> : null}
      </div>
      <div className="logs-expanded-summary">
        <EvidenceFact label="When" value={`${stamp.date} ${stamp.time}`} />
        <EvidenceFact label="Outcome" value={`${statusText} / ${formatDuration(record.durationMs)}`} />
        <EvidenceFact label="Actor" value={item.actor || "System"} />
        <EvidenceFact label="Trace" value={traceLabel || "n/a"} mono />
      </div>
      <div className="logs-inline-tabs" role="tablist" aria-label="Log detail views">
        {lprTrace ? (
          <InlineDetailButton active={tab === "timing"} label="LPR Waterfall" onClick={() => setTab("timing")} />
        ) : null}
        <InlineDetailButton active={tab === "story"} label="Story" onClick={() => setTab("story")} />
        {record.kind === "trace" && !lprTrace ? (
          <InlineDetailButton active={tab === "timing"} label={showWaterfall ? "Timeline" : "Spans"} onClick={() => setTab("timing")} />
        ) : null}
        <InlineDetailButton active={tab === "payload"} label="Payload" onClick={() => setTab("payload")} />
        <InlineDetailButton active={tab === "diff"} label="Diff" onClick={() => setTab("diff")} />
        <InlineDetailButton active={tab === "artifacts"} label={`Artifacts (${artifacts.length})`} onClick={() => setTab("artifacts")} />
      </div>
      <div className="logs-inline-detail-body">
        {tab === "story" ? <InlineStory item={item} record={record} traceDetail={traceDetail} /> : null}
        {tab === "timing" && traceDetail ? (
          <div className="logs-inline-waterfall">
            <div className="logs-inline-waterfall-head">
              <strong>{lprTrace ? "LPR Timing Waterfall" : "Operational Timeline"}</strong>
              <span>{lprTrace ? "Camera, webhook receipt, access decision, and gate timing in sequence." : "Trace spans in execution order."}</span>
            </div>
            <LprWaterfall record={record} traceDetail={traceDetail} />
          </div>
        ) : null}
        {tab === "payload" ? <JsonBlock label="Payload" value={payloadContext(record, traceDetail)} /> : null}
        {tab === "diff" ? <JsonBlock label="Changed Fields" value={record.rawAudit ? delta : spanDiffContext(record, traceDetail)} /> : null}
        {tab === "artifacts" ? <InlineArtifacts artifacts={artifacts} /> : null}
      </div>
    </div>
  );
}

function InlineDetailButton({
  active,
  label,
  onClick
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button aria-selected={active} className={active ? "active" : ""} onClick={onClick} role="tab" type="button">
      {label}
    </button>
  );
}

function InlineStory({
  item,
  record,
  traceDetail
}: {
  item: NarrativeLogItem;
  record: LogRecord;
  traceDetail: TraceDetailState | null;
}) {
  return (
    <div className="logs-expanded-story">
      <span>{item.sourceLabel}</span>
      <strong>{item.title}</strong>
      <p>{item.reason}</p>
      {traceDetail?.loading ? <p>Loading supporting timing...</p> : null}
      {traceDetail?.error ? <p>{traceDetail.error}</p> : null}
      {traceDetail?.detail?.spans.slice(0, 3).map((span) => (
        <p key={span.span_id}>{span.name}: {formatDuration(span.duration_ms)} {titleCase(span.status)}.</p>
      ))}
      {!traceDetail?.detail?.spans.length && record.rawRealtime ? <p>{record.summary}</p> : null}
    </div>
  );
}

function EvidenceFact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="logs-evidence-fact">
      <span>{label}</span>
      <strong className={mono ? "mono" : undefined}>{value || "n/a"}</strong>
    </div>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="json-block">
      <strong>{label}</strong>
      <pre>{stringifyJson(value)}</pre>
    </div>
  );
}

function InlineArtifacts({ artifacts }: { artifacts: Array<Record<string, unknown>> }) {
  if (!artifacts.length) return <EmptyState icon={Archive} label="No artifacts were attached to this record." />;
  return (
    <div className="logs-artifact-grid">
      {artifacts.map((artifact, index) => (
        <a href={String(artifact.url)} key={`${artifact.url}-${index}`} rel="noreferrer" target="_blank">
          <img alt="Telemetry artifact" src={String(artifact.url)} />
          <span>
            <strong>{String(artifact.content_type || "Artifact")}</strong>
            <small>{formatFileSize(numberPayload(artifact.size_bytes))}</small>
          </span>
        </a>
      ))}
    </div>
  );
}

function payloadContext(record: LogRecord, traceDetail: TraceDetailState | null): Record<string, unknown> {
  if (record.rawTrace) return {
    trace: traceDetail?.detail || record.rawTrace,
    trace_id: record.traceId,
    request_id: record.requestId
  };
  if (record.rawAudit) return {
    action: record.rawAudit.action,
    category: record.rawAudit.category,
    metadata: record.rawAudit.metadata,
    diff: record.rawAudit.diff,
    request_id: record.rawAudit.request_id,
    trace_id: record.rawAudit.trace_id
  };
  return {
    type: record.rawRealtime?.type || record.actionDetail,
    payload: record.rawRealtime?.payload || {},
    request_id: record.requestId,
    trace_id: record.traceId
  };
}

function spanDiffContext(record: LogRecord, traceDetail: TraceDetailState | null): unknown {
  const spans = traceDetail?.detail?.spans || [];
  if (!spans.length) return record.rawTrace?.context || {};
  const original = spans[0]?.input_payload || {};
  const modified = spans[spans.length - 1]?.output_payload || {};
  return jsonDiff(original, modified) || { original, modified };
}

function isLprTrace(record: LogRecord) {
  return record.source === "lpr" || record.rawTrace?.category === "lpr_telemetry";
}

function defaultInlineDetailTab(record: LogRecord): InlineDetailTab {
  return isLprTrace(record) ? "timing" : "story";
}

function lprLatencyLabel(record: LogRecord) {
  const context = record.rawTrace?.context || {};
  const webhookTrace = isRecord(context.webhook_trace) ? context.webhook_trace : {};
  const latency = firstPositiveNumber(
    context.captured_to_webhook_ms,
    context.webhook_latency_ms,
    webhookTrace.captured_to_webhook_ms,
    webhookTrace.webhook_latency_ms
  );
  return latency === null ? "" : `Webhook ${formatDuration(latency)}`;
}

function firstPositiveNumber(...values: unknown[]) {
  for (const value of values) {
    const parsed = numberPayload(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return null;
}
