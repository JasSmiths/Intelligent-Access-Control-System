import React from "react";
import { createPortal } from "react-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  SortingState,
  useReactTable
} from "@tanstack/react-table";
import { diff as jsonDiff } from "jsondiffpatch";
import {
  Activity,
  AlertTriangle,
  Archive,
  BarChart3,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Copy,
  Database,
  Download,
  ExternalLink,
  FileJson,
  FileText,
  Filter,
  Gauge,
  HardDrive,
  Layers3,
  Loader2,
  Pause,
  Play,
  RefreshCcw,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  Signal,
  SlidersHorizontal,
  Terminal,
  Trash2,
  X
} from "lucide-react";

import {
  api,
  Badge,
  EmptyState,
  formatFileSize,
  isRecord,
  levelTone,
  numberPayload,
  RealtimeMessage,
  stringPayload,
  titleCase
} from "../../shared";
import {
  defaultLogsFilters,
  levelOptions,
  sourceTabs,
  statusOptions,
  timeRangeOptions
} from "./constants";
import {
  LogRecord,
  LogsFilters,
  LogSourceKey,
  SavedLogsFilter,
  TelemetrySpan,
  TelemetryStorageSummary,
  TelemetrySummary,
  TelemetryTraceDetail,
  TraceDetailState
} from "./types";
import {
  artifactFromSpan,
  copyText,
  exportRecords,
  formatDuration,
  formattedTimestamp,
  formatLogMegabytes,
  formatSecondsDuration,
  gateMalfunctionCountdownLabel,
  gateTimelineSummary,
  levelLabel,
  metricFromSummary,
  outcomeTone,
  sourceLabel,
  statusLabel,
  stringifyJson,
  summaryMetricCards,
  traceIcon
} from "./utils";

const MonacoDiffEditor = React.lazy(() => import("@monaco-editor/react").then((module) => ({ default: module.DiffEditor })));

const gridColumns = "42px minmax(132px, .75fr) minmax(150px, .9fr) minmax(230px, 1.45fr) minmax(190px, 1fr) minmax(128px, .72fr) minmax(130px, .72fr) minmax(180px, .9fr)";

