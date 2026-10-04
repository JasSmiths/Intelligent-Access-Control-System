import { api, createActionConfirmation, type ApiRequestOptions } from "./client";
import type { Person } from "./types";

export type RecoveryMethod = "phone_automatic" | "camera_automatic" | "resident_confirmation" | "none";
export type RecoveryTimelineEntry = { at: string; stage: string; status: string; reason: string; details?: Record<string, unknown> };
export type RecoveryAttempt = {
  id: string;
  occurred_at: string;
  owner_id: string | null;
  owner_name: string | null;
  vehicle_id: string | null;
  registration_number: string;
  event_id: string | null;
  recovery_event_id?: string | null;
  saga_id: string | null;
  command_id: string | null;
  method: RecoveryMethod;
  outcome: string;
  reason: string;
  checks: Record<string, unknown>;
  timeline: RecoveryTimelineEntry[];
  notification: {
    status?: string; expires_at?: string | null; action_at?: string | null; delivery_id?: string | null;
    apology_delivery_id?: string | null; canonical_attempt_id?: string | null;
    canonical_outcome?: string; canonical_reason?: string;
    [key: string]: unknown;
  };
  duration_ms: number | null;
  policy_version: string | number;
};
export type RecoveryAttemptPage = { items: RecoveryAttempt[]; total: number; offset: number; limit: number };
export type RecoveryAttemptFilters = { owner_id?: string; registration_number?: string; outcome?: string; method?: string; from_at?: string; to_at?: string; offset?: number; limit?: number };
export type RecoveryOwnerSettings = Pick<Person, "missed_exit_recovery_enabled" | "missed_exit_recovery_tracker_entity_id">;

export function recoveryAttemptQuery(filters: RecoveryAttemptFilters) {
  const query = new URLSearchParams();
  for (const key of ["owner_id", "registration_number", "outcome", "method", "from_at", "to_at"] as const) {
    const value = filters[key]?.trim();
    if (value) query.set(key, value);
  }
  query.set("offset", String(Math.max(0, Math.floor(filters.offset ?? 0))));
  query.set("limit", String(Math.min(100, Math.max(1, Math.floor(filters.limit ?? 25)))));
  return query.toString();
}
export const missedExitRecoveryApi = {
  attempts(filters: RecoveryAttemptFilters, options: ApiRequestOptions = {}) {
    return api.get<RecoveryAttemptPage>(`/api/v1/missed-exit-recovery/attempts?${recoveryAttemptQuery(filters)}`, options);
  },
  attempt(id: string, options: ApiRequestOptions = {}) {
    return api.get<RecoveryAttempt>(`/api/v1/missed-exit-recovery/attempts/${encodeURIComponent(id)}`, options);
  },
  async saveOwner(person: Person, payload: RecoveryOwnerSettings) {
    const confirmation = await createActionConfirmation("person.update", { person_id: person.id, ...payload }, {
      target_entity: "Person", target_id: person.id, target_label: person.display_name, reason: "Update missed exit recovery owner settings"
    });
    return api.patch<Person>(`/api/v1/people/${encodeURIComponent(person.id)}`, { ...payload, confirmation_token: confirmation.confirmation_token });
  }
};
