import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { dashboardEventMatches } from "./reads";
import React from "react";
import { listVehicles, lookupPeople, lookupVehicleRegistrations } from "../../api/directory";
import type { AccessEvent, ExpectedPresenceSummary, Person, Presence, Vehicle } from "../../api/types";
import { insideNowRoster, isToday } from "./model";

const plateKey = (plate: string) => plate.replace(/[^a-zA-Z0-9]/g, "").toUpperCase();

export function useDashboardDirectory({ presence, expectedPresence, events }: {
  presence: Presence[];
  expectedPresence: ExpectedPresenceSummary | null;
  events: AccessEvent[];
}) {
  const [resolvedPeople, setResolvedPeople] = React.useState<Person[]>([]);
  const [resolvedVehicles, setResolvedVehicles] = React.useState<Vehicle[]>([]);
  const [activeVehicleCount, setActiveVehicleCount] = React.useState<number | null>(null);
  const [error, setError] = React.useState("");
  const visiblePeopleKey = [...new Set([
    ...insideNowRoster(presence, new Map()).slice(0, 6).map((person) => person.id),
    ...(expectedPresence?.people ?? []).slice(0, 6).map((person) => person.person_id)
  ])].sort().join("|");
  const visiblePlatesKey = [...new Set([
    ...events.slice(0, 5),
    ...events.filter((event) => event.direction === "exit" && isToday(event.occurred_at))
      .slice().sort((left, right) => Date.parse(right.occurred_at) - Date.parse(left.occurred_at) || left.id.localeCompare(right.id)).slice(0, 6)
  ].map((event) => plateKey(event.registration_number)).filter(Boolean))].sort().join("|");

  const read = React.useCallback(async (signal: AbortSignal) => {
    setError("");
    try {
      const plates = visiblePlatesKey ? visiblePlatesKey.split("|") : [];
      const [vehicles, active] = await Promise.all([
        lookupVehicleRegistrations(plates, { signal }), listVehicles({ active: true, limit: 1 }, { signal })
      ]);
      if (signal.aborted) return;
      const ownerIds = vehicles.flatMap((vehicle) => [...(vehicle.person_ids ?? []), ...(vehicle.person_id ? [vehicle.person_id] : [])]);
      const personIds = [...new Set([...(visiblePeopleKey ? visiblePeopleKey.split("|") : []), ...ownerIds])];
      const people = await lookupPeople(personIds, { signal });
      if (!signal.aborted) { setResolvedVehicles(vehicles); setResolvedPeople(people); setActiveVehicleCount(active.total); }
    } catch (failure) { if (!signal.aborted) setError(failure instanceof Error ? failure.message : "Dashboard directory details could not be refreshed."); throw failure; }
  }, [visiblePeopleKey, visiblePlatesKey]);
  const refresh = useCoordinatedRead(read, dashboardEventMatches);

  return {
    refresh,
    people: resolvedPeople,
    vehicles: resolvedVehicles,
    activeVehicleCount: activeVehicleCount ?? 0,
    error
  };
}
