import { ArrowRight, Car, CheckCircle2, ClipboardPaste, Clock3, Trash2, UserPlus } from "lucide-react";
import React from "react";

import { formatDate, isRecord, levelTone, matches, numberPayload, stringPayload, titleCase } from "../../lib/format";
import type { RealtimeMessage } from "../../api/types";
import type { BadgeTone } from "../../ui/primitives";



import type { VisitorPassStatus, VisitorPassType, VisitorPass, VisitorPassLogEntry, VisitorPassTone } from "./types";
import { visitorPassTypes } from "./PassEditor";

export const visitorPassStatuses: VisitorPassStatus[] = ["active", "scheduled", "used", "expired", "cancelled"];

export const defaultVisitorPassFilters = new Set<VisitorPassStatus>(["active", "scheduled"]);

export function visitorPassMatches(visitorPass: VisitorPass, query: string) {
  return (
    matches(visitorPass.visitor_name, query) ||
    matches(visitorPass.number_plate ?? "", query) ||
    matches(visitorPass.visitor_phone ?? "", query) ||
    matches(visitorPass.pass_type, query) ||
    matches(visitorPass.vehicle_make ?? "", query) ||
    matches(visitorPass.vehicle_colour ?? "", query) ||
    matches(visitorPass.status, query)
  );
}

export function visitorPassMatchesStatus(visitorPass: VisitorPass, filters: Set<VisitorPassStatus>) {
  return !filters.size || filters.size === visitorPassStatuses.length || filters.has(visitorPass.status);
}

export function isVisitorPassRealtimeEvent(event: RealtimeMessage) {
  return event.type.startsWith("visitor_pass.");
}

function visitorPassIdFromRealtime(event: RealtimeMessage) {
  const candidate = event.payload.visitor_pass;
  return isRecord(candidate) ? stringPayload(candidate.id) : "";
}

export function isVisitorPassAuditLogEvent(event: RealtimeMessage, visitorPassId: string) {
  if (event.type !== "audit.log.created") return false;
  const log = isRecord(event.payload.log) ? event.payload.log : event.payload;
  const targetEntity = stringPayload(log.target_entity).toLowerCase();
  const targetId = stringPayload(log.target_id);
  const action = stringPayload(log.action);
  return targetId === visitorPassId || (!targetId && targetEntity === "visitorpass" && action.startsWith("visitor_pass."));
}

export function visitorPassFromRealtime(event: RealtimeMessage): VisitorPass | null {
  const candidate = event.payload.visitor_pass;
  if (!isRecord(candidate)) return null;
  const status = stringPayload(candidate.status) as VisitorPassStatus;
  if (!visitorPassStatuses.includes(status)) return null;
  const id = stringPayload(candidate.id);
  const visitorName = stringPayload(candidate.visitor_name);
  const expectedTime = stringPayload(candidate.expected_time);
  if (!id || !visitorName || !expectedTime) return null;
  return {
    id,
    visitor_name: visitorName,
    pass_type: visitorPassTypes.includes(stringPayload(candidate.pass_type) as VisitorPassType) ? stringPayload(candidate.pass_type) as VisitorPassType : "one-time",
    visitor_phone: stringPayload(candidate.visitor_phone) || null,
    expected_time: expectedTime,
    window_minutes: numberPayload(candidate.window_minutes) || 30,
    valid_from: stringPayload(candidate.valid_from) || null,
    valid_until: stringPayload(candidate.valid_until) || null,
    window_start: stringPayload(candidate.window_start),
    window_end: stringPayload(candidate.window_end),
    status,
    creation_source: stringPayload(candidate.creation_source) || "unknown",
    source_reference: stringPayload(candidate.source_reference) || null,
    created_by_user_id: stringPayload(candidate.created_by_user_id) || null,
    created_by: stringPayload(candidate.created_by) || null,
    arrival_time: stringPayload(candidate.arrival_time) || null,
    departure_time: stringPayload(candidate.departure_time) || null,
    number_plate: stringPayload(candidate.number_plate) || null,
    vehicle_make: stringPayload(candidate.vehicle_make) || null,
    vehicle_colour: stringPayload(candidate.vehicle_colour) || null,
    duration_on_site_seconds: typeof candidate.duration_on_site_seconds === "number" ? candidate.duration_on_site_seconds : null,
    duration_human: stringPayload(candidate.duration_human) || null,
    arrival_event_id: stringPayload(candidate.arrival_event_id) || null,
    departure_event_id: stringPayload(candidate.departure_event_id) || null,
    telemetry_trace_id: stringPayload(candidate.telemetry_trace_id) || null,
    created_at: stringPayload(candidate.created_at),
    updated_at: stringPayload(candidate.updated_at)
  };
}

