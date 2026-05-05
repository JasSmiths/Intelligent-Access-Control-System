import React from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  BarChart3,
  CalendarDays,
  Camera,
  Car,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  HelpCircle,
  LogIn,
  LogOut,
  Search,
  ShieldCheck,
  UserRound
} from "lucide-react";

import {
  AccessEvent,
  api,
  Badge,
  formatDate,
  initials,
  matches,
  Person,
  Presence,
  TooltipPositionState,
  titleCase
} from "../shared";
import type { VisitorPass } from "./PassesView";

type QuickRange = "24h" | "3d" | "7d" | "14d" | "custom";

type ReportOptions = {
  includeDenied: boolean;
  includeSnapshots: boolean;
  includeConfidence: boolean;
};

type ReportDurationInfo = {
  label: string;
  tone?: "muted" | "new";
  tooltip?: string;
  tooltipDetail?: string;
};

type ReportSnapshotVehicle = Person["vehicles"][number] & {
  title?: string;
  mot_label?: string;
  tax_label?: string;
  mot_tone?: "green" | "red" | "muted";
  tax_tone?: "green" | "red" | "muted";
};

type ReportSnapshotPerson = Omit<Person, "group_id" | "schedule_id" | "schedule" | "is_active" | "notes" | "garage_door_entity_ids" | "home_assistant_mobile_app_notify_service" | "vehicles"> & {
  vehicles: ReportSnapshotVehicle[];
};

type ReportSnapshotEvent = AccessEvent & {
  confidence_percent?: number;
  detail?: string;
  duration?: ReportDurationInfo;
  occurred_label?: string;
  source_label?: string;
  tone?: "green" | "blue" | "red";
  type_label?: string;
};

type ReportSnapshotTimelineEvent = {
  id: string;
  registration_number: string;
  direction: AccessEvent["direction"];
  decision: AccessEvent["decision"];
  occurred_at: string;
  label: string;
  tone: "green" | "blue" | "red";
  progress: number;
};

type ReportSnapshot = {
  report_id: string;
  subject_type?: "person" | "visitor_pass";
  generated_at: string;
  generated_label: string;
  person: ReportSnapshotPerson;
  period: {
    start: string;
    end: string;
    label: string;
    start_label: string;
    end_label: string;
    duration_label: string;
    timezone: string;
  };
  presence: {
    state: Presence["state"];
    last_changed_at: string | null;
  };
  options: {
    include_denied: boolean;
    include_snapshots: boolean;
    include_confidence: boolean;
  };
  summary: {
    arrivals: number;
    departures: number;
    denied: number;
    total: number;
    first_event: string;
    last_event: string;
  };
  events: ReportSnapshotEvent[];
  timeline: {
    all: ReportSnapshotTimelineEvent[];
    selected: ReportSnapshotTimelineEvent[];
  };
};

type ReportExportResponse = {
  report_id: string;
  created_at: string | null;
  download_url: string;
  pdf_bytes: number;
  report: ReportSnapshot;
};

type ReportSearchResult =
  | { type: "report"; reportId: string }
  | { type: "person"; person: Person }
  | { type: "visitor_pass"; visitorPass: VisitorPass };

const quickRanges: Array<{ value: QuickRange; label: string; hours: number }> = [
  { value: "24h", label: "Last 24hrs", hours: 24 },
  { value: "3d", label: "Last 3 Days", hours: 72 },
  { value: "7d", label: "Last 7 Days", hours: 168 },
  { value: "14d", label: "Last 14 Days", hours: 336 }
];

const defaultOptions: ReportOptions = {
  includeDenied: false,
  includeSnapshots: true,
  includeConfidence: true
};

function normalizePlate(value: string) {
  return value.replace(/[^a-z0-9]/gi, "").toUpperCase();
}

function toDateTimeInputValue(date: Date) {
  const offsetDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return offsetDate.toISOString().slice(0, 16);
}

function parseDateTimeInput(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function formatDateRange(start: Date, end: Date) {
  return `${formatDate(start.toISOString())} to ${formatDate(end.toISOString())}`;
}

function formatDateOnly(date: Date) {
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric"
  }).format(date);
}

function reportInitials(person: Pick<Person, "display_name" | "first_name" | "last_name">) {
  return initials(person.display_name || `${person.first_name} ${person.last_name}`);
}

function eventTone(event: AccessEvent) {
  if (event.decision === "denied") return "red";
  return event.direction === "entry" ? "green" : "blue";
}

function eventIcon(event: AccessEvent) {
  if (event.decision === "denied") return AlertTriangle;
  return event.direction === "entry" ? LogIn : LogOut;
}

function eventLabel(event: AccessEvent) {
  if (event.decision === "denied") return "Denied";
  return event.direction === "entry" ? "Arrival" : "Departure";
}

function directionSummary(events: AccessEvent[]) {
  return {
    arrivals: events.filter((event) => event.decision === "granted" && event.direction === "entry").length,
    departures: events.filter((event) => event.decision === "granted" && event.direction === "exit").length,
    denied: events.filter((event) => event.decision === "denied").length
  };
}

function lastMovement(events: AccessEvent[]) {
  const latest = [...events].sort((a, b) => new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime())[0];
  return latest ? `${eventLabel(latest)} ${formatDate(latest.occurred_at)}` : "No movement in window";
}

function firstMovement(events: AccessEvent[]) {
  return events[0] ? formatDate(events[0].occurred_at) : "None";
}

function vehicleLabel(person: { vehicles: Array<{ registration_number: string }> } | null) {
  if (!person) return "No person selected";
  if (!person.vehicles.length) return "No registered vehicles";
  return person.vehicles.map((vehicle) => vehicle.registration_number).join(", ");
}

function personMetaLabel(person: { group?: string | null; category?: string | null } | null) {
  if (!person) return "Choose a person to preview";
  const parts = [person.group, titleCase(person.category)].filter((part): part is string => Boolean(part));
  return Array.from(new Set(parts)).join(" · ") || "No group assigned";
}

