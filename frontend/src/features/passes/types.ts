import type { AuditLog } from "../../api/types";

export type VisitorPassStatus = "active" | "scheduled" | "used" | "expired" | "cancelled";

export type VisitorPassType = "one-time" | "duration";

export type VisitorPass = {
  id: string;
  visitor_name: string;
  pass_type: VisitorPassType;
  visitor_phone: string | null;
  expected_time: string;
  window_minutes: number;
  valid_from: string | null;
  valid_until: string | null;
  window_start: string;
  window_end: string;
  status: VisitorPassStatus;
  creation_source: string;
  source_reference: string | null;
  created_by_user_id: string | null;
  created_by: string | null;
  arrival_time: string | null;
  departure_time: string | null;
  number_plate: string | null;
  vehicle_make: string | null;
  vehicle_colour: string | null;
  duration_on_site_seconds: number | null;
  duration_human: string | null;
  arrival_event_id: string | null;
  departure_event_id: string | null;
  telemetry_trace_id: string | null;
  created_at: string;
  updated_at: string;
};

export type VisitorPassLogEntry = AuditLog & {
  actor_user_label: string | null;
};

export type VisitorPassTone = "blue" | "green" | "orange" | "red" | "gray";
