import { api, createActionConfirmation, type ApiRequestOptions } from "./client";
import type { Vehicle } from "./types";

export type ProviderOutcome = {
  status: "found" | "not_found" | "disabled" | "deferred" | "failed";
  checked_at: string | null;
  retry_at: string | null;
  error: string | null;
};
export type VehicleInformationSummary = {
  mot_source?: "dvla" | "dvsa" | null;
  mot_expiry_kind?: "test" | "first_test" | null;
  mot_checked_at?: string | null;
  mot_freshness?: "fresh" | "stale" | "unknown";
  information_checked_at?: string | null;
  information_outcome?: Partial<Record<"dvla" | "dvsa", ProviderOutcome>>;
};
export type VehicleInformation = VehicleInformationSummary & {
  registration_number: string;
  make: string | null;
  model: string | null;
  colour: string | null;
  fuel_type: string | null;
  mot_status: string | null;
  mot_expiry: string | null;
  tax_status: string | null;
  tax_expiry: string | null;
  last_dvla_lookup_date: string | null;
  providers: Record<"dvla" | "dvsa", ProviderOutcome>;
};
export type MotTest = {
  number: string | null;
  completed_at: string | null;
  result: "PASSED" | "FAILED";
  expiry: string | null;
  mileage: string | null;
  mileage_unit: string | null;
  mileage_read: string | null;
  source: string | null;
  defects: { text: string | null; type: string | null; dangerous: boolean | null }[] | null;
};
export type MotHistoryPage = {
  registration_number: string;
  checked_at: string | null;
  freshness: "fresh" | "stale" | "unknown";
  outcome: string;
  items: MotTest[];
  total: number;
  next_cursor: string | null;
};
export function lookupVehicleInformation(registration: string, options: ApiRequestOptions = {}) {
  return api.post<VehicleInformation>("/api/v1/integrations/vehicles/lookup", { registration_number: registration }, options);
}
export async function refreshVehicleInformation(vehicle: Vehicle, options: ApiRequestOptions = {}) {
  const confirmation = await createActionConfirmation("vehicle.information_refresh", { vehicle_id: vehicle.id }, {
    target_entity: "Vehicle", target_id: vehicle.id, target_label: vehicle.registration_number,
    reason: "Refresh vehicle information"
  });
  return api.post<Vehicle>(`/api/v1/vehicles/${vehicle.id}/refresh-information`, {
    confirmation_token: confirmation.confirmation_token
  }, options);
}
export function readMotHistory(vehicleId: string, cursor: string | null, options: ApiRequestOptions = {}) {
  const query = new URLSearchParams({ limit: "10" });
  if (cursor) query.set("cursor", cursor);
  return api.get<MotHistoryPage>(`/api/v1/vehicles/${vehicleId}/mot-history?${query}`, options);
}
