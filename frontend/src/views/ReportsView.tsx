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
import React from "react";
import { createPortal } from "react-dom";

import { api } from "../api/client";
import { reportsApi, type ReportDurationInfo, type ReportExportResponse, type ReportSnapshotEvent,
  type ReportSnapshotVehicle, type ReportPreviewResponse, type ReportPreviewRequest } from "../api/reports";
import { initials, matches, titleCase } from "../lib/format";
import { mediaSource } from "../lib/media";
import { Badge } from "../ui/primitives";
import type { AccessEvent, Person, Presence, TooltipPositionState } from "../api/types";
import type { VisitorPass } from "./PassesView";

type QuickRange = "24h" | "3d" | "7d" | "14d" | "custom";
type ReportOptions = { includeDenied: boolean; includeSnapshots: boolean; includeConfidence: boolean };

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

// Civil calendar values use UTC Date fields only as a calendar carrier. They are
// never interpreted as instants; the report API resolves site-zone input and DST.
function toDateTimeInputValue(date: Date) {
  return date.toISOString().slice(0, 16);
}
function parseDateTimeInput(value: string) {
  return new Date(`${value || "2000-01-01T00:00"}Z`);
}
function siteCivilInput(value: string, timezone: string) {
  if (!value || !timezone) return "";
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(value)) return value.slice(0, 16);
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23"
  }).formatToParts(new Date(value));
  const part = (key: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === key)!.value;
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}
function formatSiteDate(value: string, timezone: string) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: timezone, year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit"
  }).format(new Date(value));
}

function reportInitials(person: Pick<Person, "display_name" | "first_name" | "last_name">) {
  return initials(person.display_name || `${person.first_name} ${person.last_name}`);
}

function eventIcon(event: Pick<ReportSnapshotEvent, "decision" | "direction">) {
  if (event.decision === "denied") return AlertTriangle;
  return event.direction === "entry" ? LogIn : LogOut;
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

function reportVehicleTitle(vehicle: Pick<Person["vehicles"][number], "make" | "model" | "description"> & { title?: string }) {
  return vehicle.title || [vehicle.make, vehicle.model].filter(Boolean).join(" ") || vehicle.description || "Vehicle";
}

function visitorPassMetaLabel(visitorPass: VisitorPass) {
  return `Visitor Pass · ${titleCase(visitorPass.status)} · ${titleCase(visitorPass.pass_type)}`;
}

function visitorPassVehicleLabel(visitorPass: VisitorPass) {
  return visitorPass.number_plate || "No plate assigned";
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
    date.getUTCFullYear(),
    String(date.getUTCMonth() + 1).padStart(2, "0"),
    String(date.getUTCDate()).padStart(2, "0")
  ].join("-");
}

function reportCalendarDays(month: Date) {
  const firstDay = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth(), 1));
  const mondayOffset = (firstDay.getUTCDay() + 6) % 7;
  const start = new Date(firstDay);
  start.setUTCDate(firstDay.getUTCDate() - mondayOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const next = new Date(start);
    next.setUTCDate(start.getUTCDate() + index);
    return next;
  });
}

