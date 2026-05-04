import React from "react";
import { createPortal } from "react-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Camera,
  CalendarDays,
  Car,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Command,
  ClipboardPaste,
  Construction,
  Copy,
  Database,
  DoorClosed,
  DoorOpen,
  Download,
  File as FileIcon,
  FileImage,
  FileText,
  Gauge,
  GitBranch,
  HardHat,
  Home,
  Key,
  LayoutDashboard,
  Lock,
  LogIn,
  LogOut,
  Loader2,
  MessageCircle,
  Menu,
  Moon,
  Monitor,
  MoreHorizontal,
  Play,
  PlugZap,
  Plus,
  Paperclip,
  Pencil,
  RefreshCcw,
  RefreshCw,
  Search,
  Send,
  Smile,
  Smartphone,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Save,
  Split,
  Sparkles,
  Sun,
  Terminal,
  Ticket,
  Trash2,
  Trophy,
  Type,
  Unlock,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  X,
  Zap
} from "lucide-react";

import {
  api,
  Badge,
  BadgeTone,
  EmptyState,
  matches,
  Schedule,
  scheduleDays,
  ScheduleTimeBlock,
  ScheduleTimeBlocks,
  useSettings
} from "../shared";



export type ScheduleDependencyItem = {
  id: string;
  name: string;
  kind: string;
  entity_id?: string | null;
  registration_number?: string | null;
  owner?: string | null;
};

export type ScheduleDependencies = {
  people: ScheduleDependencyItem[];
  vehicles: ScheduleDependencyItem[];
  doors: ScheduleDependencyItem[];
};

export type ScheduleCellPoint = {
  day: number;
  slot: number;
};

export type ScheduleDragState = {
  active: boolean;
  targetSelected: boolean;
  anchorDay: number;
  anchorSlot: number;
  baseSlots: Set<string>;
};

export type ScheduleCopiedBlock = {
  startSlot: number;
  endSlot: number;
};

export type ScheduleContextMenu =
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

export const scheduleDayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export const scheduleSlotCount = 48;

export const scheduleMinutesPerSlot = 30;

