import { AuditLog, BadgeTone, RealtimeMessage } from "../../shared";

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

export type TelemetrySummary = {
  traces: {
    total: number;
    by_category: Record<string, number>;
    by_level: Record<string, number>;
    by_status: Record<string, number>;
  };
  audit: {
    total: number;
    by_category: Record<string, number>;
    by_level: Record<string, number>;
    by_outcome: Record<string, number>;
  };
  storage: TelemetryStorageSummary;
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

export type LogSourceKey =
  | "all"
  | "lpr"
  | "access"
  | "gate"
  | "maintenance"
  | "automation"
  | "ai"
  | "crud"
  | "api"
  | "integrations"
  | "updates"
  | "live";

export type LogRecordKind = "trace" | "audit" | "live";

export type LogRecord = {
  id: string;
  kind: LogRecordKind;
  source: LogSourceKey;
  timestamp: string;
  category: string;
  sourceLabel: string;
  sourceDetail: string;
  action: string;
  actionDetail: string;
  subject: string;
  subjectDetail: string;
  status: string;
  level: string;
  outcome: string;
  durationMs: number | null;
  actor: string;
  traceId: string | null;
  requestId: string | null;
  summary: string;
  searchText: string;
  tone: BadgeTone;
  rawTrace?: TelemetryTrace;
  rawAudit?: AuditLog;
  rawRealtime?: RealtimeMessage;
};

export type LogsFilters = {
  query: string;
  timeRange: string;
  level: string;
  status: string;
  actor: string;
  subject: string;
};

export type SavedLogsFilter = {
  id: string;
  name: string;
  source: LogSourceKey;
  filters: LogsFilters;
};

export type TraceDetailState = {
  loading: boolean;
  error: string;
  detail: TelemetryTraceDetail | null;
};
