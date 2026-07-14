export type InvestigationOutcome =
  | "succeeded"
  | "blocked"
  | "skipped"
  | "failed"
  | "pending"
  | "cancelled"
  | "unknown";

export type InvestigationSeverity = "info" | "warning" | "error" | "critical" | string;

export type CorrelationConfidence = "exact" | "strong" | "inferred" | "unlinked" | string;

export type FilterOption = {
  id?: string;
  value: string;
  label: string;
  kind?: string;
  count?: number | null;
};

export type InvestigationFilterCatalog = {
  site_timezone: string;
  devices: FilterOption[];
  automations: FilterOption[];
  schedules: FilterOption[];
  integrations: FilterOption[];
  categories: FilterOption[];
  severities: FilterOption[];
  outcomes: FilterOption[];
  triggers: FilterOption[];
  actors: FilterOption[];
};

export type InvestigationRange = {
  key?: string | null;
  from?: string | null;
  to?: string | null;
  label?: string | null;
};

export type ConfigurationEvidence = {
  type: string;
  recorded_at_decision_time: boolean;
  label: string;
  value: unknown;
  warning?: string | null;
};

export type InvestigationEvidence = {
  id: string;
  episode_id?: string | null;
  event_id?: string | null;
  citation_id?: string | null;
  timestamp: string;
  timestamp_precision?: string | null;
  type?: string | null;
  title: string;
  description: string;
  stage?: string | null;
  source?: string | null;
  source_subsystem?: string | null;
  category?: string | null;
  outcome?: InvestigationOutcome | string | null;
  severity?: InvestigationSeverity | null;
  reason_code?: string | null;
  trace_id?: string | null;
  correlation_id?: string | null;
  entity_ids?: Record<string, string | null> | null;
  command_sent?: boolean | null;
  command_dispatch?: string | null;
  configuration?: ConfigurationEvidence | null;
  metadata?: Record<string, unknown> | null;
  raw?: unknown;
};

export type ActivityEpisode = {
  episode_id: string;
  kind: "trace" | "audit" | string;
  trace_id?: string | null;
  correlation_id?: string | null;
  correlation_confidence?: CorrelationConfidence | null;
  occurred_at: string;
  ended_at?: string | null;
  duration_ms?: number | null;
  title: string;
  summary: string;
  reason_code?: string | null;
  outcome: InvestigationOutcome | string;
  severity?: InvestigationSeverity | null;
  category?: string | null;
  actor?: string | null;
  source?: string | null;
  audit_id?: string | null;
  dispatch_state?: "withheld" | "attempted_rejected" | "accepted_unverified" | "verified" | "not_applicable" | "unknown" | string;
  correlation?: { confidence: CorrelationConfidence; basis: string };
  automation?: {
    run_id?: string | null;
    rule_id?: string | null;
    name?: string | null;
    status?: string | null;
    trigger?: string | null;
  } | null;
  entities?: Array<{ type: string; id: string; label: string }>;
  evidence_count?: number | null;
  routine?: boolean;
};

export type ActivityEpisodeDetail = {
  episode: ActivityEpisode;
  timeline: InvestigationEvidence[];
  citations: Array<{ id: string; label: string; timestamp: string; episode_id?: string | null }>;
  configuration_context: ConfigurationEvidence[];
  raw: unknown;
  site_timezone: string;
};

export type ActivityPage = {
  items: ActivityEpisode[];
  next_cursor: string | null;
  site_timezone: string;
  resolved_range: InvestigationRange;
  applied_filters?: Record<string, unknown>;
  partial?: boolean;
  partial_reason?: string | null;
  total_estimate?: number | null;
};

export type OverviewRepeat = {
  key: string;
  title: string;
  summary?: string;
  count: number;
  reason_code?: string | null;
  outcome?: InvestigationOutcome | string;
  latest_at?: string | null;
  filters?: Partial<InvestigationQuery>;
  episode_id?: string | null;
};

export type InvestigationOverview = {
  site_timezone: string;
  resolved_range: InvestigationRange;
  recent_problems: ActivityEpisode[];
  incomplete_runs: ActivityEpisode[];
  repeated_problems: OverviewRepeat[];
  important_activity: ActivityEpisode[];
};

export type InvestigationClaim = {
  id?: string | null;
  text: string;
  evidence_ids?: string[];
  citation_ids?: string[];
};

export type InvestigationAnswer = {
  question?: string;
  answer: string;
  most_likely_reason?: string | null;
  outcome?: InvestigationOutcome | string | null;
  classification?: string | null;
  dispatch_state?: string | null;
  certainty?: "high" | "medium" | "low" | string | null;
  site_timezone: string;
  resolved_range: InvestigationRange;
  interpreted_filters?: Record<string, unknown>;
  claims?: InvestigationClaim[];
  citations?: Array<{ id: string; label: string; timestamp: string; episode_id?: string | null }>;
  evidence?: InvestigationEvidence[];
  episodes?: ActivityEpisode[];
  missing_evidence?: string[];
  ai_used?: boolean;
  mode?: string | null;
};

export type InvestigationQuery = {
  range: "today" | "yesterday" | "24h" | "7d" | "custom";
  from: string;
  to: string;
  device: string;
  automation: string;
  schedule: string;
  integration: string;
  category: string;
  outcome: string;
  severity: string;
  actor: string;
  trigger: string;
  trace: string;
  q: string;
  includeRoutine: boolean;
};

export const EMPTY_FILTER_CATALOG: InvestigationFilterCatalog = {
  site_timezone: "UTC",
  devices: [],
  automations: [],
  schedules: [],
  integrations: [],
  categories: [],
  severities: [],
  outcomes: [],
  triggers: [],
  actors: []
};
