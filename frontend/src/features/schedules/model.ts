import type { ScheduleTimeBlock, ScheduleTimeBlocks } from "../../api/types";
import { scheduleDays } from "../../lib/format";

export type ScheduleCellPoint = {
  day: number;
  slot: number;
};

export type ScheduleCopiedBlock = {
  startSlot: number;
  endSlot: number;
};

export const scheduleSlotCount = 48;

const scheduleMinutesPerSlot = 30;

export function selectedSlotRange(slots: Set<string>, day: number, slot: number): ScheduleCopiedBlock | null {
  if (!slots.has(scheduleSlotKey(day, slot))) return null;
  let startSlot = slot;
  let endSlot = slot;
  while (startSlot > 0 && slots.has(scheduleSlotKey(day, startSlot - 1))) startSlot -= 1;
  while (endSlot < scheduleSlotCount - 1 && slots.has(scheduleSlotKey(day, endSlot + 1))) endSlot += 1;
  return { startSlot, endSlot };
}

export function formatScheduleBlockLabel(block: ScheduleCopiedBlock) {
  return `${formatScheduleMinute(block.startSlot * scheduleMinutesPerSlot)} - ${formatScheduleMinute((block.endSlot + 1) * scheduleMinutesPerSlot)}`;
}

export function emptyScheduleBlocks(): ScheduleTimeBlocks {
  return Object.fromEntries(scheduleDays.map((_, index) => [String(index), []])) as ScheduleTimeBlocks;
}

export function normalizeScheduleBlocks(blocks: ScheduleTimeBlocks): ScheduleTimeBlocks {
  return slotsToScheduleBlocks(scheduleBlocksToSlots(blocks));
}

export function scheduleBlocksToSlots(blocks: ScheduleTimeBlocks): Set<string> {
  const slots = new Set<string>();
  for (let day = 0; day < 7; day += 1) {
    for (const block of blocks[String(day)] ?? []) {
      const start = parseScheduleTime(block.start);
      const end = parseScheduleTime(block.end);
      if (start == null || end == null || start >= end) continue;
      for (let minute = start; minute < end; minute += scheduleMinutesPerSlot) {
        const slot = Math.floor(minute / scheduleMinutesPerSlot);
        if (slot >= 0 && slot < scheduleSlotCount) slots.add(scheduleSlotKey(day, slot));
      }
    }
  }
  return slots;
}

export function slotsToScheduleBlocks(slots: Set<string>): ScheduleTimeBlocks {
  const blocks = emptyScheduleBlocks();
  for (let day = 0; day < 7; day += 1) {
    const selected = Array.from({ length: scheduleSlotCount }, (_, slot) => slots.has(scheduleSlotKey(day, slot)));
    const intervals: ScheduleTimeBlock[] = [];
    let startSlot: number | null = null;
    for (let slot = 0; slot <= scheduleSlotCount; slot += 1) {
      const active = selected[slot] ?? false;
      if (active && startSlot === null) {
        startSlot = slot;
      }
      if ((!active || slot === scheduleSlotCount) && startSlot !== null) {
        intervals.push({
          start: formatScheduleMinute(startSlot * scheduleMinutesPerSlot),
          end: formatScheduleMinute(slot * scheduleMinutesPerSlot)
        });
        startSlot = null;
      }
    }
    blocks[String(day)] = intervals;
  }
  return blocks;
}

export function addSlotRange(target: Set<string>, days: number[], startMinute: number, endMinute: number) {
  for (const day of days) {
    for (let minute = startMinute; minute < endMinute; minute += scheduleMinutesPerSlot) {
      target.add(scheduleSlotKey(day, minute / scheduleMinutesPerSlot));
    }
  }
}

export function scheduleSlotKey(day: number, slot: number) {
  return `${day}:${slot}`;
}

function parseScheduleTime(value: string) {
  if (value === "24:00" || value === "23:59") return 24 * 60;
  const [hours, minutes] = value.split(":").map((part) => Number(part));
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  return hours * 60 + minutes;
}

function formatScheduleMinute(value: number) {
  if (value >= 24 * 60) return "24:00";
  return `${Math.floor(value / 60).toString().padStart(2, "0")}:${(value % 60).toString().padStart(2, "0")}`;
}

export function formatSlotLabel(slot: number) {
  return `${formatScheduleMinute(slot * scheduleMinutesPerSlot)}-${formatScheduleMinute((slot + 1) * scheduleMinutesPerSlot)}`;
}

export function scheduleHasBlocks(blocks: ScheduleTimeBlocks) {
  return Object.values(blocks ?? {}).some((items) => items.length);
}

export function scheduleDayHasBlocks(blocks: ScheduleTimeBlocks, day: number) {
  return Boolean(blocks?.[String(day)]?.length);
}

export function scheduleSummary(blocks: ScheduleTimeBlocks) {
  const selected = scheduleBlocksToSlots(blocks);
  if (selected.size === 0) return "No allowed time";
  if (selected.size === scheduleSlotCount * 7) return "24/7";
  const hours = selected.size / 2;
  const days = Array.from({ length: 7 }, (_, day) =>
    Array.from({ length: scheduleSlotCount }, (_, slot) => selected.has(scheduleSlotKey(day, slot))).some(Boolean)
  ).filter(Boolean).length;
  return `${hours % 1 === 0 ? hours : hours.toFixed(1)}h across ${days} day${days === 1 ? "" : "s"}`;
}