export function visitorPassLogsEqual(left: VisitorPassLogEntry[], right: VisitorPassLogEntry[]) {
  if (left.length !== right.length) return false;
  return left.every((log, index) => {
    const other = right[index];
    return Boolean(other) &&
      log.id === other.id &&
      log.timestamp === other.timestamp &&
      log.action === other.action &&
      JSON.stringify(log.diff) === JSON.stringify(other.diff) &&
      JSON.stringify(log.metadata) === JSON.stringify(other.metadata);
  });
}

export function visitorPassBaseStatusTone(status: VisitorPassStatus): VisitorPassTone {
  if (status === "active" || status === "used") return "green";
  if (status === "scheduled") return "blue";
  if (status === "cancelled") return "red";
  return "gray";
}

export function visitorPassStatusPillTone(visitorPass: VisitorPass): VisitorPassTone {
  return visitorPassBaseStatusTone(visitorPass.status);
}

export function visitorPassWindowLabel(visitorPass: VisitorPass) {
  if (visitorPass.pass_type === "duration") return "Duration";
  return visitorPass.creation_source === "icloud_calendar" ? "Calendar Sync" : `+/- ${visitorPass.window_minutes}m`;
}

export function visitorPassSourceLabel(source: string) {
  if (source === "icloud_calendar") return "iCloud Calendar";
  return titleCase(source);
}

export function visitorPassInitials(name: string) {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || "")
    .join("");
}

export function visitorPassPassDurationLabel(visitorPass: VisitorPass) {
  const start = new Date(visitorPass.window_start).getTime();
  const end = new Date(visitorPass.window_end).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return formatDurationSeconds(Math.round((end - start) / 1000));
}

export function visitorPassVisitDurationLabel(visitorPass: VisitorPass) {
  if (visitorPass.duration_human) return visitorPass.duration_human;
  if (visitorPass.duration_on_site_seconds !== null) return formatDurationSeconds(visitorPass.duration_on_site_seconds);
  if (visitorPass.arrival_time && visitorPass.departure_time) {
    const arrival = new Date(visitorPass.arrival_time).getTime();
    const departure = new Date(visitorPass.departure_time).getTime();
    if (Number.isFinite(arrival) && Number.isFinite(departure) && departure >= arrival) {
      return formatDurationSeconds(Math.round((departure - arrival) / 1000));
    }
  }
  if (visitorPass.arrival_time && !visitorPass.departure_time) {
    const arrival = new Date(visitorPass.arrival_time).getTime();
    if (Number.isFinite(arrival)) {
      const elapsed = Math.max(0, Math.round((Date.now() - arrival) / 1000));
      return `On site for ${formatDurationSeconds(elapsed)}`;
    }
  }
  return null;
}

function formatDurationSeconds(seconds: number) {
  const normalized = Math.max(0, Math.round(seconds));
  const days = Math.floor(normalized / 86400);
  const hours = Math.floor((normalized % 86400) / 3600);
  const minutes = Math.floor((normalized % 3600) / 60);
  if (days && hours) return `${days}d ${hours}h`;
  if (days) return `${days}d`;
  if (hours && minutes) return `${hours}h ${minutes}m`;
  if (hours) return `${hours}h`;
  if (minutes) return `${minutes}m`;
  return "0m";
}

export function visitorPassVehicleSummary(visitorPass: VisitorPass) {
  const vehicle = [visitorPass.vehicle_colour, visitorPass.vehicle_make].filter(Boolean).join(" ");
  return [vehicle, visitorPass.number_plate].filter(Boolean).join(" - ");
}

