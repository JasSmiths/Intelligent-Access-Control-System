import { AlertTriangle, Car, Lock, LogOut } from "lucide-react";
import type React from "react";
import type { AccessEvent, AlertSeverity, Anomaly, ExpectedPresencePerson, HomeAssistantManagedCover, Person, Presence, Vehicle } from "../../api/types";
import type { BadgeTone } from "../../ui/primitives";
import { titleCase, visitorEventDisplayName } from "../../lib/format";
import { mediaSource } from "../../lib/media";
import type { DashboardCommand, DoorCommandAction } from "./types";

export function commandForGate(
  label: string,
  state: string,
  setPendingCommand: React.Dispatch<React.SetStateAction<DashboardCommand | null>>,
  setCommandError: React.Dispatch<React.SetStateAction<string>>,
  deviceKey?: string
) {
  const normalized = normalizeGateState(state);
  if (normalized !== "closed") return undefined;
  return () => {
    setCommandError("");
    setPendingCommand({ kind: "gate", label, action: "open", ...(deviceKey ? { entity_id: deviceKey } : {}) });
  };
}

export function commandForGarageDoor(
  door: HomeAssistantManagedCover,
  setPendingCommand: React.Dispatch<React.SetStateAction<DashboardCommand | null>>,
  setCommandError: React.Dispatch<React.SetStateAction<string>>
) {
  const normalized = normalizeGateState(door.state ?? "unknown");
  if (!["open", "closed"].includes(normalized)) return undefined;
  const action = normalized === "open" ? "close" : "open";
  return () => {
    setCommandError("");
    setPendingCommand({ kind: "garage_door", entity_id: door.entity_id, label: door.name || door.entity_id, action });
  };
}

export function inProgressState(action: DoorCommandAction) {
  return action === "open" ? "opening" : "closing";
}

export function gateStateDisplay(state: string): { label: string; tone: BadgeTone; actionable: boolean } {
  const normalized = state.toLowerCase();
  if (normalized === "open") return { label: "Open", tone: "green", actionable: true };
  if (normalized === "opening") return { label: "Opening", tone: "amber", actionable: false };
  if (normalized === "closed") return { label: "Closed", tone: "gray", actionable: true };
  if (normalized === "closing") return { label: "Closing", tone: "amber", actionable: false };
  return { label: "Unknown", tone: "amber", actionable: false };
}

export function normalizeGateState(state: string) {
  const normalized = state.toLowerCase();
  if (["open", "opening"].includes(normalized)) return "open";
  if (["closed", "closing"].includes(normalized)) return "closed";
  return "unknown";
}

export type ExpectedPresenceTooltipPerson = ExpectedPresencePerson & {
  profilePhotoDataUrl: string | null;
};

export type PresenceRosterPerson = {
  id: string;
  display_name: string;
  profilePhotoDataUrl: string | null;
  detail: string;
};

export function insideNowRoster(rows: Presence[], peopleById: Map<string, Person>, now = new Date()): PresenceRosterPerson[] {
  return rows
    .filter((row) => row.state === "present")
    .slice()
    .sort((left, right) => {
      const byTime = timeValue(right.last_changed_at) - timeValue(left.last_changed_at);
      return byTime || left.display_name.localeCompare(right.display_name);
    })
    .map((row) => ({
      id: row.person_id,
      display_name: row.display_name,
      profilePhotoDataUrl: profilePhotoForPerson(peopleById.get(row.person_id)),
      detail: insideSinceLabel(row.last_changed_at, now)
    }));
}

export function exitedTodayRoster(events: AccessEvent[], vehicles: Vehicle[], people: Person[], now = new Date()): PresenceRosterPerson[] {
  const peopleById = new Map(people.map((person) => [person.id, person]));
  const vehiclesByRegistration = new Map(vehicles.map((vehicle) => [vehicle.registration_number.toUpperCase(), vehicle]));
  return events
    .filter((event) => event.direction === "exit" && isToday(event.occurred_at, now))
    .slice()
    .sort((left, right) => timeValue(right.occurred_at) - timeValue(left.occurred_at) || left.id.localeCompare(right.id))
    .map((event) => exitRosterPerson(event, vehiclesByRegistration.get(event.registration_number.toUpperCase()), people, peopleById));
}

export function exitRosterPerson(
  event: AccessEvent,
  vehicle: Vehicle | undefined,
  people: Person[],
  peopleById: Map<string, Person>
): PresenceRosterPerson {
  const time = formatTime(event.occurred_at);
  const visitorName = visitorEventDisplayName(event);
  if (visitorName) {
    return { id: event.id, display_name: visitorName, profilePhotoDataUrl: null, detail: `Exited at ${time}` };
  }
  const owners = ownersForExit(event.registration_number, vehicle, people, peopleById);
  if (owners.length) {
    return {
      id: event.id,
      display_name: owners.map((person) => person.display_name).join(", "),
      profilePhotoDataUrl: owners.length === 1 ? profilePhotoForPerson(owners[0]) : null,
      detail: `Exited at ${time}`
    };
  }
  const ownerLabel = vehicle?.owners?.filter(Boolean).join(", ") || vehicle?.owner || "";
  if (ownerLabel) {
    return { id: event.id, display_name: ownerLabel, profilePhotoDataUrl: null, detail: `Exited at ${time}` };
  }
  return {
    id: event.id,
    display_name: "Unknown",
    profilePhotoDataUrl: null,
    detail: event.registration_number ? `${event.registration_number} · ${time}` : `Exited at ${time}`
  };
}

