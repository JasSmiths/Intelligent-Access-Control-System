import { initials } from "../../lib/format";
import { titleFromEntityId } from "../../lib/settings";
import type { Group, HomeAssistantDiscovery, Person, Vehicle } from "../../api/types";
import type { BadgeTone } from "../../ui/primitives";

import type { HomeAssistantPersonSuggestion } from "./types";

export type DirectoryGroupMeta = {
  id: string;
  name: string;
  category: string | null;
};

export type DirectoryGroupSection<T> = DirectoryGroupMeta & {
  items: T[];
};

export type DirectoryGroupBucket<T> = DirectoryGroupSection<T> & {
  order: number;
};

export const unassignedDirectoryGroup: DirectoryGroupMeta = {
  id: "__unassigned__",
  name: "Unassigned",
  category: null
};

export function isResidentsDirectoryGroup(section: DirectoryGroupMeta) {
  return section.category?.trim().toLowerCase() === "family" && section.name.trim().toLowerCase() === "residents";
}

export function directoryGroupDefaultOpen(section: DirectoryGroupMeta) {
  return isResidentsDirectoryGroup(section);
}

export function buildDirectoryGroupIndex(groups: Group[]) {
  const groupById = new Map<string, Group>();
  const groupOrder = new Map<string, number>();
  groups.forEach((group, index) => {
    groupById.set(group.id, group);
    groupOrder.set(group.id, index);
  });
  return { groupById, groupOrder };
}

export function directoryGroupMetaFromGroup(group: Group): DirectoryGroupMeta {
  return {
    id: group.id,
    name: group.name,
    category: group.category
  };
}

export function directoryGroupMetaForPerson(person: Person, groupById: Map<string, Group>): DirectoryGroupMeta {
  if (person.group_id) {
    const group = groupById.get(person.group_id);
    if (group) return directoryGroupMetaFromGroup(group);
    return {
      id: person.group_id,
      name: person.group ?? "Unknown Group",
      category: person.category
    };
  }

  if (person.group) {
    return {
      id: `group-name:${person.group.trim().toLowerCase()}`,
      name: person.group,
      category: person.category
    };
  }

  return unassignedDirectoryGroup;
}

export function directoryGroupOrder(meta: DirectoryGroupMeta, groupOrder: Map<string, number>, groupCount: number) {
  if (isResidentsDirectoryGroup(meta)) return -1;
  const knownOrder = groupOrder.get(meta.id);
  if (knownOrder !== undefined) return knownOrder;
  if (meta.id === unassignedDirectoryGroup.id) return groupCount + 1000;
  return groupCount + 100;
}

export function addToDirectoryBucket<T>(
  buckets: Map<string, DirectoryGroupBucket<T>>,
  meta: DirectoryGroupMeta,
  order: number,
  item: T
) {
  const existing = buckets.get(meta.id);
  if (existing) {
    existing.items.push(item);
    return;
  }

  buckets.set(meta.id, {
    ...meta,
    items: [item],
    order
  });
}

export function directoryBucketsToSections<T>(buckets: Map<string, DirectoryGroupBucket<T>>): DirectoryGroupSection<T>[] {
  return Array.from(buckets.values())
    .filter((bucket) => bucket.items.length)
    .sort((left, right) => left.order - right.order || left.name.localeCompare(right.name))
    .map((bucket) => ({
      id: bucket.id,
      name: bucket.name,
      category: bucket.category,
      items: bucket.items
    }));
}

export function groupPeopleByDirectoryGroup(people: Person[], groups: Group[]): DirectoryGroupSection<Person>[] {
  const { groupById, groupOrder } = buildDirectoryGroupIndex(groups);
  const buckets = new Map<string, DirectoryGroupBucket<Person>>();

  for (const person of people) {
    const meta = directoryGroupMetaForPerson(person, groupById);
    addToDirectoryBucket(buckets, meta, directoryGroupOrder(meta, groupOrder, groups.length), person);
  }

  return directoryBucketsToSections(buckets);
}

export function indexPeopleByVehicleId(people: Person[]) {
  const peopleByVehicleId = new Map<string, Person[]>();
  for (const person of people) {
    for (const vehicle of person.vehicles) {
      const owners = peopleByVehicleId.get(vehicle.id) ?? [];
      owners.push(person);
      peopleByVehicleId.set(vehicle.id, owners);
    }
  }
  return peopleByVehicleId;
}

export function ownerPeopleForVehicle(
  vehicle: Vehicle,
  peopleByVehicleId: Map<string, Person[]>,
  peopleById: Map<string, Person>
) {
  const ownersById = new Map<string, Person>();

  for (const personId of vehicle.person_ids ?? []) {
    const person = peopleById.get(personId);
    if (person) ownersById.set(person.id, person);
  }

  for (const person of peopleByVehicleId.get(vehicle.id) ?? []) {
    ownersById.set(person.id, person);
  }

  if (vehicle.person_id) {
    const person = peopleById.get(vehicle.person_id);
    if (person) ownersById.set(person.id, person);
  }

  return Array.from(ownersById.values()).sort((left, right) => left.display_name.localeCompare(right.display_name));
}

export function vehicleOwnerLabel(
  vehicle: Vehicle,
  peopleByVehicleId: Map<string, Person[]>,
  peopleById: Map<string, Person>
) {
  const owners = ownerPeopleForVehicle(vehicle, peopleByVehicleId, peopleById);
  if (owners.length) return owners.map((person) => person.display_name).join(", ");
  if (vehicle.owners?.length) return vehicle.owners.join(", ");
  return vehicle.owner ?? "Unassigned";
}

