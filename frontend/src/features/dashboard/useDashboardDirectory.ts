import React from "react";
import { listVehicles, lookupPeople, lookupVehicleRegistrations } from "../../api/directory";
import type { AccessEvent, ExpectedPresenceSummary, Person, Presence, Vehicle } from "../../api/types";
import { insideNowRoster, isToday } from "./model";

function mergeById<T extends { id: string }>(resolved: T[], seed: T[]): T[] {
  return [...new Map([...resolved, ...seed].map((item) => [item.id, item])).values()];
}
const plateKey = (plate: string) => plate.replace(/[^a-zA-Z0-9]/g, "").toUpperCase();

export function useDashboardDirectory({ presence, expectedPresence, events, people: seedPeople, vehicles: seedVehicles, refreshToken = 0 }: {
  presence: Presence[];
  expectedPresence: ExpectedPresenceSummary | null;
  events: AccessEvent[];
  people: Person[];
  vehicles: Vehicle[];
  refreshToken?: number;
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

  React.useEffect(() => {
    const controller = new AbortController();
    setError("");
    const read = async () => {
      const knownPlates = new Set(seedVehicles.map((vehicle) => plateKey(vehicle.registration_number)));
      const plates = visiblePlatesKey ? visiblePlatesKey.split("|").filter((plate) => !knownPlates.has(plate)) : [];
      const [vehicles, active] = await Promise.all([
        lookupVehicleRegistrations(plates, { signal: controller.signal }),
        listVehicles({ active: true, limit: 1 }, { signal: controller.signal })
      ]);
      if (controller.signal.aborted) return;
      const directoryVehicles = mergeById(vehicles, seedVehicles);
      const ownerIds = directoryVehicles.filter((vehicle) => visiblePlatesKey.split("|").includes(plateKey(vehicle.registration_number)))
        .flatMap((vehicle) => [...(vehicle.person_ids ?? []), ...(vehicle.person_id ? [vehicle.person_id] : [])]);
      const knownPeople = new Set(seedPeople.map((person) => person.id));
      const personIds = [...new Set([...(visiblePeopleKey ? visiblePeopleKey.split("|") : []), ...ownerIds])]
        .filter((id) => !knownPeople.has(id));
      const people = await lookupPeople(personIds, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setResolvedVehicles(vehicles); setResolvedPeople(people); setActiveVehicleCount(active.total);
    };
    read().catch((failure: unknown) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Dashboard directory details could not be refreshed.");
    });
    return () => controller.abort();
  }, [visiblePeopleKey, visiblePlatesKey, seedPeople, seedVehicles, refreshToken]);

  return {
    people: React.useMemo(() => mergeById(resolvedPeople, seedPeople), [resolvedPeople, seedPeople]),
    vehicles: React.useMemo(() => mergeById(resolvedVehicles, seedVehicles), [resolvedVehicles, seedVehicles]),
    activeVehicleCount: activeVehicleCount ?? seedVehicles.filter((vehicle) => vehicle.is_active !== false).length,
    error
  };
}