function formatReportPickerValue(date: Date) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: "UTC",
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
  const [visibleMonth, setVisibleMonth] = React.useState(() => new Date(Date.UTC(selectedDate.getUTCFullYear(), selectedDate.getUTCMonth(), 1)));
  const selectedKey = localDateKey(selectedDate);
  const days = React.useMemo(() => reportCalendarDays(visibleMonth), [visibleMonth]);

  React.useEffect(() => {
    if (!open) setVisibleMonth(new Date(Date.UTC(selectedDate.getUTCFullYear(), selectedDate.getUTCMonth(), 1)));
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
    next.setUTCHours(selectedDate.getUTCHours(), selectedDate.getUTCMinutes(), 0, 0);
    updateDate(next);
  };

  const setTimePart = (part: "hour" | "minute", rawValue: string) => {
    const parsed = Number(rawValue);
    const next = new Date(selectedDate);
    if (part === "hour") next.setUTCHours(clampDateTimePart(parsed, 0, 23));
    if (part === "minute") next.setUTCMinutes(clampDateTimePart(parsed, 0, 59));
    next.setUTCSeconds(0, 0);
    updateDate(next);
  };

  return (
    <div className="report-date-time-field">
      <span>{label}</span>
      <button
        aria-label={label}
        disabled={!value}
        aria-controls={open ? pickerId : undefined}
        aria-expanded={open}
        className="report-date-time-trigger"
        onClick={() => setOpen((current) => !current)}
        ref={buttonRef}
        type="button"
      >
        <CalendarDays size={15} />
        <strong>{value ? formatReportPickerValue(selectedDate) : "Loading site time…"}</strong>
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
            <button aria-label="Previous month" onClick={() => setVisibleMonth(new Date(Date.UTC(visibleMonth.getUTCFullYear(), visibleMonth.getUTCMonth() - 1, 1)))} type="button">
              <ChevronLeft size={16} />
            </button>
            <strong>{visibleMonth.toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" })}</strong>
            <button aria-label="Next month" onClick={() => setVisibleMonth(new Date(Date.UTC(visibleMonth.getUTCFullYear(), visibleMonth.getUTCMonth() + 1, 1)))} type="button">
              <ChevronRight size={16} />
            </button>
          </div>
          <div className="report-date-time-calendar">
            {reportWeekdayLabels.map((day) => <span key={day}>{day}</span>)}
            {days.map((day) => {
              const key = localDateKey(day);
              return (
                <button
                  className={`${day.getUTCMonth() === visibleMonth.getUTCMonth() ? "" : "muted"} ${key === selectedKey ? "active" : ""}`}
                  key={key}
                  onClick={() => setDay(day)}
                  type="button"
                >
                  {day.getUTCDate()}
                </button>
              );
            })}
          </div>
          <div className="report-date-time-footer">
            <div>
              <span>Time</span>
              <strong>{String(selectedDate.getUTCHours()).padStart(2, "0")}:{String(selectedDate.getUTCMinutes()).padStart(2, "0")}</strong>
            </div>
            <label>
              <span>Hour</span>
              <input max={23} min={0} onChange={(event) => setTimePart("hour", event.target.value)} type="number" value={String(selectedDate.getUTCHours()).padStart(2, "0")} />
            </label>
            <label>
              <span>Min</span>
              <input max={59} min={0} onChange={(event) => setTimePart("minute", event.target.value)} type="number" value={String(selectedDate.getUTCMinutes()).padStart(2, "0")} />
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

function ReportSnapshotThumb({ event, timezone }: { event: ReportSnapshotEvent; timezone: string }) {
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
          <span>{event.snapshot_captured_at ? `Captured ${formatSiteDate(event.snapshot_captured_at, timezone)}` : `${event.type_label} at ${event.occurred_label ?? formatSiteDate(event.occurred_at, timezone)}`}</span>
        </div>,
        document.body
      ) : null}
    </button>
  );
}