export function groupVehiclesByDirectoryGroup(
  vehicles: Vehicle[],
  peopleByVehicleId: Map<string, Person[]>,
  peopleById: Map<string, Person>,
  groups: Group[]
): DirectoryGroupSection<Vehicle>[] {
  const { groupById, groupOrder } = buildDirectoryGroupIndex(groups);
  const buckets = new Map<string, DirectoryGroupBucket<Vehicle>>();

  for (const vehicle of vehicles) {
    const owners = ownerPeopleForVehicle(vehicle, peopleByVehicleId, peopleById);
    const vehicleGroups = new Map<string, DirectoryGroupMeta>();

    for (const owner of owners) {
      const meta = directoryGroupMetaForPerson(owner, groupById);
      vehicleGroups.set(meta.id, meta);
    }

    if (!vehicleGroups.size) {
      vehicleGroups.set(unassignedDirectoryGroup.id, unassignedDirectoryGroup);
    }

    for (const meta of vehicleGroups.values()) {
      addToDirectoryBucket(buckets, meta, directoryGroupOrder(meta, groupOrder, groups.length), vehicle);
    }
  }

  return directoryBucketsToSections(buckets);
}

export function motComplianceTone(status: string | null | undefined): BadgeTone {
  const normalized = String(status || "").trim().toLowerCase().replace(/_/g, " ");
  if (!normalized) return "gray";
  return normalized === "valid" || normalized === "not required" ? "green" : "red";
}

export function taxComplianceTone(status: string | null | undefined): BadgeTone {
  const normalized = String(status || "").trim().toLowerCase();
  if (!normalized) return "gray";
  if (normalized === "taxed") return "green";
  if (normalized === "sorn") return "gray";
  return "red";
}

export function vehicleComplianceExpiryLabel(value: string | null | undefined) {
  return value ? `Expires ${formatDateOnly(value)}` : "Expiry unavailable";
}

export function vehicleLastDvlaCheckLabel(value: string | null | undefined) {
  if (!value) return "Not checked yet";
  return dateOnlyKey(value) === localDateKey() ? "Last checked with DVLA: Today" : `Last checked with DVLA: ${formatDateOnly(value)}`;
}

export function suggestHomeAssistantPersonIntegrations(
  firstName: string,
  lastName: string,
  discovery: HomeAssistantDiscovery
): HomeAssistantPersonSuggestion {
  const displayName = `${firstName} ${lastName}`.trim();
  const mobile = bestHomeAssistantMatch(
    displayName,
    discovery.mobile_app_notification_services.map((service) => ({
      id: service.service_id,
      label: service.name ? `${service.name} ${service.service_id}` : service.service_id
    })),
    0.45
  );
  return {
    mobile: mobile ? { id: mobile.id, label: titleFromEntityId(mobile.id), confidence: mobile.confidence } : undefined
  };
}

export function bestHomeAssistantMatch(
  personName: string,
  candidates: Array<{ id: string; label: string }>,
  threshold: number
): { id: string; confidence: number } | null {
  const personTokens = homeAssistantNameTokens(personName);
  if (!personTokens.size) return null;
  let best: { id: string; confidence: number } | null = null;
  for (const candidate of candidates) {
    const candidateTokens = homeAssistantNameTokens(`${candidate.id} ${candidate.label}`);
    if (!candidateTokens.size) continue;
    const overlap = [...personTokens].filter((token) => candidateTokens.has(token)).length / personTokens.size;
    const personCompact = [...personTokens].sort().join("");
    const candidateCompact = [...candidateTokens].sort().join("");
    const substringScore = candidateCompact.includes(personCompact) || [...personTokens].some((token) => candidateCompact.includes(token))
      ? 0.7
      : 0;
    const confidence = Math.max(overlap, substringScore);
    if (!best || confidence > best.confidence) {
      best = { id: candidate.id, confidence };
    }
  }
  return best && best.confidence >= threshold ? best : null;
}

export function homeAssistantNameTokens(value: string) {
  return new Set(
    value
      .toLowerCase()
      .replace(/notify\.mobile_app_/g, " ")
      .split(/[^a-z0-9]+/)
      .filter(Boolean)
  );
}

export function groupCategoryTone(category: string): BadgeTone {
  if (category === "family") return "green";
  if (category === "friends") return "blue";
  if (category === "visitors") return "amber";
  if (category === "contractors") return "gray";
  return "gray";
}

export function vehicleTitle(vehicle: Vehicle) {
  return vehicle.description || [vehicle.color, vehicle.make, vehicle.model].filter(Boolean).join(" ") || "Vehicle details pending";
}

export function normalizePlateInput(value: string) {
  return value.replace(/[^a-z0-9]/gi, "").toUpperCase();
}

export function personInitials(person: Pick<Person, "first_name" | "last_name" | "display_name">) {
  const first = person.first_name?.trim()[0] ?? "";
  const last = person.last_name?.trim()[0] ?? "";
  return (first + last || initials(person.display_name)).toUpperCase();
}

export function formatDateOnly(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric"
  }).format(dateOnlyToDate(value));
}

export function dateOnlyKey(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (match) return `${match[1]}-${match[2]}-${match[3]}`;
  return localDateKey(new Date(value));
}

export function localDateKey(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function dateOnlyToDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return new Date(value);
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}