export function SchedulesView({
  schedules,
  query,
  refresh
}: {
  schedules: Schedule[];
  query: string;
  refresh: () => Promise<void>;
}) {
  const [modalOpen, setModalOpen] = React.useState(false);
  const [selectedSchedule, setSelectedSchedule] = React.useState<Schedule | null>(null);
  const [error, setError] = React.useState("");
  const [policySaved, setPolicySaved] = React.useState("");
  const [policySaving, setPolicySaving] = React.useState(false);
  const accessSettings = useSettings("access");
  const defaultPolicy = String(accessSettings.values.schedule_default_policy ?? "allow").toLowerCase() === "deny" ? "deny" : "allow";
  const filtered = schedules.filter((schedule) =>
    matches(schedule.name, query) ||
    matches(schedule.description ?? "", query) ||
    matches(scheduleSummary(schedule.time_blocks), query)
  );

  React.useEffect(() => {
    if (!policySaved) return undefined;
    const timer = window.setTimeout(() => setPolicySaved(""), 5200);
    return () => window.clearTimeout(timer);
  }, [policySaved]);

  const openCreate = () => {
    setSelectedSchedule(null);
    setModalOpen(true);
  };

  const openEdit = (schedule: Schedule) => {
    setSelectedSchedule(schedule);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setSelectedSchedule(null);
  };

  const deleteSchedule = async (schedule: Schedule) => {
    if (!window.confirm(`Delete ${schedule.name}?`)) return;
    setError("");
    try {
      await api.delete(`/api/v1/schedules/${schedule.id}`);
      await refresh();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete schedule");
    }
  };

  const updateDefaultPolicy = async (policy: "allow" | "deny") => {
    if (policy === defaultPolicy || policySaving) return;
    setError("");
    setPolicySaved("");
    setPolicySaving(true);
    try {
      await accessSettings.save({ schedule_default_policy: policy });
      setPolicySaved("Default policy saved.");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save default policy");
    } finally {
      setPolicySaving(false);
    }
  };

  return (
    <section className="view-stack schedules-page">
      <div className="users-hero schedules-hero card">
        <div className="schedules-hero-main">
          <div>
            <span className="eyebrow">Access Control</span>
            <h1>Schedules</h1>
            <p>Reusable weekly access templates for people, vehicles, gates, and garage doors.</p>
          </div>
          <button className="primary-button" onClick={openCreate} type="button">
            <Plus size={17} /> New Schedule
          </button>
        </div>
        <section className="schedule-policy-card" aria-labelledby="schedule-default-policy-title">
          <div className="schedule-policy-copy">
            <div className="schedule-card-icon">
              <ShieldCheck size={18} />
            </div>
            <div>
              <h2 id="schedule-default-policy-title">Default Policy</h2>
              <p>Used when a person, vehicle, gate, or garage door has no schedule assigned.</p>
            </div>
          </div>
          <div className="schedule-policy-actions" role="group" aria-label="No schedule default policy">
            <button
              aria-label="Always Allow"
              aria-pressed={defaultPolicy === "allow"}
              className={defaultPolicy === "allow" ? "schedule-policy-option active allow" : "schedule-policy-option allow"}
              disabled={accessSettings.loading || policySaving}
              onClick={() => updateDefaultPolicy("allow")}
              type="button"
            >
              <CheckCircle2 size={16} />
              <span className="policy-label-full">Always Allow</span>
              <span className="policy-label-short">Allow</span>
            </button>
            <button
              aria-label="Never Allow"
              aria-pressed={defaultPolicy === "deny"}
              className={defaultPolicy === "deny" ? "schedule-policy-option active deny" : "schedule-policy-option deny"}
              disabled={accessSettings.loading || policySaving}
              onClick={() => updateDefaultPolicy("deny")}
              type="button"
            >
              <Lock size={16} />
              <span className="policy-label-full">Never Allow</span>
              <span className="policy-label-short">Deny</span>
            </button>
          </div>
          <div className="schedule-policy-status">
            {policySaving ? (
              <Badge tone="gray">Saving</Badge>
            ) : policySaved ? (
              <span className="schedule-policy-saved-pill">
                <Badge tone="green">Saved</Badge>
              </span>
            ) : null}
          </div>
        </section>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}
      {accessSettings.error ? <div className="auth-error inline-error">{accessSettings.error}</div> : null}

      <div className="schedule-card-grid">
        {filtered.length ? filtered.map((schedule) => (
          <article className="card schedule-card" key={schedule.id}>
            <button className="schedule-card-main" onClick={() => openEdit(schedule)} type="button">
              <div className="schedule-card-icon">
                <Clock3 size={18} />
              </div>
              <div className="schedule-card-copy">
                <strong>{schedule.name}</strong>
                <span>{schedule.description || scheduleSummary(schedule.time_blocks)}</span>
              </div>
              <Badge tone={scheduleHasBlocks(schedule.time_blocks) ? "green" : "amber"}>
                {scheduleSummary(schedule.time_blocks)}
              </Badge>
            </button>
            <div className="schedule-card-days" aria-hidden="true">
              {scheduleDays.map((day, index) => (
                <span
                  className={scheduleDayHasBlocks(schedule.time_blocks, index) ? "active" : ""}
                  key={day}
                >
                  {day.slice(0, 1)}
                </span>
              ))}
            </div>
            <div className="schedule-card-actions">
              <button className="secondary-button" onClick={() => openEdit(schedule)} type="button">
                <CalendarDays size={15} /> Edit
              </button>
              <button className="icon-button danger" onClick={() => deleteSchedule(schedule)} type="button" aria-label={`Delete ${schedule.name}`}>
                <Trash2 size={15} />
              </button>
            </div>
          </article>
        )) : (
          <div className="card schedule-empty-card">
            <EmptyState icon={Clock3} label="No schedules match this view" />
          </div>
        )}
      </div>

      {modalOpen ? (
        <ScheduleModal
          mode={selectedSchedule ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await refresh();
            closeModal();
          }}
          schedule={selectedSchedule}
          setPageError={setError}
        />
      ) : null}
    </section>
  );
}

