import { api } from "./client";

export type AccessEvidenceCandidate = {
  ingest_id: string;
  ordinal: number;
  registration_number: string;
  detected_registration_number: string;
  confidence: number;
  captured_at: string;
  candidate_registration_numbers: string[];
  known_vehicle_plate_match: Record<string, unknown> | null;
  visitor_pass_plate_match: Record<string, unknown> | null;
  webhook_trace: Record<string, unknown>;
};
export type AccessEvidencePage = {
  event_id: string;
  window_id: string;
  version: 2;
  total: number;
  items: AccessEvidenceCandidate[];
  next_cursor: string | null;
};

export function getAccessEvidencePage(eventId: string, cursor: string | null = null, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor) params.set("cursor", cursor);
  return api.get<AccessEvidencePage>(`/api/v1/events/${encodeURIComponent(eventId)}/candidates?${params}`, { signal });
}