export function ReportsView({
  events, people, presence
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
  const [endInput, setEndInput] = React.useState("");
  const [startInput, setStartInput] = React.useState("");
  const [siteTimezone, setSiteTimezone] = React.useState("");
  const [rangeRevision, setRangeRevision] = React.useState(0);
  const [isLoadingContext, setIsLoadingContext] = React.useState(true);
  const [contextError, setContextError] = React.useState<string | null>(null);
  const [folds, setFolds] = React.useState<Pick<ReportPreviewRequest, "period_start_fold" | "period_end_fold">>({});
  const [preview, setPreview] = React.useState<{ request: ReportPreviewRequest; result: ReportPreviewResponse } | null>(null);
  const [previewError, setPreviewError] = React.useState<{ request: ReportPreviewRequest; message: string } | null>(null);
  const [options, setOptions] = React.useState<ReportOptions>(defaultOptions);
  const [visitorPasses, setVisitorPasses] = React.useState<VisitorPass[]>([]);
  const [loadedReport, setLoadedReport] = React.useState<ReportExportResponse | null>(null);
  const [isExportingReport, setIsExportingReport] = React.useState(false);
  const [isLoadingReportId, setIsLoadingReportId] = React.useState(false);
  const [reportActionError, setReportActionError] = React.useState<string | null>(null);
  const visitorPassesLoadedRef = React.useRef(false);
  const actionController = React.useRef<AbortController | null>(null);
  React.useEffect(() => () => actionController.current?.abort(), []);

  React.useEffect(() => {
    if (range === "custom") { setIsLoadingContext(false); return; }
    const controller = new AbortController();
    setIsLoadingContext(true);
    setContextError(null);
    reportsApi.context({ signal: controller.signal }).then((context) => {
      if (controller.signal.aborted) return;
      setSiteTimezone(context.site_timezone);
      const hours = quickRanges.find((item) => item.value === range)!.hours;
      setEndInput(context.now);
      setStartInput(new Date(new Date(context.now).getTime() - hours * 3_600_000).toISOString());
      setFolds({});
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setContextError(error instanceof Error ? error.message : "Site time could not be loaded.");
    }).finally(() => {
      if (!controller.signal.aborted) setIsLoadingContext(false);
    });
    return () => controller.abort();
  }, [range, rangeRevision]);

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

  const selectedPerson = reportablePeople.find((person) => person.id === selectedPersonId) ?? null;
  const selectedVisitorPass = visitorPasses.find((visitorPass) => visitorPass.id === selectedVisitorPassId) ?? null;
  React.useEffect(() => {
    const shouldLoadVisitorPasses = isPersonSearchOpen || Boolean(personQuery.trim()) || Boolean(selectedVisitorPassId);
    if (!shouldLoadVisitorPasses || visitorPassesLoadedRef.current) return undefined;
    const controller = new AbortController();
    api.get<VisitorPass[]>("/api/v1/visitor-passes?limit=500", { signal: controller.signal })
      .then((nextVisitorPasses) => {
        if (controller.signal.aborted) return;
        visitorPassesLoadedRef.current = true;
        setVisitorPasses(nextVisitorPasses);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [isPersonSearchOpen, personQuery, selectedVisitorPassId]);

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

  // Shell resources invalidate the backend read; they never supply partial report data.
  const previewRequest = React.useMemo<ReportPreviewRequest | null>(() => {
    if (loadedReport || isLoadingContext || contextError || !startInput || !endInput || (!selectedPersonId && !selectedVisitorPassId)) return null;
    return {
      person_id: selectedPersonId || undefined, visitor_pass_id: selectedVisitorPassId || undefined,
      period_start: startInput, period_end: endInput,
      include_denied: options.includeDenied, include_snapshots: options.includeSnapshots,
      include_confidence: options.includeConfidence, ...folds
    };
  }, [selectedPersonId, selectedVisitorPassId, startInput, endInput, options, folds, loadedReport,
    isLoadingContext, contextError, events, people, presence]);
  React.useEffect(() => {
    if (!previewRequest) return;
    const controller = new AbortController();
    reportsApi.preview(previewRequest, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setPreview({ request: previewRequest, result }); })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setPreviewError({ request: previewRequest, message: error instanceof Error ? error.message : "Report preview failed." });
      });
    return () => controller.abort();
  }, [previewRequest]);
  const currentPreview = preview?.request === previewRequest ? preview.result : null;
  const currentPreviewError = previewError?.request === previewRequest ? previewError.message : null;
  const previewPending = Boolean(previewRequest && !currentPreview && !currentPreviewError);
  const activeReport = loadedReport?.report ?? (currentPreview?.status === "ready" ? currentPreview.report : null);
  const previewPerson = activeReport?.person ?? null;
  const previewPresence = activeReport?.presence ?? null;
  const previewSummary = {
    arrivals: activeReport?.summary.arrivals ?? "—", departures: activeReport?.summary.departures ?? "—",
    total: activeReport?.summary.total ?? "—", firstEvent: activeReport?.summary.first_event ?? "—"
  };
  const previewEvents = activeReport?.events ?? [];
  const previewEventCount = previewEvents.length;
  const previewVehicles: ReportSnapshotVehicle[] = previewPerson?.vehicles ?? [];
  const previewAllTimelineEvents = activeReport?.timeline.all ?? [];
  const previewSelectedTimelineEvents = activeReport?.timeline.selected ?? [];
  const previewPeriodLabel = activeReport?.period.label ?? "";
  const previewPeriodStartLabel = activeReport?.period.start_label ?? "";
  const previewPeriodEndLabel = activeReport?.period.end_label ?? "";
  const previewPeriodDurationLabel = activeReport?.period.duration_label ?? "";
  const previewGeneratedLabel = activeReport?.generated_label ?? "";
  const previewOptions = activeReport ? {
    includeDenied: activeReport.options.include_denied, includeSnapshots: activeReport.options.include_snapshots,
    includeConfidence: activeReport.options.include_confidence
  } : options;
  const previewSubjectKind = activeReport?.subject_type === "visitor_pass" ? "Visitor Pass" : "Person";
  const displayTimezone = activeReport?.period.timezone ?? siteTimezone;
  const previewPersonPhoto = previewPerson ? mediaSource(previewPerson.profile_photo_url, previewPerson.profile_photo_data_url, "thumb") : "";

  const cancelReportAction = React.useCallback(() => {
    actionController.current?.abort();
    actionController.current = null;
    setIsLoadingReportId(false);
    setIsExportingReport(false);
  }, []);

  const selectPerson = React.useCallback((person: Person) => {
    cancelReportAction();
    setSelectedPersonId(person.id);
    setSelectedVisitorPassId("");
    setPersonQuery(person.display_name);
    setIsPersonSearchOpen(false);
    setHighlightedPersonIndex(0);
    setLoadedReport(null);
    setReportActionError(null);
  }, [cancelReportAction]);

  const selectVisitorPass = React.useCallback((visitorPass: VisitorPass) => {
    setSelectedPersonId("");
    cancelReportAction();
    setSelectedVisitorPassId(visitorPass.id);
    setPersonQuery(visitorPass.visitor_name);
    setIsPersonSearchOpen(false);
    setHighlightedPersonIndex(0);
    setLoadedReport(null);
    setReportActionError(null);
  }, [cancelReportAction]);

  const downloadReportPdf = React.useCallback((downloadUrl: string) => {
    const anchor = document.createElement("a");
    anchor.href = downloadUrl;
    anchor.download = "";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }, []);

  const loadReportById = React.useCallback(async (reportId: string) => {
    cancelReportAction();
    const controller = new AbortController();
    actionController.current = controller;
    setRange("custom");
    setIsLoadingReportId(true);
    setReportActionError(null);
    try {
      const response = await reportsApi.load(reportId, { signal: controller.signal });
      if (controller.signal.aborted) return;
      const matchingPerson = response.report.subject_type === "visitor_pass"
        ? null
        : reportablePeople.find((person) => person.id === response.report.person.id);
      const matchingVisitorPass = response.report.subject_type === "visitor_pass"
        ? visitorPasses.find((visitorPass) => visitorPass.id === response.report.person.id)
        : null;
      setLoadedReport(response);
      setSiteTimezone(response.report.period.timezone);
      setFolds({});
      setSelectedPersonId(matchingPerson?.id ?? "");
      setSelectedVisitorPassId(matchingVisitorPass?.id ?? "");
      setPersonQuery(`Report #${response.report_id} - ${response.report.person.display_name}`);
      setStartInput(response.report.period.start);
      setEndInput(response.report.period.end);
      setOptions({
        includeDenied: response.report.options.include_denied,
        includeSnapshots: response.report.options.include_snapshots,
        includeConfidence: response.report.options.include_confidence
      });
      setRange("custom");
      setIsPersonSearchOpen(false);
      setHighlightedPersonIndex(0);
    } catch (error) {
      if (!controller.signal.aborted) setReportActionError(error instanceof Error ? error.message : "Report could not be loaded.");
    } finally {
      if (actionController.current === controller) { actionController.current = null; setIsLoadingReportId(false); }
    }
  }, [cancelReportAction, reportablePeople, visitorPasses]);

  const exportCurrentReport = React.useCallback(async () => {
    if (currentPreview?.status !== "ready" || actionController.current) return;
    const controller = new AbortController();
    actionController.current = controller;
    setIsExportingReport(true);
    setReportActionError(null);
    try {
      const report = currentPreview.report;
      const response = await reportsApi.export({
        person_id: selectedPersonId || undefined, visitor_pass_id: selectedVisitorPassId || undefined,
        period_start: report.period.start, period_end: report.period.end, ...report.options
      }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setLoadedReport(response);
      setPersonQuery(`Report #${response.report_id} - ${response.report.person.display_name}`);
      downloadReportPdf(response.download_url);
    } catch (error) {
      if (!controller.signal.aborted) setReportActionError(error instanceof Error ? error.message : "Report could not be exported.");
    } finally {
      if (actionController.current === controller) { actionController.current = null; setIsExportingReport(false); }
    }
  }, [currentPreview, downloadReportPdf, selectedPersonId, selectedVisitorPassId]);

  const handleReportExportClick = () => {
    if (loadedReport) {
      downloadReportPdf(loadedReport.download_url);
      return;
    }
    void exportCurrentReport();
  };

  const clearLoadedReport = () => {
    cancelReportAction();
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
    clearLoadedReport();
    setIsLoadingContext(true);
    setRange(nextRange);
    setRangeRevision((revision) => revision + 1);
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
                {previewPersonPhoto
                  ? <img alt="" decoding="async" loading="lazy" src={previewPersonPhoto} />
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
                  setFolds((current) => ({ ...current, period_start_fold: undefined }));
                }}
                value={siteCivilInput(startInput, displayTimezone)}
              />
              <ReportsDateTimePicker
                label="To"
                onChange={(nextValue) => {
                  clearLoadedReport();
                  setRange("custom");
                  setEndInput(nextValue);
                  setFolds((current) => ({ ...current, period_end_fold: undefined }));
                }}
                value={siteCivilInput(endInput, displayTimezone)}
              />
            </div>
            <div className="report-timezone-note">
              <Clock3 size={14} />
              Complete Preview | {displayTimezone || "Loading site timezone…"}
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
            <span className="report-panel-label">{loadedReport ? "Report Summary" : "Preview Summary"}</span>
            <div><LogIn size={15} /><span>Arrivals</span><strong>{previewSummary.arrivals}</strong></div>
            <div><LogOut size={15} /><span>Departures</span><strong>{previewSummary.departures}</strong></div>
            <div><BarChart3 size={15} /><span>Total Events</span><strong>{previewSummary.total}</strong></div>
            <div><Clock3 size={15} /><span>First Event</span><strong>{previewSummary.firstEvent}</strong></div>
          </div>
        </div>

        <div className="report-builder-status">
          <div>
            <span className="report-live-dot" />
            <strong>{loadedReport ? `Report #${loadedReport.report_id}` : previewPending ? "Loading complete preview…" : activeReport ? "Complete preview" : "Choose a subject and period"}</strong>
          </div>
          <button
            className="report-export-button"
            disabled={(!loadedReport && currentPreview?.status !== "ready") || isExportingReport || isLoadingReportId}
            onClick={handleReportExportClick}
            type="button"
          >
            <Download size={16} /> {isExportingReport ? "Exporting PDF" : loadedReport ? "Download PDF" : "Export PDF"}
          </button>
        </div>
        {currentPreview?.status === "time_choice_required" ? currentPreview.time_choices.map((choice) => (
          <label className="report-field" key={choice.field}>
            <span>{choice.field === "period_start" ? "From" : "To"}: {choice.local_time.replace("T", " ")} occurs twice in {currentPreview.site_timezone}</span>
            <select aria-label={`${choice.field === "period_start" ? "From" : "To"} occurrence`}
              value={folds[`${choice.field}_fold`] ?? ""}
              onChange={(event) => setFolds((current) => ({ ...current, [`${choice.field}_fold`]: Number(event.target.value) as 0 | 1 }))}>
              <option value="" disabled>Choose an occurrence</option>
              {choice.choices.map((occurrence) => <option key={occurrence.fold} value={occurrence.fold}>{occurrence.label}</option>)}
            </select>
          </label>
        )) : null}
        {reportActionError || currentPreviewError || contextError ? (
          <div className="report-action-error" role="alert">{reportActionError || currentPreviewError || contextError}</div>
        ) : null}
      </div>

      {previewPerson ? (
      <div className="report-preview-shell">
        <div className="report-preview-header">
          <div>
            <h2>{loadedReport ? "Exported Report" : "Complete Preview"}</h2>
            <p>{previewPerson.display_name} {previewSubjectKind.toLowerCase()} movement report</p>
          </div>
          <Badge tone="blue">{loadedReport ? `Report #${loadedReport.report_id}` : "Complete history"}</Badge>
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
                {previewPersonPhoto ? <img alt="" decoding="async" loading="lazy" src={previewPersonPhoto} /> : reportInitials(previewPerson)}
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
                        <time className="report-cell-time">{event.occurred_label ?? formatSiteDate(event.occurred_at, displayTimezone)}</time>
                        <span className={`report-type-pill ${event.tone}`}><Icon size={15} /> {event.type_label}</span>
                        <strong className="report-cell-vehicle">{event.registration_number}</strong>
                        <span className="report-cell-detail">{event.detail ?? (event.decision === "granted" ? "Access granted" : "Access denied")}</span>
                        <span className="report-cell-duration">
                          <ReportDurationCell duration={event.duration ?? { label: "N/A", tone: "muted" }} />
                        </span>
                        <span className="report-cell-source">{event.source_label ?? event.source}</span>
                        {previewOptions.includeSnapshots ? <ReportSnapshotThumb event={event} timezone={displayTimezone} /> : <span className="report-table-excluded">Off</span>}
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
                      style={{ left: `${event.progress}%` }}
                      title={`All vehicles · ${event.label} · ${formatSiteDate(event.occurred_at, displayTimezone)} · ${event.registration_number}`}
                    />
                  ))}
                  {previewSelectedTimelineEvents.map((event) => (
                    <span
                      className={`report-period-marker ${event.tone}`}
                      key={event.id}
                      style={{ left: `${event.progress}%` }}
                      title={`${event.label} · ${formatSiteDate(event.occurred_at, displayTimezone)} · ${event.registration_number}`}
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