export function ScheduleModal({
  mode,
  onClose,
  onSaved,
  schedule,
  setPageError
}: {
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  schedule: Schedule | null;
  setPageError: (message: string) => void;
}) {
  const [form, setForm] = React.useState({
    name: schedule?.name ?? "",
    description: schedule?.description ?? "",
    time_blocks: normalizeScheduleBlocks(schedule?.time_blocks ?? emptyScheduleBlocks())
  });
  const [dependencies, setDependencies] = React.useState<ScheduleDependencies | null>(null);
  const [dependenciesLoading, setDependenciesLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  React.useEffect(() => {
    if (!schedule) {
      setDependencies(null);
      return;
    }
    setDependenciesLoading(true);
    api.get<ScheduleDependencies>(`/api/v1/schedules/${schedule.id}/dependencies`)
      .then(setDependencies)
      .catch(() => setDependencies(null))
      .finally(() => setDependenciesLoading(false));
  }, [schedule]);

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setPageError("");
    setSubmitting(true);
    const payload = {
      name: form.name,
      description: form.description || null,
      time_blocks: normalizeScheduleBlocks(form.time_blocks)
    };
    try {
      if (mode === "edit" && schedule) {
        await api.patch<Schedule>(`/api/v1/schedules/${schedule.id}`, payload);
      } else {
        await api.post<Schedule>("/api/v1/schedules", payload);
      }
      await onSaved();
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save schedule";
      setError(message);
      setPageError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card schedule-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Schedule" : "New Schedule"}</h2>
            <p>{scheduleSummary(form.time_blocks)}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}

        <div className="schedule-modal-grid">
          <div className="schedule-details-panel">
            <label className="field">
              <span>Schedule name</span>
              <div className="field-control">
                <Clock3 size={17} />
                <input value={form.name} onChange={(event) => update("name", event.target.value)} required />
              </div>
            </label>
            <label className="field">
              <span>Description</span>
              <textarea value={form.description} onChange={(event) => update("description", event.target.value)} />
            </label>
            <ScheduleDependencyPanel dependencies={dependencies} loading={dependenciesLoading} />
          </div>

          <WeeklyScheduleGrid
            value={form.time_blocks}
            onChange={(timeBlocks) => update("time_blocks", timeBlocks)}
          />
        </div>

        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            <Save size={16} />
            {submitting ? "Saving..." : mode === "edit" ? "Save Schedule" : "Create Schedule"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function ScheduleDependencyPanel({
  dependencies,
  loading
}: {
  dependencies: ScheduleDependencies | null;
  loading: boolean;
}) {
  const items = dependencies ? [
    ...dependencies.people.map((item) => ({ ...item, tone: "blue" as BadgeTone })),
    ...dependencies.vehicles.map((item) => ({ ...item, tone: "green" as BadgeTone })),
    ...dependencies.doors.map((item) => ({ ...item, tone: "amber" as BadgeTone }))
  ] : [];

  return (
    <section className="schedule-dependencies">
      <div className="panel-header">
        <h2>In Use By</h2>
        <Badge tone={items.length ? "blue" : "gray"}>{loading ? "loading" : String(items.length)}</Badge>
      </div>
      {loading ? (
        <div className="schedule-dependency-empty">Loading dependencies</div>
      ) : items.length ? (
        <div className="schedule-dependency-list">
          {items.map((item) => (
            <span className={`schedule-dependency-pill ${item.tone}`} key={`${item.kind}-${item.id}`}>
              {dependencyIcon(item.kind)}
              <span>{item.name}</span>
            </span>
          ))}
        </div>
      ) : (
        <div className="schedule-dependency-empty">No assignments</div>
      )}
    </section>
  );
}

export function dependencyIcon(kind: string) {
  if (kind === "vehicle") return <Car size={13} />;
  if (kind === "gate" || kind === "garage_door") return <Warehouse size={13} />;
  return <UserRound size={13} />;
}

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

export function scheduleCellFromPoint(
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

export function selectedSlotRange(slots: Set<string>, day: number, slot: number): ScheduleCopiedBlock | null {
  if (!slots.has(scheduleSlotKey(day, slot))) return null;
  let startSlot = slot;
  let endSlot = slot;
  while (startSlot > 0 && slots.has(scheduleSlotKey(day, startSlot - 1))) startSlot -= 1;
  while (endSlot < scheduleSlotCount - 1 && slots.has(scheduleSlotKey(day, endSlot + 1))) endSlot += 1;
  return { startSlot, endSlot };
}

export function scheduleContextMenuPoint(clientX: number, clientY: number) {
  const menuWidth = 244;
  const menuHeight = 292;
  return {
    x: Math.max(12, Math.min(clientX, window.innerWidth - menuWidth - 12)),
    y: Math.max(12, Math.min(clientY, window.innerHeight - menuHeight - 12))
  };
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

export function parseScheduleTime(value: string) {
  if (value === "24:00" || value === "23:59") return 24 * 60;
  const [hours, minutes] = value.split(":").map((part) => Number(part));
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  return hours * 60 + minutes;
}

export function formatScheduleMinute(value: number) {
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
