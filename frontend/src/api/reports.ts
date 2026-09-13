import { api, type ApiRequestOptions } from "./client";
import type { AccessEvent, Person, Presence } from "./types";

export type ReportDurationInfo = {
  label: string;
  tone?: "muted" | "new";
  tooltip?: string;
  tooltipDetail?: string;
};

export type ReportSnapshotVehicle = Person["vehicles"][number] & {
  title?: string;
  mot_label?: string;
  tax_label?: string;
  mot_tone?: "green" | "red" | "muted";
  tax_tone?: "green" | "red" | "muted";
};

export type ReportSnapshotPerson = Omit<Person, "group_id" | "schedule_id" | "schedule" | "is_active" | "notes" | "garage_door_entity_ids" | "home_assistant_mobile_app_notify_service" | "home_assistant_presence_input_boolean_entity_ids" | "home_assistant_presence_input_boolean_entry_action" | "home_assistant_presence_input_boolean_exit_action" | "profile_photo_data_url" | "vehicles"> & {
  profile_photo_data_url?: string | null;
  vehicles: ReportSnapshotVehicle[];
};

export type ReportSnapshotEvent = Omit<AccessEvent, "external_admission_mode" | "external_admission_source" | "movement_saga"> & {
  confidence_percent?: number;
  detail?: string;
  duration?: ReportDurationInfo;
  occurred_label?: string;
  source_label?: string;
  tone?: "green" | "blue" | "red";
  type_label?: string;
};

export type ReportSnapshotTimelineEvent = {
  id: string;
  registration_number: string;
  direction: AccessEvent["direction"];
  decision: AccessEvent["decision"];
  occurred_at: string;
  label: string;
  tone: "green" | "blue" | "red";
  progress: number;
};

export type ReportSnapshot = {
  report_id: string | null;
  subject_type?: "person" | "visitor_pass";
  generated_at: string;
  generated_label: string;
  person: ReportSnapshotPerson;
  period: {
    start: string;
    end: string;
    label: string;
    start_label: string;
    end_label: string;
    duration_label: string;
    timezone: string;
  };
  presence: {
    state: Presence["state"];
    last_changed_at: string | null;
  };
  options: {
    include_denied: boolean;
    include_snapshots: boolean;
    include_confidence: boolean;
  };
  summary: {
    arrivals: number;
    departures: number;
    denied: number;
    total: number;
    first_event: string;
    last_event: string;
  };
  events: ReportSnapshotEvent[];
  timeline: {
    all: ReportSnapshotTimelineEvent[];
    selected: ReportSnapshotTimelineEvent[];
  };
};

export type ReportExportResponse = {
  report_id: string;
  created_at: string | null;
  download_url: string;
  pdf_bytes: number;
  report: ReportSnapshot;
};

export type ReportRequest = {
  person_id?: string;
  visitor_pass_id?: string;
  period_start: string;
  period_end: string;
  include_denied: boolean;
  include_snapshots: boolean;
  include_confidence: boolean;
};
export type ReportPreviewRequest = ReportRequest & {
  period_start_fold?: 0 | 1;
  period_end_fold?: 0 | 1;
};
export type ReportTimeChoice = {
  field: "period_start" | "period_end";
  local_time: string;
  choices: Array<{ fold: 0 | 1; utc_offset_minutes: number; label: string }>;
};
export type ReportPreviewResponse =
  | { status: "ready"; complete: true; report: ReportSnapshot }
  | { status: "time_choice_required"; site_timezone: string; time_choices: ReportTimeChoice[] };
export type ReportPreviewContext = { site_timezone: string; now: string };

export const reportsApi = {
  context(options: ApiRequestOptions = {}) {
    return api.get<ReportPreviewContext>("/api/v1/reports/context", options);
  },
  preview(request: ReportPreviewRequest, options: ApiRequestOptions = {}) {
    return api.post<ReportPreviewResponse>("/api/v1/reports/person-movements/preview", request, options);
  },
  export(request: ReportRequest, options: ApiRequestOptions = {}) {
    return api.post<ReportExportResponse>("/api/v1/reports/person-movements/export", request, options);
  },
  load(reportId: string, options: ApiRequestOptions = {}) {
    return api.get<ReportExportResponse>(`/api/v1/reports/${encodeURIComponent(reportId)}`, options);
  }
};