function reportDurationLabel(start: Date, end: Date) {
  const milliseconds = Math.max(0, end.getTime() - start.getTime());
  const hours = Math.max(1, Math.ceil(milliseconds / (60 * 60 * 1000)));
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"}`;
  const days = Math.ceil(hours / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

function reportVehicleTitle(vehicle: Pick<Person["vehicles"][number], "make" | "model" | "description"> & { title?: string }) {
  return vehicle.title || [vehicle.make, vehicle.model].filter(Boolean).join(" ") || vehicle.description || "Vehicle";
}

function visitorPassMetaLabel(visitorPass: VisitorPass) {
  return `Visitor Pass · ${titleCase(visitorPass.status)} · ${titleCase(visitorPass.pass_type)}`;
}

function visitorPassVehicleLabel(visitorPass: VisitorPass) {
  return visitorPass.number_plate || "No plate assigned";
}

function visitorPassToReportPerson(visitorPass: VisitorPass): ReportSnapshotPerson {
  const plate = visitorPass.number_plate?.trim() || "";
  return {
    id: visitorPass.id,
    first_name: visitorPass.visitor_name,
    last_name: "",
    display_name: visitorPass.visitor_name,
    pronouns: null,
    profile_photo_data_url: null,
    group: "Visitor Pass",
    category: visitorPass.pass_type,
    vehicles: plate
      ? [{
          id: `visitor-pass-${visitorPass.id}`,
          registration_number: plate,
          vehicle_photo_data_url: null,
          description: "Visitor Pass Vehicle",
          make: visitorPass.vehicle_make,
          model: null,
          color: visitorPass.vehicle_colour,
          fuel_type: null,
          title: visitorPass.vehicle_make || "Visitor Vehicle",
          mot_label: "No data",
          tax_label: "No data",
          mot_tone: "muted",
          tax_tone: "muted"
        }]
      : []
  };
}

function visitorPassPresence(visitorPass: VisitorPass): Pick<Presence, "state" | "last_changed_at"> {
  if (visitorPass.departure_time) return { state: "exited", last_changed_at: visitorPass.departure_time };
  if (visitorPass.arrival_time) return { state: "present", last_changed_at: visitorPass.arrival_time };
  return { state: "unknown", last_changed_at: null };
}

function reportComplianceDate(value?: string | null) {
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

function reportComplianceLabel(expiry?: string | null) {
  return reportComplianceDate(expiry) || "No data";
}

function reportComplianceTone(status?: string | null) {
  const normalized = (status ?? "").trim().toLowerCase();
  if (!normalized) return "muted";
  if (normalized.includes("untaxed") || normalized.includes("expired") || normalized.includes("invalid") || normalized.includes("fail")) return "red";
  if (normalized === "valid" || normalized === "taxed" || normalized === "sorn" || normalized.includes("not required")) return "green";
  return "muted";
}

function formatDurationReferenceDate(value: string) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    month: "2-digit",
    year: "numeric"
  }).formatToParts(new Date(value));
  const getPart = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${getPart("day")}/${getPart("month")}/${getPart("year")} - ${getPart("hour")}:${getPart("minute")}`;
}

function pluralUnit(value: number, singular: string, plural = `${singular}s`) {
  return `${value} ${value === 1 ? singular : plural}`;
}

function formatVerboseDuration(milliseconds: number) {
  const totalDays = Math.max(0, Math.floor(milliseconds / (24 * 60 * 60 * 1000)));
  const years = Math.floor(totalDays / 365);
  const months = Math.floor((totalDays % 365) / 30);
  const days = (totalDays % 365) % 30;
  const parts = [
    years ? pluralUnit(years, "Year") : null,
    months ? pluralUnit(months, "Month") : null,
    days ? pluralUnit(days, "Day") : null
  ].filter(Boolean);
  return parts.join(", ") || "Less than 1 Day";
}

function formatTableDuration(start: string, end: string, detail: string): ReportDurationInfo {
  const milliseconds = Math.max(0, new Date(end).getTime() - new Date(start).getTime());
  const totalMinutes = Math.max(0, Math.floor(milliseconds / (60 * 1000)));
  const totalHours = Math.floor(totalMinutes / 60);
  const totalDays = Math.floor(totalHours / 24);
  const minutes = totalMinutes % 60;
  const hours = totalHours % 24;

  if (totalHours < 24) {
    return { label: totalHours ? `${totalHours}hr${totalHours === 1 ? "" : "s"} ${minutes}m` : `${minutes}m` };
  }

  if (totalDays < 14) {
    return { label: `${pluralUnit(totalDays, "Day")}, ${hours}hr${hours === 1 ? "" : "s"} ${minutes}m` };
  }

  return {
    label: formatDurationReferenceDate(start),
    tooltip: formatVerboseDuration(milliseconds),
    tooltipDetail: detail
  };
}

function isMovementEvent(event: AccessEvent) {
  return event.decision === "granted" && (event.direction === "entry" || event.direction === "exit");
}

function dayRhythmProgress(event: AccessEvent) {
  const occurredAt = new Date(event.occurred_at);
  const minutes = occurredAt.getHours() * 60 + occurredAt.getMinutes() + occurredAt.getSeconds() / 60;
  return Math.min(99.6, Math.max(0.4, (minutes / (24 * 60)) * 100));
}

function sourceLabel(source: string) {
  const trimmed = source.trim();
  const normalized = trimmed.toLowerCase();
  if (!normalized) return "Gate LPR";
  if (normalized === "ubiquiti" || normalized.includes("ubiquiti") || normalized.includes("unifi")) return "Gate LPR";
  if (normalized.includes("top gate") || normalized.includes("gate lpr")) return "Gate LPR";
  return trimmed;
}

const dayRhythmTicks = [
  { left: 0, label: "12 AM" },
  { left: 100 / 6, label: "4 AM" },
  { left: 200 / 6, label: "8 AM" },
  { left: 50, label: "12 PM" },
  { left: 400 / 6, label: "4 PM" },
  { left: 500 / 6, label: "8 PM" },
  { left: 100, label: "12 AM" }
];

const reportWeekdayLabels = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

function localDateKey(date: Date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0")
  ].join("-");
}

function reportCalendarDays(month: Date) {
  const firstDay = new Date(month.getFullYear(), month.getMonth(), 1);
  const mondayOffset = (firstDay.getDay() + 6) % 7;
  const start = new Date(firstDay);
  start.setDate(firstDay.getDate() - mondayOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const next = new Date(start);
    next.setDate(start.getDate() + index);
    return next;
  });
}

function formatReportPickerValue(date: Date) {
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    month: "short",
    year: "numeric"
  }).format(date);
}

function clampDateTimePart(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

function ReportsDateTimePicker({
  label,
  onChange,
  value
}: {
  label: string;
  onChange: (value: string) => void;
  value: string;
}) {
  const pickerId = React.useId();
  const [open, setOpen] = React.useState(false);
  const [popoverPosition, setPopoverPosition] = React.useState<TooltipPositionState | null>(null);
  const buttonRef = React.useRef<HTMLButtonElement | null>(null);
  const popoverRef = React.useRef<HTMLDivElement | null>(null);
  const selectedDate = React.useMemo(() => parseDateTimeInput(value), [value]);
  const [visibleMonth, setVisibleMonth] = React.useState(() => new Date(selectedDate.getFullYear(), selectedDate.getMonth(), 1));
  const selectedKey = localDateKey(selectedDate);
  const days = React.useMemo(() => reportCalendarDays(visibleMonth), [visibleMonth]);

  React.useEffect(() => {
    if (!open) setVisibleMonth(new Date(selectedDate.getFullYear(), selectedDate.getMonth(), 1));
  }, [open, selectedDate]);

  const positionPopover = React.useCallback(() => {
    const target = buttonRef.current;
    if (!target) return;
    const width = Math.min(348, window.innerWidth - 24);
    const height = 430;
    const rect = target.getBoundingClientRect();
    const gap = 10;
    const placement = rect.bottom + gap + height > window.innerHeight - 8 ? "top" : "bottom";
    const left = Math.max(12 + width / 2, Math.min(rect.left + rect.width / 2, window.innerWidth - width / 2 - 12));
    const top = placement === "bottom"
      ? Math.min(window.innerHeight - height - 8, rect.bottom + gap)
      : Math.max(8, rect.top - height - gap);
    setPopoverPosition({ left, placement, top });
  }, []);

  React.useEffect(() => {
    if (!open) return undefined;
    positionPopover();
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (buttonRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("resize", positionPopover);
    window.addEventListener("scroll", positionPopover, true);
    window.addEventListener("pointerdown", closeOnOutside);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("resize", positionPopover);
      window.removeEventListener("scroll", positionPopover, true);
      window.removeEventListener("pointerdown", closeOnOutside);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open, positionPopover]);

  const updateDate = (nextDate: Date) => {
    onChange(toDateTimeInputValue(nextDate));
  };

  const setDay = (day: Date) => {
    const next = new Date(day);
    next.setHours(selectedDate.getHours(), selectedDate.getMinutes(), 0, 0);
    updateDate(next);
  };

  const setTimePart = (part: "hour" | "minute", rawValue: string) => {
    const parsed = Number(rawValue);
    const next = new Date(selectedDate);
    if (part === "hour") next.setHours(clampDateTimePart(parsed, 0, 23));
    if (part === "minute") next.setMinutes(clampDateTimePart(parsed, 0, 59));
    next.setSeconds(0, 0);
    updateDate(next);
  };

  return (
    <div className="report-date-time-field">
      <span>{label}</span>
      <button
        aria-controls={open ? pickerId : undefined}
        aria-expanded={open}
        className="report-date-time-trigger"
        onClick={() => setOpen((current) => !current)}
        ref={buttonRef}
        type="button"
      >
        <CalendarDays size={15} />
        <strong>{formatReportPickerValue(selectedDate)}</strong>
        <Clock3 size={14} />
      </button>
      {open && popoverPosition ? createPortal(
        <div
          className={`report-date-time-popover ${popoverPosition.placement}`}
          id={pickerId}
          ref={popoverRef}
          role="dialog"
          style={{ left: popoverPosition.left, top: popoverPosition.top }}
        >
          <div className="report-date-time-popover-head">
            <button aria-label="Previous month" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() - 1, 1))} type="button">
              <ChevronLeft size={16} />
            </button>
            <strong>{visibleMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong>
            <button aria-label="Next month" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1))} type="button">
              <ChevronRight size={16} />
            </button>
          </div>
          <div className="report-date-time-calendar">
            {reportWeekdayLabels.map((day) => <span key={day}>{day}</span>)}
            {days.map((day) => {
              const key = localDateKey(day);
              return (
                <button
                  className={`${day.getMonth() === visibleMonth.getMonth() ? "" : "muted"} ${key === selectedKey ? "active" : ""}`}
                  key={key}
                  onClick={() => setDay(day)}
                  type="button"
                >
                  {day.getDate()}
                </button>
              );
            })}
          </div>
          <div className="report-date-time-footer">
            <div>
              <span>Time</span>
              <strong>{String(selectedDate.getHours()).padStart(2, "0")}:{String(selectedDate.getMinutes()).padStart(2, "0")}</strong>
            </div>
            <label>
              <span>Hour</span>
              <input max={23} min={0} onChange={(event) => setTimePart("hour", event.target.value)} type="number" value={String(selectedDate.getHours()).padStart(2, "0")} />
            </label>
            <label>
              <span>Min</span>
              <input max={59} min={0} onChange={(event) => setTimePart("minute", event.target.value)} type="number" value={String(selectedDate.getMinutes()).padStart(2, "0")} />
            </label>
          </div>
        </div>,
        document.body
      ) : null}
    </div>
  );
}

function ReportDurationCell({ duration }: { duration: ReportDurationInfo }) {
  const tooltipId = React.useId();
  const [tooltipPosition, setTooltipPosition] = React.useState<TooltipPositionState | null>(null);

  React.useEffect(() => {
    if (!tooltipPosition) return undefined;
    const hideTooltip = () => setTooltipPosition(null);
    window.addEventListener("resize", hideTooltip);
    window.addEventListener("scroll", hideTooltip, true);
    return () => {
      window.removeEventListener("resize", hideTooltip);
      window.removeEventListener("scroll", hideTooltip, true);
    };
  }, [tooltipPosition]);

  const showTooltip = (target: HTMLElement) => {
    if (!duration.tooltip) return;
    const tooltipWidth = Math.min(240, window.innerWidth - 24);
    const tooltipHeight = 72;
    const rect = target.getBoundingClientRect();
    const gap = 10;
    const placement = rect.bottom + gap + tooltipHeight > window.innerHeight - 8 ? "top" : "bottom";
    const left = Math.max(12 + tooltipWidth / 2, Math.min(rect.left + rect.width / 2, window.innerWidth - tooltipWidth / 2 - 12));
    const top = placement === "bottom"
      ? Math.min(window.innerHeight - tooltipHeight - 8, rect.bottom + gap)
      : Math.max(8, rect.top - tooltipHeight - gap);
    setTooltipPosition({ left, placement, top });
  };

  if (!duration.tooltip) {
    return <span className={`report-duration-value ${duration.tone ?? ""}`}>{duration.label}</span>;
  }

  return (
    <button
      aria-describedby={tooltipPosition ? tooltipId : undefined}
      className="report-duration-value interactive"
      onBlur={() => setTooltipPosition(null)}
      onFocus={(event) => showTooltip(event.currentTarget)}
      onKeyDown={(event) => {
        if (event.key === "Escape") setTooltipPosition(null);
      }}
      onMouseEnter={(event) => showTooltip(event.currentTarget)}
      onMouseLeave={() => setTooltipPosition(null)}
      type="button"
    >
      {duration.label}
      {tooltipPosition ? createPortal(
        <div
          className={`iacs-tooltip report-duration-tooltip ${tooltipPosition.placement}`}
          id={tooltipId}
          role="tooltip"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          <strong>{duration.tooltip}</strong>
          {duration.tooltipDetail ? <span>{duration.tooltipDetail}</span> : null}
        </div>,
        document.body
      ) : null}
    </button>
  );
}

function ReportSnapshotThumb({ event }: { event: AccessEvent }) {
  const tooltipId = React.useId();
  const [tooltipPosition, setTooltipPosition] = React.useState<TooltipPositionState | null>(null);

  React.useEffect(() => {
    if (!tooltipPosition) return undefined;
    const hideTooltip = () => setTooltipPosition(null);
    window.addEventListener("resize", hideTooltip);
    window.addEventListener("scroll", hideTooltip, true);
    return () => {
      window.removeEventListener("resize", hideTooltip);
      window.removeEventListener("scroll", hideTooltip, true);
    };
  }, [tooltipPosition]);

  const showTooltip = (target: HTMLElement) => {
    if (!event.snapshot_url) return;
    const tooltipWidth = Math.min(360, window.innerWidth - 24);
    const tooltipHeight = Math.round((tooltipWidth - 16) * 9 / 16) + 58;
    const rect = target.getBoundingClientRect();
    const gap = 10;
    const placement = rect.bottom + gap + tooltipHeight > window.innerHeight - 8 ? "top" : "bottom";
    const left = Math.max(12 + tooltipWidth / 2, Math.min(rect.left + rect.width / 2, window.innerWidth - tooltipWidth / 2 - 12));
    const top = placement === "bottom"
      ? Math.min(window.innerHeight - tooltipHeight - 8, rect.bottom + gap)
      : Math.max(8, rect.top - tooltipHeight - gap);
    setTooltipPosition({ left, placement, top });
  };

  if (!event.snapshot_url) {
    return (
      <span className="report-table-snapshot empty" aria-label="No snapshot available">
        <Camera size={14} />
      </span>
    );
  }

  return (
    <button
      aria-describedby={tooltipPosition ? tooltipId : undefined}
      aria-label={`Snapshot for ${event.registration_number}`}
      className="report-table-snapshot thumb"
      onBlur={() => setTooltipPosition(null)}
      onFocus={(mouseEvent) => showTooltip(mouseEvent.currentTarget)}
      onKeyDown={(keyboardEvent) => {
        if (keyboardEvent.key === "Escape") setTooltipPosition(null);
      }}
      onMouseEnter={(mouseEvent) => showTooltip(mouseEvent.currentTarget)}
      onMouseLeave={() => setTooltipPosition(null)}
      type="button"
    >
      <img alt="" loading="lazy" src={event.snapshot_url} />
      {tooltipPosition ? createPortal(
        <div
          className={`iacs-tooltip report-snapshot-tooltip ${tooltipPosition.placement}`}
          id={tooltipId}
          role="tooltip"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          <img alt="" loading="lazy" src={event.snapshot_url} />
          <strong>{event.registration_number}</strong>
          <span>{event.snapshot_captured_at ? `Captured ${formatDate(event.snapshot_captured_at)}` : `${eventLabel(event)} at ${formatDate(event.occurred_at)}`}</span>
        </div>,
        document.body
      ) : null}
    </button>
  );
}

export function ReportsView({
  events,
  people,
  presence
}: {
  events: AccessEvent[];
  people: Person[];
  presence: Presence[];
}) {
  const personSearchLabelId = React.useId();
  const personSearchListId = React.useId();
  const reportablePeople = React.useMemo(
    () => people.filter((person) => person.is_active).sort((a, b) => a.display_name.localeCompare(b.display_name)),
    [people]
  );
  const [selectedPersonId, setSelectedPersonId] = React.useState("");
  const [selectedVisitorPassId, setSelectedVisitorPassId] = React.useState("");
  const [personQuery, setPersonQuery] = React.useState("");
  const [isPersonSearchOpen, setIsPersonSearchOpen] = React.useState(false);
  const [highlightedPersonIndex, setHighlightedPersonIndex] = React.useState(0);
  const [range, setRange] = React.useState<QuickRange>("7d");
  const [endInput, setEndInput] = React.useState(() => toDateTimeInputValue(new Date()));
  const [startInput, setStartInput] = React.useState(() => toDateTimeInputValue(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)));
  const [options, setOptions] = React.useState<ReportOptions>(defaultOptions);
  const [reportSourceEvents, setReportSourceEvents] = React.useState(events);
  const [visitorPasses, setVisitorPasses] = React.useState<VisitorPass[]>([]);
  const [loadedReport, setLoadedReport] = React.useState<ReportExportResponse | null>(null);
  const [isExportingReport, setIsExportingReport] = React.useState(false);
  const [isLoadingReportId, setIsLoadingReportId] = React.useState(false);
  const [reportActionError, setReportActionError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (selectedPersonId && reportablePeople.some((person) => person.id === selectedPersonId)) return;
    if (selectedPersonId) {
      setSelectedPersonId("");
      setPersonQuery("");
    }
  }, [reportablePeople, selectedPersonId]);

  React.useEffect(() => {
    if (selectedVisitorPassId && visitorPasses.some((visitorPass) => visitorPass.id === selectedVisitorPassId)) return;
    if (selectedVisitorPassId) {
      setSelectedVisitorPassId("");
      setPersonQuery("");
    }
  }, [selectedVisitorPassId, visitorPasses]);

  React.useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.get<AccessEvent[]>("/api/v1/events?limit=250"),
      api.get<VisitorPass[]>("/api/v1/visitor-passes?limit=500")
    ])
      .then(([nextEvents, nextVisitorPasses]) => {
        if (!cancelled) {
          setReportSourceEvents(nextEvents);
          setVisitorPasses(nextVisitorPasses);
        }
      })
      .catch(() => {
        if (!cancelled) setReportSourceEvents(events);
      });
    return () => {
      cancelled = true;
    };
  }, [events]);

  const selectedPerson = reportablePeople.find((person) => person.id === selectedPersonId) ?? null;
  const selectedVisitorPass = visitorPasses.find((visitorPass) => visitorPass.id === selectedVisitorPassId) ?? null;
  const selectedPresence = selectedPerson
    ? presence.find((item) => item.person_id === selectedPerson.id) ?? null
    : null;
  const selectedPlates = React.useMemo(
    () => {
      if (selectedPerson) {
        return new Set(selectedPerson.vehicles.map((vehicle) => normalizePlate(vehicle.registration_number)));
      }
      return new Set(selectedVisitorPass?.number_plate ? [normalizePlate(selectedVisitorPass.number_plate)] : []);
    },
    [selectedPerson, selectedVisitorPass]
  );
  const selectedVisitorPassEventIds = React.useMemo(
    () => new Set([selectedVisitorPass?.arrival_event_id, selectedVisitorPass?.departure_event_id].filter((value): value is string => Boolean(value))),
    [selectedVisitorPass]
  );
  const selectedSubject = selectedPerson || selectedVisitorPass;
  const eventMatchesSelectedSubject = React.useCallback((event: AccessEvent) => {
    if (selectedPerson) return selectedPlates.has(normalizePlate(event.registration_number));
    if (selectedVisitorPass) {
      return (
        event.visitor_pass_id === selectedVisitorPass.id ||
        selectedVisitorPassEventIds.has(event.id) ||
        selectedPlates.has(normalizePlate(event.registration_number))
      );
    }
    return false;
  }, [selectedPerson, selectedPlates, selectedVisitorPass, selectedVisitorPassEventIds]);
  const reportableVisitorPasses = React.useMemo(
    () => [...visitorPasses].sort((a, b) => a.visitor_name.localeCompare(b.visitor_name)),
    [visitorPasses]
  );
  const filteredPeople = React.useMemo(
    () => reportablePeople.filter((person) =>
      matches(person.display_name, personQuery) ||
      person.vehicles.some((vehicle) => matches(vehicle.registration_number, personQuery))
    ),
    [personQuery, reportablePeople]
  );
  const filteredVisitorPasses = React.useMemo(
    () => reportableVisitorPasses.filter((visitorPass) =>
      matches(visitorPass.visitor_name, personQuery) ||
      matches(visitorPass.number_plate || "", personQuery) ||
      matches(visitorPass.vehicle_make || "", personQuery) ||
      matches(visitorPass.vehicle_colour || "", personQuery)
    ),
    [personQuery, reportableVisitorPasses]
  );
  const reportIdCandidate = React.useMemo(() => {
    const trimmed = personQuery.trim();
    return /^\d{4,12}$/.test(trimmed) ? trimmed : "";
  }, [personQuery]);
  const personSearchResults = React.useMemo<ReportSearchResult[]>(() => {
    const peopleResults: ReportSearchResult[] = (personQuery.trim() ? filteredPeople : reportablePeople)
      .slice(0, 5)
      .map((person) => ({ type: "person", person }));
    const visitorPassResults: ReportSearchResult[] = (personQuery.trim() ? filteredVisitorPasses : reportableVisitorPasses)
      .slice(0, Math.max(0, (reportIdCandidate ? 7 : 8) - peopleResults.length))
      .map((visitorPass) => ({ type: "visitor_pass", visitorPass }));
    const subjectResults = [...peopleResults, ...visitorPassResults].slice(0, reportIdCandidate ? 7 : 8);
    return reportIdCandidate
      ? [{ type: "report", reportId: reportIdCandidate }, ...subjectResults]
      : subjectResults;
  }, [filteredPeople, filteredVisitorPasses, personQuery, reportIdCandidate, reportablePeople, reportableVisitorPasses]);

  React.useEffect(() => {
    setHighlightedPersonIndex(0);
  }, [personQuery, personSearchResults.length]);

  const startDate = React.useMemo(() => parseDateTimeInput(startInput), [startInput]);
  const endDate = React.useMemo(() => parseDateTimeInput(endInput), [endInput]);
  const generatedAt = React.useMemo(() => formatDate(new Date().toISOString()), [selectedPersonId, selectedVisitorPassId, startInput, endInput, options]);
  const reportEvents = React.useMemo(() => {
    const startTime = startDate.getTime();
    const endTime = endDate.getTime();
    return reportSourceEvents
      .filter(eventMatchesSelectedSubject)
      .filter((event) => {
        const occurredAt = new Date(event.occurred_at).getTime();
        return occurredAt >= startTime && occurredAt <= endTime;
      })
      .filter((event) => options.includeDenied || event.decision !== "denied")
      .sort((a, b) => new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime());
  }, [endDate, eventMatchesSelectedSubject, options.includeDenied, reportSourceEvents, startDate]);
  const visibleEvents = React.useMemo(
    () => [...reportEvents]
      .sort((a, b) => new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime())
      .slice(0, 12),
    [reportEvents]
  );
  const summary = directionSummary(reportEvents);
  const durationByEventId = React.useMemo(() => {
    const selectedHistory = reportSourceEvents
      .filter(isMovementEvent)
      .filter(eventMatchesSelectedSubject)
      .sort((a, b) => new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime());
    const nextDurations = new Map<string, ReportDurationInfo>();

    reportEvents.forEach((event) => {
      if (!isMovementEvent(event)) {
        nextDurations.set(event.id, { label: "N/A", tone: "muted" });
        return;
      }

      const eventTime = new Date(event.occurred_at).getTime();
      const eventPlate = normalizePlate(event.registration_number);
      const selectedBefore = selectedHistory.filter((item) => new Date(item.occurred_at).getTime() < eventTime);

      if (event.direction === "entry") {
        const vehicleBefore = selectedBefore.filter((item) => normalizePlate(item.registration_number) === eventPlate);
        const previousArrival = [...vehicleBefore].reverse().find((item) => item.direction === "entry");
        const previousDeparture = [...vehicleBefore].reverse().find((item) => item.direction === "exit");

        if (!previousArrival) {
          nextDurations.set(event.id, { label: "New Arrival", tone: "new" });
          return;
        }

        if (previousDeparture && new Date(previousDeparture.occurred_at).getTime() > new Date(previousArrival.occurred_at).getTime()) {
          nextDurations.set(event.id, formatTableDuration(previousDeparture.occurred_at, event.occurred_at, "Time since this vehicle was last on site"));
          return;
        }

        nextDurations.set(event.id, { label: "No prior departure", tone: "muted" });
        return;
      }

      const previousArrival = [...selectedBefore].reverse().find((item) => item.direction === "entry");
      const previousDeparture = [...selectedBefore].reverse().find((item) => item.direction === "exit");

      if (!previousArrival) {
        nextDurations.set(event.id, { label: "No arrival found", tone: "muted" });
        return;
      }

      if (previousDeparture && new Date(previousDeparture.occurred_at).getTime() > new Date(previousArrival.occurred_at).getTime()) {
        nextDurations.set(event.id, { label: "No active visit", tone: "muted" });
        return;
      }

      nextDurations.set(event.id, formatTableDuration(previousArrival.occurred_at, event.occurred_at, "Time on site since last arrival"));
    });

    return nextDurations;
  }, [eventMatchesSelectedSubject, reportEvents, reportSourceEvents]);
  const allTimelineEvents = React.useMemo(() => {
    if (!selectedSubject) return [];
    const startTime = startDate.getTime();
    const endTime = endDate.getTime();
    return reportSourceEvents
      .filter(isMovementEvent)
      .filter((event) => {
        const occurredAt = new Date(event.occurred_at).getTime();
        return occurredAt >= startTime && occurredAt <= endTime;
      })
      .sort((a, b) => new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime());
  }, [endDate, reportSourceEvents, selectedSubject, startDate]);
  const selectedTimelineEvents = selectedSubject ? reportEvents.filter(isMovementEvent) : [];
  const activeReport = loadedReport?.report ?? null;
  const previewPerson = activeReport?.person ?? selectedPerson ?? (selectedVisitorPass ? visitorPassToReportPerson(selectedVisitorPass) : null);
  const previewPresence = activeReport?.presence
    ? { state: activeReport.presence.state, last_changed_at: activeReport.presence.last_changed_at }
    : selectedPerson ? selectedPresence : selectedVisitorPass ? visitorPassPresence(selectedVisitorPass) : null;
  const previewSummary = activeReport
    ? {
        arrivals: activeReport.summary.arrivals,
        departures: activeReport.summary.departures,
        denied: activeReport.summary.denied,
        total: activeReport.summary.total,
        firstEvent: activeReport.summary.first_event,
        lastEvent: activeReport.summary.last_event
      }
    : {
        arrivals: summary.arrivals,
        departures: summary.departures,
        denied: summary.denied,
        total: reportEvents.length,
        firstEvent: firstMovement(reportEvents),
        lastEvent: lastMovement(reportEvents)
      };
  const previewEvents: ReportSnapshotEvent[] = activeReport ? activeReport.events : visibleEvents;
  const previewEventCount = activeReport ? activeReport.events.length : reportEvents.length;
  const previewVehicles: ReportSnapshotVehicle[] = previewPerson?.vehicles ?? [];
  const previewAllTimelineEvents = activeReport ? activeReport.timeline.all : allTimelineEvents;
  const previewSelectedTimelineEvents = activeReport ? activeReport.timeline.selected : selectedTimelineEvents;
  const previewPeriodLabel = activeReport?.period.label ?? formatDateRange(startDate, endDate);
  const previewPeriodStartLabel = activeReport?.period.start_label ?? formatDateOnly(startDate);
  const previewPeriodEndLabel = activeReport?.period.end_label ?? formatDateOnly(endDate);
  const previewPeriodDurationLabel = activeReport?.period.duration_label ?? reportDurationLabel(startDate, endDate);
  const previewGeneratedLabel = activeReport?.generated_label ?? generatedAt;
  const previewOptions = activeReport
    ? {
        includeDenied: activeReport.options.include_denied,
        includeSnapshots: activeReport.options.include_snapshots,
        includeConfidence: activeReport.options.include_confidence
      }
    : options;
  const previewSubjectKind = activeReport?.subject_type === "visitor_pass" || selectedVisitorPass ? "Visitor Pass" : "Person";

  const selectPerson = React.useCallback((person: Person) => {
    setSelectedPersonId(person.id);
    setSelectedVisitorPassId("");
    setPersonQuery(person.display_name);
    setIsPersonSearchOpen(false);
    setHighlightedPersonIndex(0);
    setLoadedReport(null);
    setReportActionError(null);
  }, []);

  const selectVisitorPass = React.useCallback((visitorPass: VisitorPass) => {
    setSelectedPersonId("");
    setSelectedVisitorPassId(visitorPass.id);
    setPersonQuery(visitorPass.visitor_name);
    setIsPersonSearchOpen(false);
    setHighlightedPersonIndex(0);
    setLoadedReport(null);
    setReportActionError(null);
  }, []);

  const downloadReportPdf = React.useCallback((downloadUrl: string) => {
    const anchor = document.createElement("a");
    anchor.href = downloadUrl;
    anchor.download = "";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }, []);

  const loadReportById = React.useCallback(async (reportId: string) => {
    setIsLoadingReportId(true);
    setReportActionError(null);
    try {
      const response = await api.get<ReportExportResponse>(`/api/v1/reports/${encodeURIComponent(reportId)}`);
      const matchingPerson = response.report.subject_type === "visitor_pass"
        ? null
        : reportablePeople.find((person) => person.id === response.report.person.id);
      const matchingVisitorPass = response.report.subject_type === "visitor_pass"
        ? visitorPasses.find((visitorPass) => visitorPass.id === response.report.person.id)
        : null;
      setLoadedReport(response);
      setSelectedPersonId(matchingPerson?.id ?? "");
      setSelectedVisitorPassId(matchingVisitorPass?.id ?? "");
      setPersonQuery(`Report #${response.report_id} - ${response.report.person.display_name}`);
      setStartInput(toDateTimeInputValue(new Date(response.report.period.start)));
      setEndInput(toDateTimeInputValue(new Date(response.report.period.end)));
      setOptions({
        includeDenied: response.report.options.include_denied,
        includeSnapshots: response.report.options.include_snapshots,
        includeConfidence: response.report.options.include_confidence
      });
      setRange("custom");
      setIsPersonSearchOpen(false);
      setHighlightedPersonIndex(0);
    } catch (error) {
      setReportActionError(error instanceof Error ? error.message : "Report could not be loaded.");
    } finally {
      setIsLoadingReportId(false);
    }
  }, [reportablePeople, visitorPasses]);

  const exportCurrentReport = React.useCallback(async () => {
    if (!selectedPerson && !selectedVisitorPass) return;
    setIsExportingReport(true);
    setReportActionError(null);
    try {
      const response = await api.post<ReportExportResponse>("/api/v1/reports/person-movements/export", {
        person_id: selectedPerson?.id,
        visitor_pass_id: selectedVisitorPass?.id,
        period_start: startDate.toISOString(),
        period_end: endDate.toISOString(),
        include_denied: options.includeDenied,
        include_snapshots: options.includeSnapshots,
        include_confidence: options.includeConfidence
      });
      setLoadedReport(response);
      setPersonQuery(`Report #${response.report_id} - ${response.report.person.display_name}`);
      downloadReportPdf(response.download_url);
    } catch (error) {
      setReportActionError(error instanceof Error ? error.message : "Report could not be exported.");
    } finally {
      setIsExportingReport(false);
    }
  }, [downloadReportPdf, endDate, options.includeConfidence, options.includeDenied, options.includeSnapshots, selectedPerson, selectedVisitorPass, startDate]);

  const handleReportExportClick = () => {
    if (loadedReport) {
      downloadReportPdf(loadedReport.download_url);
      return;
    }
    void exportCurrentReport();
  };

  const clearLoadedReport = () => {
    if (loadedReport) setLoadedReport(null);
    if (reportActionError) setReportActionError(null);
  };

  const handlePersonSearchKeyDown = (keyboardEvent: React.KeyboardEvent<HTMLInputElement>) => {
    if (keyboardEvent.key === "ArrowDown") {
      keyboardEvent.preventDefault();
      setIsPersonSearchOpen(true);
      setHighlightedPersonIndex((current) => Math.min(current + 1, Math.max(0, personSearchResults.length - 1)));
      return;
    }

    if (keyboardEvent.key === "ArrowUp") {
      keyboardEvent.preventDefault();
      setIsPersonSearchOpen(true);
      setHighlightedPersonIndex((current) => Math.max(0, current - 1));
      return;
    }

    if (keyboardEvent.key === "Enter") {
      const result = personSearchResults[Math.min(highlightedPersonIndex, personSearchResults.length - 1)];
      if (!result) return;
      keyboardEvent.preventDefault();
      if (result.type === "report") {
        void loadReportById(result.reportId);
      } else if (result.type === "visitor_pass") {
        selectVisitorPass(result.visitorPass);
      } else {
        selectPerson(result.person);
      }
      return;
    }

    if (keyboardEvent.key === "Escape") {
      setIsPersonSearchOpen(false);
    }
  };

  const applyQuickRange = (nextRange: QuickRange) => {
    const option = quickRanges.find((item) => item.value === nextRange);
    if (!option) return;
    clearLoadedReport();
    const end = new Date();
    const start = new Date(end.getTime() - option.hours * 60 * 60 * 1000);
    setRange(nextRange);
    setEndInput(toDateTimeInputValue(end));
    setStartInput(toDateTimeInputValue(start));
  };

  const updateOption = (key: keyof ReportOptions) => {
    clearLoadedReport();
    setOptions((current) => ({ ...current, [key]: !current[key] }));
  };

  return (
    <section className="reports-page">
      <div className="reports-page-head">
        <div>
          <h1>Reports</h1>
          <p>Access Arrivals / Departures</p>
        </div>
        <button className="report-help-button" type="button">
          <HelpCircle size={16} /> How this report works
        </button>
      </div>

      <div className="report-builder-panel">
        <div className="report-builder-main">
          <div className="report-person-panel">
            <div
              className="report-field report-person-field"
              onBlur={(event) => {
                const nextTarget = event.relatedTarget;
                if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return;
                setIsPersonSearchOpen(false);
              }}
            >
              <span id={personSearchLabelId}>Subject</span>
              <div className={`report-person-search ${previewPerson ? "" : "needs-selection"}`} role="presentation">
                <Search size={15} />
                <input
                  aria-activedescendant={isPersonSearchOpen && personSearchResults[highlightedPersonIndex] ? `${personSearchListId}-option-${highlightedPersonIndex}` : undefined}
                  aria-autocomplete="list"
                  aria-controls={personSearchListId}
                  aria-expanded={isPersonSearchOpen}
                  aria-labelledby={personSearchLabelId}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    clearLoadedReport();
                    setPersonQuery(nextValue);
                    setIsPersonSearchOpen(true);
                    setHighlightedPersonIndex(0);
                    if (!nextValue.trim()) {
                      setSelectedPersonId("");
                      setSelectedVisitorPassId("");
                    }
                  }}
                  onFocus={() => setIsPersonSearchOpen(true)}
                  onKeyDown={handlePersonSearchKeyDown}
                  placeholder="Search people, visitor passes or plates"
                  role="combobox"
                  type="text"
                  value={personQuery}
                />
              </div>
              {isPersonSearchOpen ? (
                <div className="report-person-results" id={personSearchListId} role="listbox">
                  {personSearchResults.length ? personSearchResults.map((result, index) => (
                    result.type === "report" ? (
                      <button
                        aria-selected={index === highlightedPersonIndex}
                        className={`report-result-report ${index === highlightedPersonIndex ? "active" : ""}`}
                        id={`${personSearchListId}-option-${index}`}
                        key={`report-${result.reportId}`}
                        onClick={() => void loadReportById(result.reportId)}
                        onMouseDown={(mouseEvent) => mouseEvent.preventDefault()}
                        onMouseEnter={() => setHighlightedPersonIndex(index)}
                        role="option"
                        type="button"
                      >
                        <span>
                          <strong>{isLoadingReportId ? "Opening Report" : `Open Report #${result.reportId}`}</strong>
                          <small>Load an exported PDF report snapshot</small>
                        </span>
                        <em>Report ID</em>
                      </button>
                    ) : result.type === "person" ? (
                      <button
                        aria-selected={result.person.id === selectedPersonId}
                        className={`${index === highlightedPersonIndex ? "active" : ""} ${result.person.id === selectedPersonId ? "selected" : ""}`}
                        id={`${personSearchListId}-option-${index}`}
                        key={result.person.id}
                        onClick={() => selectPerson(result.person)}
                        onMouseDown={(mouseEvent) => mouseEvent.preventDefault()}
                        onMouseEnter={() => setHighlightedPersonIndex(index)}
                        role="option"
                        type="button"
                      >
                        <span>
                          <strong>{result.person.display_name}</strong>
                          <small>{personMetaLabel(result.person)}</small>
                        </span>
                        <em>{vehicleLabel(result.person)}</em>
                      </button>
                    ) : (
                      <button
                        aria-selected={result.visitorPass.id === selectedVisitorPassId}
                        className={`${index === highlightedPersonIndex ? "active" : ""} ${result.visitorPass.id === selectedVisitorPassId ? "selected" : ""}`}
                        id={`${personSearchListId}-option-${index}`}
                        key={result.visitorPass.id}
                        onClick={() => selectVisitorPass(result.visitorPass)}
                        onMouseDown={(mouseEvent) => mouseEvent.preventDefault()}
                        onMouseEnter={() => setHighlightedPersonIndex(index)}
                        role="option"
                        type="button"
                      >
                        <span>
                          <strong>{result.visitorPass.visitor_name}</strong>
                          <small>{visitorPassMetaLabel(result.visitorPass)}</small>
                        </span>
                        <em>{visitorPassVehicleLabel(result.visitorPass)}</em>
                      </button>
                    )
                  )) : (
                    <div className="report-person-result-empty" role="presentation">
                      No people, visitor passes, plates, or report IDs match
                    </div>
                  )}
                </div>
              ) : null}
            </div>
            <div className="report-person-card">
              <span className="report-avatar small">
                {previewPerson?.profile_photo_data_url
                  ? <img alt="" src={previewPerson.profile_photo_data_url} />
                  : previewPerson ? reportInitials(previewPerson) : <UserRound size={18} />}
              </span>
              <div>
                {previewPerson ? (
                  <>
                    <strong>{previewPerson.display_name}</strong>
                    <span>{loadedReport ? `Exported Report #${loadedReport.report_id}` : personMetaLabel(previewPerson)}</span>
                    <span>{vehicleLabel(previewPerson)}</span>
                  </>
                ) : (
                  <>
                    <strong>No Subject Selected</strong>
                    <span>Choose a Person or Visitor Pass to generate report</span>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="report-period-panel">
            <div className="report-field">
              <span>Quick Period</span>
              <div className="report-quick-ranges" aria-label="Quick report periods">
                {quickRanges.map((item) => (
                  <button
                    className={range === item.value ? "active" : ""}
                    key={item.value}
                    onClick={() => applyQuickRange(item.value)}
                    type="button"
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="report-date-grid">
              <ReportsDateTimePicker
                label="From"
                onChange={(nextValue) => {
                  clearLoadedReport();
                  setRange("custom");
                  setStartInput(nextValue);
                }}
                value={startInput}
              />
              <ReportsDateTimePicker
                label="To"
                onChange={(nextValue) => {
                  clearLoadedReport();
                  setRange("custom");
                  setEndInput(nextValue);
                }}
                value={endInput}
              />
            </div>
            <div className="report-timezone-note">
              <Clock3 size={14} />
              Live Preview | Browser&apos;s Timezone
            </div>
          </div>

          <div className="report-include-panel">
            <span className="report-panel-label">Include</span>
            <button className={options.includeDenied ? "active" : ""} onClick={() => updateOption("includeDenied")} type="button">
              <span className="report-check-box"><AlertTriangle size={14} /></span> Denied attempts
            </button>
            <button className={options.includeSnapshots ? "active" : ""} onClick={() => updateOption("includeSnapshots")} type="button">
              <span className="report-check-box"><Camera size={14} /></span> Snapshots
            </button>
            <button className={options.includeConfidence ? "active" : ""} onClick={() => updateOption("includeConfidence")} type="button">
              <span className="report-check-box"><CheckCircle2 size={14} /></span> Confidence
            </button>
          </div>

          <div className="report-live-summary-panel">
            <span className="report-panel-label">{loadedReport ? "Report Summary" : "Live Summary"}</span>
            <div><LogIn size={15} /><span>Arrivals</span><strong>{previewSummary.arrivals}</strong></div>
            <div><LogOut size={15} /><span>Departures</span><strong>{previewSummary.departures}</strong></div>
            <div><BarChart3 size={15} /><span>Total Events</span><strong>{previewSummary.total}</strong></div>
            <div><Clock3 size={15} /><span>First Event</span><strong>{previewSummary.firstEvent}</strong></div>
          </div>
        </div>

        <div className="report-builder-status">
          <div>
            <span className="report-live-dot" />
            <strong>{loadedReport ? `Report #${loadedReport.report_id}` : "Live"}</strong>
          </div>
          <button
            className="report-export-button"
            disabled={(!selectedPerson && !selectedVisitorPass && !loadedReport) || isExportingReport}
            onClick={handleReportExportClick}
            type="button"
          >
            <Download size={16} /> {isExportingReport ? "Exporting PDF" : loadedReport ? "Download PDF" : "Export PDF"}
          </button>
        </div>
        {reportActionError ? (
          <div className="report-action-error" role="alert">{reportActionError}</div>
        ) : null}
      </div>

      {previewPerson ? (
      <div className="report-preview-shell">
        <div className="report-preview-header">
          <div>
            <h2>{loadedReport ? "Exported Report" : "Live Preview"}</h2>
            <p>{previewPerson.display_name} {previewSubjectKind.toLowerCase()} movement report</p>
          </div>
          <Badge tone="blue">{loadedReport ? `Report #${loadedReport.report_id}` : "Updates live"}</Badge>
        </div>

        <article className="report-sheet" aria-label="Report preview">
          <header className="report-document-header">
            <div className="report-brand">
              <span className="report-brand-mark"><ShieldCheck size={20} /></span>
              <div>
                <strong>IACS</strong>
                <span>Intelligent Access Control System</span>
              </div>
            </div>
            <h2>{previewSubjectKind} Arrivals / Departures Report</h2>
            <span>Generated {previewGeneratedLabel}</span>
          </header>

          <section className="report-subject-band">
            <div className="report-subject-person">
              <span className="report-avatar">
                {previewPerson.profile_photo_data_url ? <img alt="" src={previewPerson.profile_photo_data_url} /> : reportInitials(previewPerson)}
              </span>
              <div>
                <strong>{previewPerson.display_name}</strong>
                <span>{loadedReport ? `Report ID ${loadedReport.report_id}` : personMetaLabel(previewPerson)}</span>
                <span>{vehicleLabel(previewPerson)}</span>
              </div>
            </div>
            <div className="report-subject-period">
              <CalendarDays size={17} />
              <span>Period</span>
              <strong>{previewPeriodLabel}</strong>
            </div>
            <div className="report-subject-metrics">
              <span><LogIn size={16} /> Arrivals <strong>{previewSummary.arrivals}</strong></span>
              <span><LogOut size={16} /> Departures <strong>{previewSummary.departures}</strong></span>
              <span><BarChart3 size={16} /> Total <strong>{previewSummary.total}</strong></span>
            </div>
          </section>

          <div className="report-document-grid">
            <section className="report-table-panel">
              {previewEvents.length ? (
                <div className="report-table">
                  <div className="report-table-head">
                    <span>Date / Time</span>
                    <span>Type</span>
                    <span>Vehicle</span>
                    <span>Event Detail</span>
                    <span>Duration</span>
                    <span>Source</span>
                    <span>Photo</span>
                    <span>Conf.</span>
                  </div>
                  {previewEvents.map((event) => {
                    const Icon = eventIcon(event);
                    return (
                      <div className="report-table-row" key={event.id}>
                        <time className="report-cell-time">{formatDate(event.occurred_at)}</time>
                        <span className={`report-type-pill ${eventTone(event)}`}><Icon size={15} /> {eventLabel(event)}</span>
                        <strong className="report-cell-vehicle">{event.registration_number}</strong>
                        <span className="report-cell-detail">{event.detail ?? (event.decision === "granted" ? "Access granted" : "Access denied")}</span>
                        <span className="report-cell-duration">
                          <ReportDurationCell duration={event.duration ?? durationByEventId.get(event.id) ?? { label: "N/A", tone: "muted" }} />
                        </span>
                        <span className="report-cell-source">{event.source_label ?? sourceLabel(event.source)}</span>
                        {previewOptions.includeSnapshots ? <ReportSnapshotThumb event={event} /> : <span className="report-table-excluded">Off</span>}
                        {previewOptions.includeConfidence ? <span className="report-confidence">{event.confidence_percent ?? Math.round(event.confidence * 100)}%</span> : <span className="report-table-excluded">Off</span>}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="report-empty-state">
                  <UserRound size={28} />
                  <strong>No movements in this period</strong>
                  <span>Try a longer quick range or choose a subject with access events.</span>
                </div>
              )}
              {previewEventCount ? (
                <div className="report-table-footer">
                  Showing {previewEvents.length} of {previewEventCount} events
                </div>
              ) : null}
            </section>

            <aside className="report-side-panel">
              <div>
                <h3>Period Summary</h3>
                {loadedReport ? <p><span>Report ID</span><strong>{loadedReport.report_id}</strong></p> : null}
                <p><span>Start</span><strong>{previewPeriodStartLabel}</strong></p>
                <p><span>End</span><strong>{previewPeriodEndLabel}</strong></p>
                <p><span>Duration</span><strong>{previewPeriodDurationLabel}</strong></p>
                <p><span>Presence</span><strong>{previewPresence ? titleCase(previewPresence.state) : "Unknown"}</strong></p>
              </div>
              <div>
                <h3>Vehicle Summary</h3>
                {previewVehicles.map((vehicle) => (
                  <div className="report-vehicle-summary-card" key={vehicle.id}>
                    <div className="report-vehicle-summary-title">
                      <Car size={15} />
                      <div>
                        <strong>{reportVehicleTitle(vehicle)}</strong>
                        <span>{vehicle.registration_number}</span>
                      </div>
                    </div>
                    <div className="report-vehicle-compliance-row">
                      <span>MOT</span>
                      <strong className={`report-compliance-pill ${vehicle.mot_tone ?? reportComplianceTone(vehicle.mot_status)}`}>
                        {vehicle.mot_label ?? reportComplianceLabel(vehicle.mot_expiry)}
                      </strong>
                    </div>
                    <div className="report-vehicle-compliance-row">
                      <span>Tax</span>
                      <strong className={`report-compliance-pill ${vehicle.tax_tone ?? reportComplianceTone(vehicle.tax_status)}`}>
                        {vehicle.tax_label ?? reportComplianceLabel(vehicle.tax_expiry)}
                      </strong>
                    </div>
                  </div>
                ))}
                {!previewVehicles.length ? <p><span>No registered vehicles</span></p> : null}
              </div>
            </aside>
          </div>

          <section className="report-period-timeline-section">
            <div className="report-section-heading">
              <h3>Timeline (All-Day Rhythm)</h3>
            </div>
            {previewAllTimelineEvents.length ? (
              <div className="report-period-timeline">
                <div className="report-period-labels">
                  {dayRhythmTicks.map((tick) => (
                    <span key={`${tick.left}-${tick.label}`} style={{ left: `${tick.left}%` }}>{tick.label}</span>
                  ))}
                </div>
                <div className="report-period-track">
                  {dayRhythmTicks.map((tick) => (
                    <span className="report-period-tick" key={`${tick.left}-${tick.label}`} style={{ left: `${tick.left}%` }} />
                  ))}
                  {previewAllTimelineEvents.map((event) => (
                    <span
                      className="report-period-marker grey"
                      key={`all-${event.id}`}
                      style={{ left: `${"progress" in event ? event.progress : dayRhythmProgress(event)}%` }}
                      title={`All vehicles · ${"label" in event ? event.label : eventLabel(event)} · ${formatDate(event.occurred_at)} · ${event.registration_number}`}
                    />
                  ))}
                  {previewSelectedTimelineEvents.map((event) => (
                    <span
                      className={`report-period-marker ${"tone" in event ? event.tone : eventTone(event)}`}
                      key={event.id}
                      style={{ left: `${"progress" in event ? event.progress : dayRhythmProgress(event)}%` }}
                      title={`${"label" in event ? event.label : eventLabel(event)} · ${formatDate(event.occurred_at)} · ${event.registration_number}`}
                    />
                  ))}
                </div>
                <div className="report-period-legend">
                  <span><i className="grey" /> All vehicles</span>
                  <span><i className="green" /> {previewSubjectKind} arrivals</span>
                  <span><i className="blue" /> {previewSubjectKind} departures</span>
                </div>
              </div>
            ) : (
              <p className="report-muted-copy">No vehicle arrivals or departures are available in the selected window.</p>
            )}
          </section>
        </article>
      </div>
      ) : null}
    </section>
  );
}
