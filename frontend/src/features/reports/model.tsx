import { AlertTriangle, LogIn, LogOut } from "lucide-react";
import type { Person } from "../../api/types";
import type { ReportSnapshotEvent } from "../../api/reports";
import type { VisitorPass } from "../passes/types";
import { initials, titleCase } from "../../lib/format";

export type QuickRange = "24h" | "3d" | "7d" | "14d" | "custom";
export type ReportOptions = { includeDenied: boolean; includeSnapshots: boolean; includeConfidence: boolean };

export type ReportSearchResult =
  | { type: "report"; reportId: string }
  | { type: "person"; person: Person }
  | { type: "visitor_pass"; visitorPass: VisitorPass };

export const quickRanges: Array<{ value: QuickRange; label: string; hours: number }> = [
  { value: "24h", label: "Last 24hrs", hours: 24 },
  { value: "3d", label: "Last 3 Days", hours: 72 },
  { value: "7d", label: "Last 7 Days", hours: 168 },
  { value: "14d", label: "Last 14 Days", hours: 336 }
];

export const defaultOptions: ReportOptions = {
  includeDenied: false,
  includeSnapshots: true,
  includeConfidence: true
};

// Civil calendar values use UTC Date fields only as a calendar carrier. They are
// never interpreted as instants; the report API resolves site-zone input and DST.
export function toDateTimeInputValue(date: Date) {
  return date.toISOString().slice(0, 16);
}
export function parseDateTimeInput(value: string) {
  return new Date(`${value || "2000-01-01T00:00"}Z`);
}
export function siteCivilInput(value: string, timezone: string) {
  if (!value || !timezone) return "";
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(value)) return value.slice(0, 16);
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  }).formatToParts(new Date(value));
  const part = (key: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === key)!.value;
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}
export function formatSiteDate(value: string, timezone: string) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone, year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit"
  }).format(new Date(value));
}

export function reportInitials(person: Pick<Person, "display_name" | "first_name" | "last_name">) {
  return initials(person.display_name || `${person.first_name} ${person.last_name}`);
}

export function eventIcon(event: Pick<ReportSnapshotEvent, "decision" | "direction">) {
  if (event.decision === "denied") return AlertTriangle;
  return event.direction === "entry" ? LogIn : LogOut;
}

export function vehicleLabel(person: { vehicles: Array<{ registration_number: string }> } | null) {
  if (!person) return "No person selected";
  if (!person.vehicles.length) return "No registered vehicles";
  return person.vehicles.map((vehicle) => vehicle.registration_number).join(", ");
}

export function personMetaLabel(person: { group?: string | null; category?: string | null } | null) {
  if (!person) return "Choose a person to preview";
  const parts = [person.group, titleCase(person.category)].filter((part): part is string => Boolean(part));
  return Array.from(new Set(parts)).join(" · ") || "No group assigned";
}

export function reportVehicleTitle(vehicle: Pick<Person["vehicles"][number], "make" | "model" | "description"> & { title?: string }) {
  return vehicle.title || [vehicle.make, vehicle.model].filter(Boolean).join(" ") || vehicle.description || "Vehicle";
}

export function visitorPassMetaLabel(visitorPass: VisitorPass) {
  return `Visitor Pass · ${titleCase(visitorPass.status)} · ${titleCase(visitorPass.pass_type)}`;
}

export function visitorPassVehicleLabel(visitorPass: VisitorPass) {
  return visitorPass.number_plate || "No plate assigned";
}

export function reportComplianceDate(value?: string | null) {
  if (!value) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const date = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric"
  }).format(date);
}

export function reportComplianceLabel(expiry?: string | null) {
  return reportComplianceDate(expiry) || "No data";
}

export function reportComplianceTone(status?: string | null) {
  const normalized = (status ?? "").trim().toLowerCase();
  if (!normalized) return "muted";
  if (normalized.includes("untaxed") || normalized.includes("expired") || normalized.includes("invalid") || normalized.includes("fail")) return "red";
  if (normalized === "valid" || normalized === "taxed" || normalized === "sorn" || normalized.includes("not required")) return "green";
  return "muted";
}