export function ownersForExit(registration: string, vehicle: Vehicle | undefined, people: Person[], peopleById: Map<string, Person>) {
  const owners = new Map<string, Person>();
  const add = (person: Person | undefined) => {
    if (person) owners.set(person.id, person);
  };
  for (const personId of vehicle?.person_ids ?? []) add(peopleById.get(personId));
  if (vehicle?.person_id) add(peopleById.get(vehicle.person_id));
  const key = registration.toUpperCase();
  for (const person of people) {
    if ((person.vehicles ?? []).some((item) => item.registration_number.toUpperCase() === key)) add(person);
  }
  return [...owners.values()].sort((left, right) => left.display_name.localeCompare(right.display_name));
}

export function insideSinceLabel(value: string | null, now: Date) {
  if (!value) return "Currently inside";
  if (isToday(value, now)) return `Since ${formatTime(value)}`;
  const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(new Date(value));
  return `Since ${date}`;
}

export function personCountLabel(count: number) {
  return `${count} ${count === 1 ? "person" : "people"}`;
}

export function exitCountLabel(count: number) {
  return `${count} ${count === 1 ? "exit" : "exits"}`;
}

export function profilePhotoForPerson(person: Person | undefined) {
  if (!person) return null;
  return mediaSource(person.profile_photo_url, person.profile_photo_data_url, "thumb") || null;
}

export function timeValue(value: string | null) {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function expectedPresenceTimingLabel(person: ExpectedPresenceTooltipPerson) {
  if (person.evidence_days === 0) {
    return person.typical_arrival ? `Seen today at ${person.typical_arrival}` : "Seen today";
  }
  if (person.typical_arrival) {
    return `Usually ${person.typical_arrival}`;
  }
  return `${person.evidence_days} routine ${person.evidence_days === 1 ? "day" : "days"}`;
}

export function initialsForName(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return parts.slice(0, 2).map((part) => part[0]?.toUpperCase() ?? "").join("");
}

export function presenceSegmentWidth(value: number, total: number) {
  if (!total || !value) return 0;
  return Math.max((value / total) * 100, 6);
}

export type DashboardEvent = {
  id: string;
  time: string;
  label: string;
  subtitle: string;
  snapshot_url: string | null;
  snapshotLabel: string;
  status: "IN" | "OUT";
  statusTone: BadgeTone;
  statusIcon?: React.ElementType;
  statusLabel: string;
  tone: "green" | "blue" | "gray" | "amber";
  icon: React.ElementType;
};

export function getDashboardEvents(events: AccessEvent[], vehicles: Vehicle[], people: Person[]): DashboardEvent[] {
  const peopleById = new Map(people.map((person) => [person.id, person]));
  const vehiclesByRegistration = new Map(vehicles.map((vehicle) => [vehicle.registration_number.toUpperCase(), vehicle]));

  return events.slice(0, 5).map((event) => {
    const vehicle = vehiclesByRegistration.get(event.registration_number.toUpperCase());
    const owner = vehicle?.person_id ? peopleById.get(vehicle.person_id) : undefined;
    const ownerFirstName = owner?.first_name || vehicle?.owner?.split(" ")[0] || "";
    const visitorName = visitorEventDisplayName(event);
    const isDenied = event.decision === "denied" || event.direction === "denied";

    return {
      id: event.id,
      time: formatTime(event.occurred_at),
      label: visitorName || ownerFirstName || "Unknown",
      subtitle: `${event.registration_number}  •  ${event.visitor_pass_id ? "Visitor Pass" : "LPR"}`,
      snapshot_url: event.snapshot_url,
      snapshotLabel: `Snapshot for ${visitorName || ownerFirstName || event.registration_number}`,
      status: event.direction === "exit" ? "OUT" : "IN",
      statusTone: isDenied ? "amber" : event.direction === "entry" ? "green" : "gray",
      statusIcon: isDenied ? Lock : undefined,
      statusLabel: isDenied ? "Denied" : event.direction === "exit" ? "Out" : "In",
      tone: isDenied ? "amber" : event.direction === "entry" ? "green" : "blue",
      icon: event.direction === "exit" ? LogOut : isDenied ? AlertTriangle : Car
    };
  });
}

export type DashboardAnomaly = {
  id: string;
  alertId: string;
  title: string;
  detail: string;
  time: string;
  severity: AlertSeverity;
};

export function getDashboardAnomalies(anomalies: Anomaly[]): DashboardAnomaly[] {
  return anomalies.slice(0, 4).map((item) => ({
    id: item.id,
    alertId: item.grouped ? item.alert_ids[0] : item.id,
    title: titleCase(item.type),
    detail: item.message,
    time: formatTime(item.last_seen_at || item.created_at),
    severity: item.severity
  }));
}

export function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).format(new Date(value));
}

export function formatLongDate(value: Date) {
  const date = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric"
  }).format(value);
  const time = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true
  }).format(value);
  return `${date} • ${time}`;
}

export function greetingForDate(value: Date) {
  const hour = value.getHours();
  if (hour < 12) return "Good Morning";
  if (hour < 17) return "Good Afternoon";
  if (hour < 22) return "Good Evening";
  return "Good Night";
}

export function isToday(value: string, now = new Date()) {
  const date = new Date(value);
  return (
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  );
}
