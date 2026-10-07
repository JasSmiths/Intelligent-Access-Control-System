import React from "react";
import { createPortal } from "react-dom";
import { CalendarDays, ChevronLeft, ChevronRight, Clock3 } from "lucide-react";
import type { TooltipPositionState } from "../../api/types";
import { getUsableViewportBounds, observeOverlayPlacement, placeOverlay } from "../../lib/viewportPlacement";
import { parseDateTimeInput, toDateTimeInputValue } from "./model";

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

export function ReportsDateTimePicker({
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

  React.useEffect(() => {
    if (!open) return undefined;
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (buttonRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", closeOnOutside);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeOnOutside);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  React.useLayoutEffect(() => {
    if (!open || !popoverPosition) return undefined;
    const update = () => {
      const button = buttonRef.current;
      const popover = popoverRef.current;
      if (!button || !popover) return;
      const placement = placeOverlay(button.getBoundingClientRect(), { width: popover.offsetWidth, height: popover.scrollHeight }, getUsableViewportBounds(), { gap: 10, alignment: "center" });
      const center = placement.left + Math.min(popover.offsetWidth, placement.maxWidth) / 2;
      setPopoverPosition((current) => current && (current.left !== center || current.top !== placement.top || current.placement !== placement.side)
        ? { left: center, top: placement.top, placement: placement.side }
        : current);
      popover.style.maxWidth = `${placement.maxWidth}px`;
      popover.style.maxHeight = `${placement.maxHeight}px`;
      popover.style.visibility = "visible";
    };
    return observeOverlayPlacement(buttonRef.current, popoverRef.current, update);
  }, [open, Boolean(popoverPosition)]);

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
        onClick={() => { setOpen((current) => !current); setPopoverPosition(open ? null : { left: 0, top: 0, placement: "bottom" }); }}
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
          style={{ left: popoverPosition.left, top: popoverPosition.top, visibility: popoverPosition.left === 0 ? "hidden" : "visible" }}
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