export function visitorPassLogDetails(log: VisitorPassLogEntry, visitorPass: VisitorPass): {
  title: string;
  description: string;
  tone: BadgeTone;
  fields: Array<{ label: string; value: string }>;
} {
  const actor = visitorPassLogActor(log);
  const oldValue = isRecord(log.diff.old) ? log.diff.old : {};
  const newValue = isRecord(log.diff.new) ? log.diff.new : {};
  const fields = visitorPassLogChangedFields(oldValue, newValue);

  if (log.action === "visitor_pass.create") {
    return {
      title: "Pass Created",
      description: `${actor} created the Visitor Pass for ${visitorPass.visitor_name}.`,
      tone: "green",
      fields,
    };
  }
  if (log.action === "visitor_pass.update") {
    const changedWindow = fields.some((field) => ["Expected Time", "Window", "Valid From", "Valid Until"].includes(field.label));
    return {
      title: changedWindow ? "Time Window Updated" : "Pass Updated",
      description: `${actor} updated ${visitorPass.visitor_name}'s Visitor Pass.`,
      tone: changedWindow ? "blue" : "gray",
      fields,
    };
  }
  if (log.action === "visitor_pass.cancel") {
    return {
      title: "Pass Cancelled",
      description: `${actor} cancelled the Visitor Pass.`,
      tone: "red",
      fields,
    };
  }
  if (log.action === "visitor_pass.delete") {
    return {
      title: "Pass Deleted",
      description: `${actor} deleted the Visitor Pass.`,
      tone: "red",
      fields,
    };
  }
  if (log.action === "visitor_pass.vehicle_plate_update") {
    return {
      title: "Registration Updated",
      description: `${actor} updated the visitor registration.`,
      tone: "blue",
      fields,
    };
  }
  if (log.action === "visitor_pass.claim" || log.action === "visitor_pass.arrival_linked") {
    return {
      title: "Arrival Linked",
      description: "IACS matched the arriving vehicle to this Visitor Pass.",
      tone: "green",
      fields,
    };
  }
  if (log.action === "visitor_pass.departure_linked") {
    return {
      title: "Departure Recorded",
      description: "IACS recorded the visitor leaving site.",
      tone: "purple",
      fields,
    };
  }
  if (log.action === "visitor_pass.status_refresh") {
    return {
      title: "Status Changed",
      description: "IACS refreshed the Visitor Pass lifecycle status.",
      tone: "gray",
      fields,
    };
  }
  return {
    title: titleCase(log.action.replace(/\./g, " ")),
    description: `${actor} changed this Visitor Pass.`,
    tone: levelTone(log.level),
    fields,
  };
}

export function visitorPassLogIcon(action: string): React.ElementType {
  if (action === "visitor_pass.create") return UserPlus;
  if (action === "visitor_pass.vehicle_plate_update") return Car;
  if (action === "visitor_pass.update") return Clock3;
  if (action === "visitor_pass.cancel" || action === "visitor_pass.delete") return Trash2;
  if (action.includes("arrival") || action === "visitor_pass.claim") return CheckCircle2;
  if (action.includes("departure")) return ArrowRight;
  return ClipboardPaste;
}

function visitorPassLogActor(log: VisitorPassLogEntry) {
  const actor = log.actor_user_label || log.actor || "IACS";
  if (log.actor === "System") return "IACS";
  if (log.actor.toLowerCase().includes("icloud")) return "iCloud Calendar Sync";
  return actor;
}

function visitorPassLogChangedFields(oldValue: Record<string, unknown>, newValue: Record<string, unknown>) {
  const labels: Record<string, string> = {
    expected_time: "Expected Time",
    window_minutes: "Window",
    valid_from: "Valid From",
    valid_until: "Valid Until",
    status: "Status",
    number_plate: "Registration",
    arrival_time: "Arrival",
    departure_time: "Departure",
    duration_on_site_seconds: "Visit Duration",
  };
  return Object.entries(labels).flatMap(([key, label]) => {
    if (!(key in oldValue) && !(key in newValue)) return [];
    const before = visitorPassLogFieldValue(key, oldValue[key]);
    const after = visitorPassLogFieldValue(key, newValue[key]);
    if (!before && !after) return [];
    return [{ label, value: `${before || "unset"} -> ${after || "unset"}` }];
  });
}

function visitorPassLogFieldValue(key: string, value: unknown) {
  const text = stringPayload(value);
  if (text && ["expected_time", "valid_from", "valid_until", "arrival_time", "departure_time"].includes(key)) {
    return formatDate(text);
  }
  if (key === "window_minutes" && value !== null && value !== undefined) return `+/- ${String(value)}m`;
  if (key === "duration_on_site_seconds" && typeof value === "number") return formatDurationSeconds(value);
  if (key === "status") return titleCase(text);
  return text;
}