export function LogsTopBar({
  storage,
  clearing,
  onClear,
  onExportCsv,
  onExportJson,
  onRefresh
}: {
  storage: TelemetryStorageSummary | null;
  clearing: boolean;
  onClear: () => void;
  onExportCsv: () => void;
  onExportJson: () => void;
  onRefresh: () => void;
}) {
  return (
    <div className="logs-command-bar">
      <div className="logs-command-title">
        <Terminal size={20} />
        <div>
          <h1>Logs</h1>
          <span>Telemetry, audit history, and live operational events.</span>
        </div>
      </div>
      <div className="logs-command-actions">
        <div className="logs-storage-meter" aria-label="Telemetry storage usage">
          <span>Storage</span>
          <strong>{storage ? formatLogMegabytes(storage.total_size_bytes) : "..."}</strong>
          <i><b style={{ width: storage ? `${Math.min(100, Math.max(8, storage.total_size_bytes / Math.max(storage.total_size_bytes, 1) * 48))}%` : "12%" }} /></i>
        </div>
        <button className="secondary-button" onClick={onRefresh} type="button">
          <RefreshCcw size={15} /> Refresh
        </button>
        <div className="logs-export-group">
          <button className="secondary-button" onClick={onExportCsv} type="button">
            <Download size={15} /> CSV
          </button>
          <button className="secondary-button" onClick={onExportJson} type="button">
            <FileJson size={15} /> JSON
          </button>
        </div>
        <button className="danger-button" disabled={clearing} onClick={onClear} type="button">
          {clearing ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
          {clearing ? "Clearing..." : "Clear Logs"}
        </button>
      </div>
    </div>
  );
}

export function LogsMetricStrip({
  summary,
  storage,
  liveCount
}: {
  summary: TelemetrySummary | null;
  storage: TelemetryStorageSummary | null;
  liveCount: number;
}) {
  const metrics = summaryMetricCards(summary, storage, liveCount);
  return (
    <div className="logs-metric-strip">
      <MetricTile icon={Signal} label="Live Events" tone="green" value={String(metrics.live)} detail="Realtime buffer" />
      <MetricTile icon={AlertTriangle} label="Errors" tone="red" value={String(metrics.errors)} detail="Trace and audit failures" />
      <MetricTile icon={Gauge} label="Warnings" tone="amber" value={String(metrics.warnings)} detail="Warning-level records" />
      <MetricTile icon={HardDrive} label="Trace Storage" tone="blue" value={metrics.storage} detail={`${storage?.file_count || summary?.storage.file_count || 0} files/artifacts`} />
      <MetricTile icon={ShieldCheck} label="Audit Preserved" tone="purple" value={String(metrics.audit)} detail="Durable audit rows" />
    </div>
  );
}

function MetricTile({
  icon: Icon,
  label,
  value,
  detail,
  tone
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  detail: string;
  tone: "green" | "red" | "amber" | "blue" | "purple";
}) {
  return (
    <div className="logs-metric">
      <div className={`logs-metric-icon ${tone}`}>
        <Icon size={18} />
      </div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

export function LogsSourceTabs({
  activeSource,
  liveCount,
  liveSourceCounts,
  summary,
  onChange
}: {
  activeSource: LogSourceKey;
  liveCount: number;
  liveSourceCounts: Partial<Record<LogSourceKey, number>>;
  summary: TelemetrySummary | null;
  onChange: (source: LogSourceKey) => void;
}) {
  return (
    <div className="logs-source-tabs" role="tablist" aria-label="Log sources">
      {sourceTabs.map((tab) => {
        const Icon = tab.icon;
        const persistedCount = tab.key === "live" ? 0 : metricFromSummary(summary, tab.key);
        const liveSourceCount = tab.key === "live" ? liveCount : liveSourceCounts[tab.key] || 0;
        const count = persistedCount + liveSourceCount;
        return (
          <button
            aria-selected={activeSource === tab.key}
            className={activeSource === tab.key ? "logs-source-tab active" : "logs-source-tab"}
            key={tab.key}
            onClick={() => onChange(tab.key)}
            role="tab"
            type="button"
            title={tab.description}
          >
            <Icon size={15} />
            <span>{tab.shortLabel}</span>
            <small>{count.toLocaleString()}</small>
          </button>
        );
      })}
    </div>
  );
}

export function LogsFilterBar({
  filters,
  savedFilters,
  activeSource,
  onApplySaved,
  onChange,
  onReset,
  onSave
}: {
  filters: LogsFilters;
  savedFilters: SavedLogsFilter[];
  activeSource: LogSourceKey;
  onApplySaved: (filter: SavedLogsFilter) => void;
  onChange: (filters: LogsFilters) => void;
  onReset: () => void;
  onSave: () => void;
}) {
  const update = (patch: Partial<LogsFilters>) => onChange({ ...filters, ...patch });
  return (
    <div className="logs-filter-bar">
      <label className="search logs-search">
        <Search size={16} />
        <input
          value={filters.query}
          onChange={(event) => update({ query: event.target.value })}
          placeholder="Search events, plates, users, payloads..."
        />
      </label>
      <SelectFilter icon={CalendarDays} label="Time range" value={filters.timeRange} onChange={(value) => update({ timeRange: value })} options={timeRangeOptions} />
      <SelectFilter icon={SlidersHorizontal} label="Level" value={filters.level} onChange={(value) => update({ level: value })} options={levelOptions} />
      <SelectFilter icon={Filter} label="Status" value={filters.status} onChange={(value) => update({ status: value })} options={statusOptions} />
      <label className="logs-inline-filter">
        <span>Actor / Source</span>
        <input value={filters.actor} onChange={(event) => update({ actor: event.target.value })} placeholder="System, Alfred, admin..." />
      </label>
      <label className="logs-inline-filter">
        <span>Plate / Target</span>
        <input value={filters.subject} onChange={(event) => update({ subject: event.target.value })} placeholder="ABC-1234, gate, user..." />
      </label>
      <select
        aria-label="Saved filters"
        className="logs-saved-filter"
        onChange={(event) => {
          const selected = savedFilters.find((filter) => filter.id === event.target.value);
          if (selected) onApplySaved(selected);
          event.currentTarget.value = "";
        }}
        value=""
      >
        <option value="">Saved Filters</option>
        {savedFilters.map((filter) => (
          <option key={filter.id} value={filter.id}>{filter.name}</option>
        ))}
      </select>
      <button className="secondary-button" onClick={onSave} type="button">
        <Save size={15} /> Save
      </button>
      <button className="secondary-button" onClick={onReset} type="button">
        <RotateCcw size={15} /> Reset
      </button>
      <span className="logs-filter-context">{sourceLabel(activeSource)}</span>
    </div>
  );
}

function SelectFilter({
  icon: Icon,
  label,
  value,
  options,
  onChange
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="logs-select-filter">
      <Icon size={14} />
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  );
}

export function LogsExplorerTable({
  records,
  selectedId,
  loading,
  loadingMore,
  nextCursor,
  onLoadMore,
  onSelect
}: {
  records: LogRecord[];
  selectedId: string | null;
  loading: boolean;
  loadingMore: boolean;
  nextCursor: boolean;
  onLoadMore: () => void;
  onSelect: (record: LogRecord) => void;
}) {
  const [sorting, setSorting] = React.useState<SortingState>([{ id: "timestamp", desc: true }]);
  const columns = React.useMemo<ColumnDef<LogRecord>[]>(() => [
    {
      id: "select",
      header: () => <span className="sr-only">Selected</span>,
      cell: ({ row }) => (
        <span className={selectedId === row.original.id ? "logs-row-check active" : "logs-row-check"}>
          {selectedId === row.original.id ? <Check size={13} /> : null}
        </span>
      )
    },
    {
      accessorKey: "timestamp",
      header: ({ column }) => <SortButton column={column} label="Time" />,
      cell: ({ row }) => {
        const stamp = formattedTimestamp(row.original.timestamp);
        return <div className="logs-time-cell"><strong>{stamp.time}</strong><span>{stamp.date}</span></div>;
      }
    },
    {
      accessorKey: "sourceLabel",
      header: ({ column }) => <SortButton column={column} label="Source / Category" />,
      cell: ({ row }) => <SourceCell record={row.original} />
    },
    {
      accessorKey: "action",
      header: ({ column }) => <SortButton column={column} label="Event / Action" />,
      cell: ({ row }) => <TwoLine primary={row.original.action} secondary={row.original.actionDetail} />
    },
    {
      accessorKey: "subject",
      header: ({ column }) => <SortButton column={column} label="Subject / Target" />,
      cell: ({ row }) => <SubjectCell record={row.original} />
    },
    {
      accessorKey: "level",
      header: ({ column }) => <SortButton column={column} label="Status / Level" />,
      cell: ({ row }) => <StatusCell record={row.original} />
    },
    {
      accessorKey: "durationMs",
      header: ({ column }) => <SortButton column={column} label="Duration / Outcome" />,
      cell: ({ row }) => <TwoLine primary={formatDuration(row.original.durationMs)} secondary={titleCase(row.original.outcome || row.original.status)} />
    },
    {
      accessorKey: "traceId",
      header: "Trace / Request ID",
      cell: ({ row }) => <TraceIdCell record={row.original} />
    }
  ], [selectedId]);

  const table = useReactTable({
    data: records,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel()
  });
  const rows = table.getRowModel().rows;
  const parentRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 58,
    overscan: 10
  });

  if (loading) {
    return <div className="logs-table-card"><div className="loading-panel">Loading logs</div></div>;
  }

  return (
    <div className="logs-table-card">
      <div className="logs-table-header" style={{ gridTemplateColumns: gridColumns }}>
        {table.getHeaderGroups()[0]?.headers.map((header) => (
          <div className="logs-th" key={header.id}>
            {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
          </div>
        ))}
      </div>
      {rows.length ? (
        <div className="logs-virtual-body" ref={parentRef}>
          <div style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}>
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const row = rows[virtualRow.index];
              const record = row.original;
              return (
                <button
                  className={selectedId === record.id ? `logs-tr active ${record.tone}` : `logs-tr ${record.tone}`}
                  data-index={virtualRow.index}
                  key={record.id}
                  onClick={() => onSelect(record)}
                  ref={rowVirtualizer.measureElement}
                  style={{ gridTemplateColumns: gridColumns, transform: `translateY(${virtualRow.start}px)` }}
                  type="button"
                >
                  {row.getVisibleCells().map((cell) => (
                    <span className="logs-td" key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </span>
                  ))}
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="logs-table-empty">
          <EmptyState icon={Terminal} label="No log records match these filters." />
        </div>
      )}
      <div className="logs-table-footer">
        <span>Showing {rows.length.toLocaleString()} records</span>
        <button className="secondary-button" disabled={!nextCursor || loadingMore} onClick={onLoadMore} type="button">
          {loadingMore ? <Loader2 className="spin" size={15} /> : <ChevronDown size={15} />}
          {loadingMore ? "Loading..." : nextCursor ? "Load More" : "All Loaded"}
        </button>
      </div>
    </div>
  );
}

function SortButton({ column, label }: { column: any; label: string }) {
  const sorted = column.getIsSorted();
  return (
    <button className="logs-sort-button" onClick={column.getToggleSortingHandler()} type="button">
      {label}
      <ChevronDown className={sorted === "asc" ? "asc" : sorted === "desc" ? "desc" : ""} size={13} />
    </button>
  );
}

function SourceCell({ record }: { record: LogRecord }) {
  const Icon = traceIcon(record);
  return (
    <div className="logs-source-cell">
      <span className={`logs-source-icon ${record.tone}`}>
        <Icon size={15} />
      </span>
      <TwoLine primary={record.sourceLabel} secondary={record.sourceDetail} />
    </div>
  );
}

function SubjectCell({ record }: { record: LogRecord }) {
  return (
    <div className="logs-subject-cell">
      {record.rawTrace?.registration_number ? <code>{record.subject}</code> : <strong>{record.subject}</strong>}
      <span>{record.subjectDetail || " "}</span>
    </div>
  );
}

function StatusCell({ record }: { record: LogRecord }) {
  return (
    <div className="logs-status-cell">
      <Badge tone={record.tone}>{record.kind === "audit" ? titleCase(record.outcome) : statusLabel(record.status)}</Badge>
      <Badge tone={levelTone(record.level)}>{levelLabel(record.level)}</Badge>
    </div>
  );
}

function TraceIdCell({ record }: { record: LogRecord }) {
  return (
    <div className="logs-trace-cell">
      <code>{record.traceId || "n/a"}</code>
      <button disabled={!record.traceId && !record.requestId} onClick={(event) => {
        event.stopPropagation();
        copyText(record.traceId || record.requestId || "").catch(() => undefined);
      }} type="button" title="Copy trace or request ID">
        <Copy size={13} />
      </button>
      <small>{record.requestId || ""}</small>
    </div>
  );
}

function TwoLine({ primary, secondary }: { primary: React.ReactNode; secondary: React.ReactNode }) {
  return (
    <div className="logs-two-line">
      <strong>{primary}</strong>
      <span>{secondary}</span>
    </div>
  );
}

export function LogsDetailInspector({
  record,
  traceDetail,
  onClose
}: {
  record: LogRecord | null;
  traceDetail: TraceDetailState;
  onClose: () => void;
}) {
  const [tab, setTab] = React.useState<"timeline" | "payload" | "diff" | "artifacts">("timeline");
  React.useEffect(() => setTab("timeline"), [record?.id]);

  if (!record) {
    return (
      <aside className="logs-detail-panel">
        <div className="logs-inspector-empty">
          <EmptyState icon={Layers3} label="Select a log record to inspect details." />
          <p>Choose a row to view the timeline, payload, diffs, artifacts, and copy actions.</p>
        </div>
      </aside>
    );
  }

  const audit = record.rawAudit;
  const oldValue = audit && isRecord(audit.diff.old) ? audit.diff.old : {};
  const newValue = audit && isRecord(audit.diff.new) ? audit.diff.new : {};
  const delta = audit ? jsonDiff(oldValue, newValue) || {} : {};
  const spans = traceDetail.detail?.spans || [];
  const artifacts = spans.map(artifactFromSpan).filter((item): item is Record<string, unknown> => Boolean(item));

  return (
    <aside className="logs-detail-panel">
      <div className="logs-detail-head">
        <div>
          <Badge tone={record.tone}>{record.kind === "trace" ? statusLabel(record.status) : titleCase(record.outcome)}</Badge>
          <h2>{record.action}</h2>
          <p>{record.summary}</p>
        </div>
        <button className="logs-icon-button" onClick={onClose} type="button" title="Close inspector">
          <X size={16} />
        </button>
      </div>

      <div className="logs-fact-grid">
        <Fact label="Time" value={formattedTimestamp(record.timestamp).date} />
        <Fact label="Source" value={record.sourceLabel} />
        <Fact label="Subject" value={record.subject} />
        <Fact label="Duration" value={formatDuration(record.durationMs)} mono />
        <Fact label="Actor" value={record.actor} />
        <Fact label="Trace" value={record.traceId || "n/a"} mono copyValue={record.traceId} />
      </div>

      <div className="logs-detail-actions">
        <button className="secondary-button" disabled={!record.traceId} onClick={() => copyText(record.traceId || "").catch(() => undefined)} type="button">
          <Copy size={14} /> Copy Trace
        </button>
        <button
          className="secondary-button"
          disabled={!record.traceId}
          onClick={() => {
            if (record.traceId) window.open(`/api/v1/telemetry/traces/${record.traceId}`, "_blank", "noopener,noreferrer");
          }}
          type="button"
        >
          <ExternalLink size={14} /> Open Trace
        </button>
      </div>

      <div className="logs-detail-tabs" role="tablist">
        <DetailTab active={tab === "timeline"} onClick={() => setTab("timeline")} label={record.kind === "trace" ? "Timeline" : "Summary"} />
        <DetailTab active={tab === "payload"} onClick={() => setTab("payload")} label="Payload" />
        <DetailTab active={tab === "diff"} onClick={() => setTab("diff")} label="Diff" />
        <DetailTab active={tab === "artifacts"} onClick={() => setTab("artifacts")} label={`Artifacts (${artifacts.length})`} />
      </div>

      <div className="logs-detail-body">
        {tab === "timeline" ? (
          record.kind === "trace" ? (
            <TraceTimeline detailState={traceDetail} record={record} />
          ) : record.kind === "live" ? (
            <LiveSummary record={record} />
          ) : (
            <AuditSummary record={record} delta={delta} />
          )
        ) : null}
        {tab === "payload" ? <PayloadPanel record={record} detail={traceDetail.detail} /> : null}
        {tab === "diff" ? <DiffPanel record={record} oldValue={oldValue} newValue={newValue} delta={delta} /> : null}
        {tab === "artifacts" ? <ArtifactsPanel artifacts={artifacts} /> : null}
      </div>
    </aside>
  );
}

function DetailTab({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button className={active ? "active" : ""} onClick={onClick} role="tab" type="button">
      {label}
    </button>
  );
}

function Fact({ label, value, mono = false, copyValue }: { label: string; value: string; mono?: boolean; copyValue?: string | null }) {
  return (
    <div className="logs-fact">
      <span>{label}</span>
      <strong className={mono ? "mono" : undefined}>{value}</strong>
      {copyValue ? (
        <button onClick={() => copyText(copyValue).catch(() => undefined)} type="button" title={`Copy ${label}`}>
          <Copy size={12} />
        </button>
      ) : null}
    </div>
  );
}

function TraceTimeline({ detailState, record }: { detailState: TraceDetailState; record: LogRecord }) {
  if (detailState.loading) return <div className="telemetry-detail-loading">Loading trace spans...</div>;
  if (detailState.error) return <div className="error-banner">{detailState.error}</div>;
  const detail = detailState.detail;
  if (!detail) return <EmptyState icon={Clock3} label="No trace details loaded." />;
  if (detail.category === "gate_malfunction") return <GateTimeline trace={detail} />;
  const traceStart = Date.parse(detail.started_at);
  const totalMs = Math.max(detail.duration_ms || 0, ...detail.spans.map((span) => {
    const end = span.ended_at ? Date.parse(span.ended_at) : Date.parse(span.started_at);
    return Math.max(0, end - traceStart);
  }), 1);

  return (
    <div className="logs-timeline">
      <div className="logs-timeline-summary">
        <Fact label="Trace ID" value={detail.trace_id} mono copyValue={detail.trace_id} />
        <Fact label="Source" value={detail.source || record.sourceLabel} />
        <Fact label="Plate" value={detail.registration_number || "n/a"} mono />
        <Fact label="Total" value={formatDuration(totalMs)} mono />
      </div>
      {detail.spans.map((span, index) => {
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
              <div className="logs-waterfall-track">
                <span style={{ left: `${offset}%`, width: `${Math.min(width, 100 - offset)}%` }} />
              </div>
              {span.error ? <p className="logs-span-error">{span.error}</p> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function GateTimeline({ trace }: { trace: TelemetryTraceDetail }) {
  const context = trace.context;
  const status = stringPayload(context.status || trace.status);
  const nextAttempt = stringPayload(context.next_attempt_scheduled_at);
  const downtimeSeconds = numberPayload(context.total_downtime_seconds);
  const attempts = stringPayload(context.fix_attempts_count || "0");
  const gateName = stringPayload(context.gate_name || trace.source || "Primary gate");
  const sortedSpans = [...trace.spans].sort((left, right) => Date.parse(left.started_at) - Date.parse(right.started_at));
  return (
    <div className="logs-timeline">
      <div className="logs-timeline-summary">
        <Fact label="Gate" value={gateName} />
        <Fact label="Status" value={titleCase(status || "unknown")} />
        <Fact label="Attempts" value={attempts} mono />
        <Fact label={status === "active" ? "Open Duration" : "Total Downtime"} value={formatSecondsDuration(downtimeSeconds)} mono />
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

function AuditSummary({ record, delta }: { record: LogRecord; delta: unknown }) {
  return (
    <div className="logs-audit-summary">
      <p>{record.summary}</p>
      <JsonBlock label="Changed Fields" value={delta} />
    </div>
  );
}

function LiveSummary({ record }: { record: LogRecord }) {
  return (
    <div className="logs-audit-summary">
      <p>{record.actionDetail}</p>
      <JsonBlock label="Live Payload" value={record.rawRealtime?.payload || {}} />
    </div>
  );
}

function PayloadPanel({ record, detail }: { record: LogRecord; detail: TelemetryTraceDetail | null }) {
  if (record.rawAudit) {
    return <JsonBlock value={{ diff: record.rawAudit.diff, metadata: record.rawAudit.metadata }} />;
  }
  if (record.rawRealtime) {
    return <JsonBlock value={{ type: record.rawRealtime.type, payload: record.rawRealtime.payload }} />;
  }
  return <JsonBlock value={detail || record.rawTrace || {}} />;
}

function DiffPanel({
  record,
  oldValue,
  newValue,
  delta
}: {
  record: LogRecord;
  oldValue: Record<string, unknown>;
  newValue: Record<string, unknown>;
  delta: unknown;
}) {
  if (!record.rawAudit) {
    return <JsonBlock label="Trace Context" value={record.rawTrace?.context || record.rawRealtime?.payload || {}} />;
  }
  return (
    <div className="logs-diff-panel">
      <JsonBlock label="Summary" value={delta} />
      <React.Suspense fallback={<div className="telemetry-detail-loading">Loading diff editor...</div>}>
        <MonacoDiffEditor
          height="260px"
          language="json"
          modified={stringifyJson(newValue)}
          options={{ readOnly: true, minimap: { enabled: false }, renderSideBySide: false, scrollBeyondLastLine: false }}
          original={stringifyJson(oldValue)}
        />
      </React.Suspense>
    </div>
  );
}

function ArtifactsPanel({ artifacts }: { artifacts: Array<Record<string, unknown>> }) {
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

function JsonBlock({ value, label }: { value: unknown; label?: string }) {
  return (
    <div className="json-block">
      {label ? <strong>{label}</strong> : null}
      <pre>{stringifyJson(value)}</pre>
    </div>
  );
}

export function LiveStreamDrawer({
  logs,
  open,
  paused,
  onClear,
  onToggleOpen,
  onTogglePaused
}: {
  logs: LogRecord[];
  open: boolean;
  paused: boolean;
  onClear: () => void;
  onToggleOpen: () => void;
  onTogglePaused: () => void;
}) {
  const [filter, setFilter] = React.useState("");
  const visible = logs.filter((record) => !filter.trim() || record.searchText.includes(filter.trim().toLowerCase()));
  return (
    <section className={open ? "logs-live-drawer open" : "logs-live-drawer"}>
      <div className="logs-live-head">
        <button className="logs-live-title" onClick={onToggleOpen} type="button">
          <Terminal size={15} />
          <strong>Live Stream</strong>
          <Badge tone={paused ? "amber" : "green"}>{paused ? "Paused" : "Live"}</Badge>
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>
        {open ? (
          <div className="logs-live-actions">
            <label className="search compact-search">
              <Search size={14} />
              <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter live events..." />
            </label>
            <button className="secondary-button" onClick={onTogglePaused} type="button">
              {paused ? <Play size={14} /> : <Pause size={14} />}
              {paused ? "Resume" : "Pause"}
            </button>
            <button className="secondary-button" onClick={onClear} type="button">
              <Trash2 size={14} /> Clear
            </button>
          </div>
        ) : null}
      </div>
      {open ? (
        <div className="logs-live-list">
          {visible.map((record) => (
            <div className={`logs-live-row ${record.tone}`} key={record.id}>
              <time>{formattedTimestamp(record.timestamp).time}</time>
              <strong>{record.actionDetail}</strong>
              <span>{record.subject}</span>
              <code>{record.traceId || record.requestId || ""}</code>
            </div>
          ))}
          {!visible.length ? <EmptyState icon={Terminal} label="No live events in this filter." /> : null}
        </div>
      ) : null}
    </section>
  );
}

export function SaveFilterModal({
  error,
  onCancel,
  onSave
}: {
  error: string;
  onCancel: () => void;
  onSave: (name: string) => void;
}) {
  const [name, setName] = React.useState("");
  return createPortal(
    <div className="modal-backdrop" role="presentation">
      <form
        className="modal-card logs-save-filter-modal"
        onSubmit={(event) => {
          event.preventDefault();
          onSave(name.trim());
        }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="save-log-filter-title"
      >
        <div className="modal-header">
          <div>
            <h2 id="save-log-filter-title">Save Filter</h2>
            <p>Keep this source and filter set for quick access.</p>
          </div>
        </div>
        <label className="form-field">
          <span>Name</span>
          <input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Gate errors today" />
        </label>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" onClick={onCancel} type="button">Cancel</button>
          <button className="secondary-button active" type="submit"><Save size={15} /> Save Filter</button>
        </div>
      </form>
    </div>,
    document.body
  );
}

export function ClearLogsConfirmModal({
  error,
  loading,
  onCancel,
  onConfirm
}: {
  error: string;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return createPortal(
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="clear-logs-confirm-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className="gate-confirm-icon danger">
              <Trash2 size={20} />
            </span>
            <div>
              <h2 id="clear-logs-confirm-title">Clear Logs?</h2>
              <p>Telemetry traces and artifacts will be purged. Audit history will be preserved.</p>
            </div>
          </div>
        </div>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Keep logs
          </button>
          <button className="danger-button" disabled={loading} onClick={onConfirm} type="button">
            {loading ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
            {loading ? "Clearing..." : "Clear Logs"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

export function exportVisible(records: LogRecord[], format: "json" | "csv") {
  exportRecords(records, format);
}

export { defaultLogsFilters };
