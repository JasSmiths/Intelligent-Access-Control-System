import { AuditLog,BadgeTone,RealtimeMessage } from "../../shared";

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

type GateMalfunctionTimelineEvent = {
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
  slowOnly: boolean;
};

export type SimplifiedLogsFilters = {
  source: LogSourceKey;
  query: string;
  timeRange: string;
  level: string;
  status: string;
  actor: string;
  subject: string;
  slowOnly: boolean;
  from: string | null;
};

export type NarrativeLogItem = {
  id: string;
  recordId: string;
  kind: LogRecordKind;
  source: LogSourceKey;
  timestamp: string;
  title: string;
  what: string;
  reason: string;
  why: string;
  supportingDetail: string;
  details: string;
  summary: string;
  subject: string;
  actor: string;
  status: string;
  level: string;
  outcome: string;
  tone: BadgeTone;
  durationMs: number | null;
  traceId: string | null;
  requestId: string | null;
  sourceLabel: string;
  sourceDetail: string;
  searchText: string;
  raw: LogRecord;
};

export type LprWaterfallPhase =
  | "capture"
  | "webhook"
  | "debounce"
  | "identity"
  | "schedule"
  | "direction"
  | "presence"
  | "persistence"
  | "snapshot"
  | "gate"
  | "garage"
  | "notification"
  | "integration"
  | "complete"
  | "diagnostic";

export type LprWaterfallStepStatus = "ok" | "warning" | "error" | "pending" | "skipped";

export type SlowStepWarning = {
  stepId: string;
  severity: "warning" | "critical";
  title: string;
  reason: string;
  durationMs: number;
  thresholdMs: number;
};

export type LprTimingObservation = {
  id?: string | null;
  source?: string | null;
  source_detail?: string | null;
  registration_number?: string | null;
  raw_value?: string | null;
  candidate_kind?: string | null;
  received_at?: string | null;
  captured_at?: string | null;
  captured_to_received_ms?: number | null;
  ms_from_access_event_time?: number | null;
  event_id?: string | null;
  camera_id?: string | null;
  camera_name?: string | null;
  confidence?: number | null;
  confidence_scale?: string | null;
  protect_action?: string | null;
  protect_model?: string | null;
  payload_path?: string | null;
  [key: string]: unknown;
};

export type LprWaterfallResponse = {
  trace_id?: string | null;
  registration_number?: string | null;
  title?: string | null;
  summary?: string | null;
  reason?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  timezone?: string | null;
  observations?: unknown[];
  slowest_observations?: unknown[];
  latest_observation?: unknown;
  steps?: unknown[];
  waterfall?: unknown;
  count?: number;
  filters?: Record<string, unknown>;
  note?: string | null;
  [key: string]: unknown;
};

export type LprWaterfallStep = {
  id: string;
  phase: LprWaterfallPhase;
  label: string;
  source: string;
  startedAt: string | null;
  endedAt: string | null;
  offsetMs: number;
  durationMs: number | null;
  status: LprWaterfallStepStatus;
  tone: BadgeTone;
  reason: string;
  detail: string;
  warning?: SlowStepWarning;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  raw?: unknown;
};

export type LprWaterfallModel = {
  id: string;
  traceId: string | null;
  registrationNumber: string | null;
  title: string;
  reason: string;
  source: string;
  decision: string | null;
  direction: string | null;
  status: string;
  startedAt: string | null;
  endedAt: string | null;
  totalDurationMs: number;
  steps: LprWaterfallStep[];
  warnings: SlowStepWarning[];
  observations: LprTimingObservation[];
  summary: string;
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
