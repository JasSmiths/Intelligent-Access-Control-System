import { CalendarDays, ClipboardPaste, Copy, Trash2, X } from "lucide-react";
import React from "react";
import type { ScheduleTimeBlocks } from "../../api/types";
import { scheduleDays } from "../../lib/format";
import type { ScheduleCellPoint, ScheduleCopiedBlock } from "./model";
import { addSlotRange, formatScheduleBlockLabel, formatSlotLabel, scheduleBlocksToSlots, scheduleSlotCount, scheduleSlotKey, scheduleSummary, selectedSlotRange, slotsToScheduleBlocks } from "./model";

type ScheduleDragState = {
  active: boolean;
  targetSelected: boolean;
  anchorDay: number;
  anchorSlot: number;
  baseSlots: Set<string>;
};

type ScheduleContextMenu =
  | {
    kind: "selected";
    x: number;
    y: number;
    day: number;
    range: ScheduleCopiedBlock;
  }
  | {
    kind: "empty";
    x: number;
    y: number;
    day: number;
  };

const scheduleDayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function WeeklyScheduleGrid({
  value,
  onChange
}: {
  value: ScheduleTimeBlocks;
  onChange: (timeBlocks: ScheduleTimeBlocks) => void;
}) {
  const [selectedSlots, setSelectedSlots] = React.useState<Set<string>>(() => scheduleBlocksToSlots(value));
  const calendarRef = React.useRef<HTMLDivElement | null>(null);
  const dragRef = React.useRef<ScheduleDragState>({
    active: false,
    targetSelected: false,
    anchorDay: 0,
    anchorSlot: 0,
    baseSlots: new Set()
  });
  const autoScrollRef = React.useRef<{ frame: number | null; clientX: number; clientY: number }>({
    frame: null,
    clientX: 0,
    clientY: 0
  });
  const [copiedBlock, setCopiedBlock] = React.useState<ScheduleCopiedBlock | null>(null);
  const [contextMenu, setContextMenu] = React.useState<ScheduleContextMenu | null>(null);

  React.useEffect(() => {
    setSelectedSlots(scheduleBlocksToSlots(value));
  }, [value]);

  const commitSlots = React.useCallback((nextSlots: Set<string>) => {
    setSelectedSlots(nextSlots);
    onChange(slotsToScheduleBlocks(nextSlots));
  }, [onChange]);

  const applyDragRange = React.useCallback((day: number, slot: number) => {
    const drag = dragRef.current;
    if (!drag.active) return;

    const next = new Set(drag.baseSlots);
    const startDay = Math.min(drag.anchorDay, day);
    const endDay = Math.max(drag.anchorDay, day);
    const startSlot = Math.min(drag.anchorSlot, slot);
    const endSlot = Math.max(drag.anchorSlot, slot);

    for (let rangeDay = startDay; rangeDay <= endDay; rangeDay += 1) {
      for (let rangeSlot = startSlot; rangeSlot <= endSlot; rangeSlot += 1) {
        const key = scheduleSlotKey(rangeDay, rangeSlot);
        if (drag.targetSelected) {
          next.add(key);
        } else {
          next.delete(key);
        }
      }
    }

    setSelectedSlots(next);
    onChange(slotsToScheduleBlocks(next));
  }, [onChange]);

  const applyBlockToDays = React.useCallback((days: number[], block: ScheduleCopiedBlock) => {
    setSelectedSlots((current) => {
      const next = new Set(current);
      for (const day of days) {
        for (let slot = block.startSlot; slot <= block.endSlot; slot += 1) {
          next.add(scheduleSlotKey(day, slot));
        }
      }
      onChange(slotsToScheduleBlocks(next));
      return next;
    });
    setContextMenu(null);
  }, [onChange]);

  const stopAutoScroll = React.useCallback(() => {
    if (autoScrollRef.current.frame !== null) {
      window.cancelAnimationFrame(autoScrollRef.current.frame);
      autoScrollRef.current.frame = null;
    }
  }, []);

  const runAutoScroll = React.useCallback(() => {
    const calendar = calendarRef.current;
    if (!dragRef.current.active || !calendar) {
      autoScrollRef.current.frame = null;
      return;
    }

    const { clientX, clientY } = autoScrollRef.current;
    const rect = calendar.getBoundingClientRect();
    const edgeSize = 56;
    const maxStep = 18;
    let top = 0;
    let left = 0;

    if (clientY < rect.top + edgeSize) {
      top = -Math.ceil(((rect.top + edgeSize - clientY) / edgeSize) * maxStep);
    } else if (clientY > rect.bottom - edgeSize) {
      top = Math.ceil(((clientY - (rect.bottom - edgeSize)) / edgeSize) * maxStep);
    }

    if (clientX < rect.left + edgeSize) {
      left = -Math.ceil(((rect.left + edgeSize - clientX) / edgeSize) * maxStep);
    } else if (clientX > rect.right - edgeSize) {
      left = Math.ceil(((clientX - (rect.right - edgeSize)) / edgeSize) * maxStep);
    }

    if (top !== 0 || left !== 0) {
      calendar.scrollBy({ top, left });
      const cell = scheduleCellFromPoint(clientX, clientY, calendar);
      if (cell) applyDragRange(cell.day, cell.slot);
    }

    autoScrollRef.current.frame = window.requestAnimationFrame(runAutoScroll);
  }, [applyDragRange]);

  const updateAutoScrollPointer = React.useCallback((clientX: number, clientY: number) => {
    autoScrollRef.current.clientX = clientX;
    autoScrollRef.current.clientY = clientY;
    if (autoScrollRef.current.frame === null) {
      autoScrollRef.current.frame = window.requestAnimationFrame(runAutoScroll);
    }
  }, [runAutoScroll]);

  React.useEffect(() => {
    const onPointerMove = (event: PointerEvent) => {
      if (!dragRef.current.active) return;
      updateAutoScrollPointer(event.clientX, event.clientY);
      const cell = scheduleCellFromPoint(event.clientX, event.clientY, calendarRef.current);
      if (!cell) return;
      applyDragRange(cell.day, cell.slot);
    };
    const onPointerUp = () => {
      dragRef.current.active = false;
      stopAutoScroll();
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      stopAutoScroll();
    };
  }, [applyDragRange, stopAutoScroll, updateAutoScrollPointer]);

  React.useEffect(() => {
    if (!contextMenu) return undefined;
    const closeMenu = () => setContextMenu(null);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };
    window.addEventListener("pointerdown", closeMenu);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", closeMenu, true);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [contextMenu]);

  const startPaint = (day: number, slot: number, event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    setContextMenu(null);
    const targetSelected = !selectedSlots.has(scheduleSlotKey(day, slot));
    dragRef.current = {
      active: true,
      targetSelected,
      anchorDay: day,
      anchorSlot: slot,
      baseSlots: new Set(selectedSlots)
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    updateAutoScrollPointer(event.clientX, event.clientY);
    applyDragRange(day, slot);
  };

  const openCellMenu = (day: number, slot: number, event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    dragRef.current.active = false;
    stopAutoScroll();

    const point = scheduleContextMenuPoint(event.clientX, event.clientY);
    const key = scheduleSlotKey(day, slot);
    if (selectedSlots.has(key)) {
      const range = selectedSlotRange(selectedSlots, day, slot);
      if (range) {
        setContextMenu({ kind: "selected", day, range, ...point });
      }
      return;
    }

    setContextMenu({ kind: "empty", day, ...point });
  };

  const copyContextRange = () => {
    if (contextMenu?.kind !== "selected") return;
    setCopiedBlock(contextMenu.range);
    setContextMenu(null);
  };

  const replicateContextRange = (days: number[]) => {
    if (contextMenu?.kind !== "selected") return;
    applyBlockToDays(days, contextMenu.range);
  };

  const clearContextRange = () => {
    if (contextMenu?.kind !== "selected") return;
    const { day, range } = contextMenu;
    setSelectedSlots((current) => {
      const next = new Set(current);
      for (let slot = range.startSlot; slot <= range.endSlot; slot += 1) {
        next.delete(scheduleSlotKey(day, slot));
      }
      onChange(slotsToScheduleBlocks(next));
      return next;
    });
    setContextMenu(null);
  };

  const clearAllContextRanges = () => {
    commitSlots(new Set());
    setContextMenu(null);
  };

  const pasteCopiedBlock = () => {
    if (contextMenu?.kind !== "empty" || !copiedBlock) return;
    applyBlockToDays([contextMenu.day], copiedBlock);
  };

  const applyPreset = (preset: "clear" | "all" | "weekdays" | "mornings") => {
    const next = new Set<string>();
    if (preset === "all") {
      for (let day = 0; day < 7; day += 1) {
        for (let slot = 0; slot < scheduleSlotCount; slot += 1) next.add(scheduleSlotKey(day, slot));
      }
    }
    if (preset === "weekdays") {
      addSlotRange(next, [0, 1, 2, 3, 4], 9 * 60, 17 * 60);
    }
    if (preset === "mornings") {
      addSlotRange(next, [0, 1, 2, 3, 4], 7 * 60, 12 * 60);
    }
    commitSlots(next);
  };

  return (
    <section className="weekly-schedule-panel">
      <div className="weekly-schedule-toolbar">
        <div>
          <strong>Weekly Access</strong>
          <span>{scheduleSummary(slotsToScheduleBlocks(selectedSlots))}</span>
        </div>
        <div>
          <button className="secondary-button" onClick={() => applyPreset("weekdays")} type="button">Weekdays</button>
          <button className="secondary-button" onClick={() => applyPreset("mornings")} type="button">Mornings</button>
          <button className="secondary-button" onClick={() => applyPreset("all")} type="button">24/7</button>
          <button className="secondary-button" onClick={() => applyPreset("clear")} type="button">Clear</button>
        </div>
      </div>

      <div className="schedule-calendar" onDragStart={(event) => event.preventDefault()} ref={calendarRef}>
        <div className="schedule-calendar-head">
          <span />
          {scheduleDays.map((day) => <strong key={day}>{day}</strong>)}
        </div>
        <div className="schedule-calendar-body">
          <div className="schedule-time-axis" aria-hidden="true">
            {Array.from({ length: 24 }, (_, hour) => (
              <span key={hour} style={{ gridRow: `${hour * 2 + 1} / span 2` }}>{`${hour.toString().padStart(2, "0")}:00`}</span>
            ))}
          </div>
          {scheduleDays.map((day, dayIndex) => (
            <div className="schedule-day-column" key={day}>
              {Array.from({ length: scheduleSlotCount }, (_, slot) => {
                const key = scheduleSlotKey(dayIndex, slot);
                const selected = selectedSlots.has(key);
                const previousSelected = selectedSlots.has(scheduleSlotKey(dayIndex, slot - 1));
                const nextSelected = selectedSlots.has(scheduleSlotKey(dayIndex, slot + 1));
                const className = [
                  "schedule-cell",
                  selected ? "selected" : "",
                  selected && !previousSelected ? "selected-start" : "",
                  selected && !nextSelected ? "selected-end" : ""
                ].filter(Boolean).join(" ");
                return (
                  <button
                    aria-label={`${scheduleDayNames[dayIndex]} ${formatSlotLabel(slot)} ${selected ? "allowed" : "blocked"}`}
                    className={className}
                    data-day={dayIndex}
                    data-schedule-cell="true"
                    data-slot={slot}
                    key={key}
                    onContextMenu={(event) => openCellMenu(dayIndex, slot, event)}
                    onPointerDown={(event) => startPaint(dayIndex, slot, event)}
                    type="button"
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
      {contextMenu ? (
        <div
          className="schedule-context-menu"
          onContextMenu={(event) => event.preventDefault()}
          onPointerDown={(event) => event.stopPropagation()}
          role="menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          {contextMenu.kind === "selected" ? (
            <>
              <div className="schedule-context-menu-label">
                <span>{scheduleDayNames[contextMenu.day]}</span>
                <strong>{formatScheduleBlockLabel(contextMenu.range)}</strong>
              </div>
              <button onClick={copyContextRange} role="menuitem" type="button">
                <Copy size={15} />
                Copy
              </button>
              <button onClick={() => replicateContextRange([0, 1, 2, 3, 4, 5, 6])} role="menuitem" type="button">
                <CalendarDays size={15} />
                Replicate All Week
              </button>
              <button onClick={() => replicateContextRange([0, 1, 2, 3, 4])} role="menuitem" type="button">
                <CalendarDays size={15} />
                Replicate Week Days Only
              </button>
              <div aria-hidden="true" className="schedule-context-menu-separator" />
              <button className="danger" onClick={clearContextRange} role="menuitem" type="button">
                <Trash2 size={15} />
                Clear Selected
              </button>
              <button className="danger" onClick={clearAllContextRanges} role="menuitem" type="button">
                <X size={15} />
                Clear All
              </button>
            </>
          ) : (
            <>
              <div className="schedule-context-menu-label">
                <span>{scheduleDayNames[contextMenu.day]}</span>
                <strong>{copiedBlock ? formatScheduleBlockLabel(copiedBlock) : "Nothing copied"}</strong>
              </div>
              <button disabled={!copiedBlock} onClick={pasteCopiedBlock} role="menuitem" type="button">
                <ClipboardPaste size={15} />
                Paste
              </button>
              <div aria-hidden="true" className="schedule-context-menu-separator" />
              <button className="danger" disabled={selectedSlots.size === 0} onClick={clearAllContextRanges} role="menuitem" type="button">
                <X size={15} />
                Clear All
              </button>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}

function scheduleCellFromPoint(
  clientX: number,
  clientY: number,
  calendar: HTMLDivElement | null
): ScheduleCellPoint | null {
  const element = document.elementFromPoint(clientX, clientY);
  const cell = element?.closest("[data-schedule-cell='true']") as HTMLElement | null;
  if (cell) {
    const day = Number(cell.dataset.day);
    const slot = Number(cell.dataset.slot);
    if (Number.isInteger(day) && Number.isInteger(slot)) return { day, slot };
  }

  if (!calendar) return null;

  const calendarRect = calendar.getBoundingClientRect();
  const edgeSlack = 72;
  if (
    clientX < calendarRect.left ||
    clientX > calendarRect.right ||
    clientY < calendarRect.top - edgeSlack ||
    clientY > calendarRect.bottom + edgeSlack
  ) {
    return null;
  }

  const body = calendar.querySelector<HTMLElement>(".schedule-calendar-body");
  if (!body) return null;

  const bodyRect = body.getBoundingClientRect();
  const axis = body.querySelector<HTMLElement>(".schedule-time-axis");
  const axisWidth = axis?.getBoundingClientRect().width ?? 56;
  const dayWidth = (bodyRect.width - axisWidth) / scheduleDays.length;
  const slotHeight = bodyRect.height / scheduleSlotCount;
  if (dayWidth <= 0 || slotHeight <= 0) return null;

  const rawDay = Math.floor((clientX - bodyRect.left - axisWidth) / dayWidth);
  const rawSlot = Math.floor((clientY - bodyRect.top) / slotHeight);
  const day = Math.max(0, Math.min(scheduleDays.length - 1, rawDay));
  const slot = Math.max(0, Math.min(scheduleSlotCount - 1, rawSlot));
  return { day, slot };
}

function scheduleContextMenuPoint(clientX: number, clientY: number) {
  const menuWidth = 244;
  const menuHeight = 292;
  return {
    x: Math.max(12, Math.min(clientX, window.innerWidth - menuWidth - 12)),
    y: Math.max(12, Math.min(clientY, window.innerHeight - menuHeight - 12))
  };
}
