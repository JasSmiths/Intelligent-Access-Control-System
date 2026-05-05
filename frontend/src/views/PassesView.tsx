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
  AuditLog,
  BadgeTone,
  EmptyState,
  formatDate,
  fromDateTimeLocal,
  initials,
  isRecord,
  levelTone,
  matches,
  numberPayload,
  RealtimeMessage,
  scheduleDays,
  stringPayload,
  titleCase,
  toDateTimeLocal,
  TooltipPositionState
} from "../shared";



export type VisitorPassStatus = "active" | "scheduled" | "used" | "expired" | "cancelled";

export type VisitorPassType = "one-time" | "duration";

export type VisitorPass = {
  id: string;
  visitor_name: string;
  pass_type: VisitorPassType;
  visitor_phone: string | null;
  expected_time: string;
  window_minutes: number;
  valid_from: string | null;
  valid_until: string | null;
  window_start: string;
  window_end: string;
  status: VisitorPassStatus;
  creation_source: string;
  source_reference: string | null;
  source_metadata: Record<string, unknown> | null;
  whatsapp_status: string | null;
  whatsapp_status_label: string | null;
  whatsapp_status_detail: string | null;
  created_by_user_id: string | null;
  created_by: string | null;
  arrival_time: string | null;
  departure_time: string | null;
  number_plate: string | null;
  vehicle_make: string | null;
  vehicle_colour: string | null;
  duration_on_site_seconds: number | null;
  duration_human: string | null;
  arrival_event_id: string | null;
  departure_event_id: string | null;
  telemetry_trace_id: string | null;
  created_at: string;
  updated_at: string;
};

export type VisitorPassWhatsAppMessage = {
  id: string;
  direction: "inbound" | "outbound" | "status";
  kind: string;
  body: string;
  actor_label: string;
  provider_message_id: string | null;
  status: string | null;
  created_at: string;
  metadata: Record<string, unknown> | null;
};

export type VisitorPassWhatsAppSendResponse = {
  visitor_pass: VisitorPass;
  message: VisitorPassWhatsAppMessage;
};

export type VisitorPassLogEntry = AuditLog & {
  actor_user_label: string | null;
};

export const visitorPassStatuses: VisitorPassStatus[] = ["active", "scheduled", "used", "expired", "cancelled"];

export const visitorPassTypes: VisitorPassType[] = ["one-time", "duration"];

export const defaultVisitorPassFilters = new Set<VisitorPassStatus>(["active", "scheduled"]);

export const visitorPassWindowOptions = [30, 60, 90, 120, 180];

export function PassesView({ query, realtime, refreshToken }: { query: string; realtime: RealtimeMessage[]; refreshToken: number }) {
  const [passes, setPasses] = React.useState<VisitorPass[]>([]);
  const [filters, setFilters] = React.useState<Set<VisitorPassStatus>>(() => new Set(defaultVisitorPassFilters));
  const [modalPass, setModalPass] = React.useState<VisitorPass | null>(null);
  const [modalOpen, setModalOpen] = React.useState(false);
  const [detailPass, setDetailPass] = React.useState<VisitorPass | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const loadPasses = React.useCallback(async (options?: { showLoading?: boolean }) => {
    const showLoading = options?.showLoading !== false;
    const params = new URLSearchParams();
    if (filters.size && filters.size < visitorPassStatuses.length) {
      filters.forEach((status) => params.append("status", status));
    }
    if (query.trim()) params.set("q", query.trim());
    const suffix = params.toString() ? `?${params.toString()}` : "";
    if (showLoading) setLoading(true);
    setError("");
    try {
      setPasses(await api.get<VisitorPass[]>(`/api/v1/visitor-passes${suffix}`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load Visitor Passes");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [filters, query]);

  React.useEffect(() => {
    loadPasses().catch(() => undefined);
  }, [loadPasses]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    loadPasses({ showLoading: false }).catch(() => undefined);
  }, [loadPasses, refreshToken]);

  React.useEffect(() => {
    const latest = realtime[0];
    if (!latest) return;
    if (isVisitorPassRealtimeEvent(latest)) {
      if (latest.type === "visitor_pass.deleted") {
        const deletedId = isRecord(latest.payload.visitor_pass) ? stringPayload(latest.payload.visitor_pass.id) : "";
        if (deletedId) {
          setPasses((current) => current.filter((item) => item.id !== deletedId));
          setDetailPass((current) => current?.id === deletedId ? null : current);
          setModalPass((current) => current?.id === deletedId ? null : current);
        }
        loadPasses({ showLoading: false }).catch(() => undefined);
        return;
      }
      const livePass = visitorPassFromRealtime(latest);
      if (livePass) {
        setPasses((current) => [livePass, ...current.filter((item) => item.id !== livePass.id)]);
        setDetailPass((current) => current?.id === livePass.id ? livePass : current);
        setModalPass((current) => current?.id === livePass.id ? livePass : current);
        return;
      }
      loadPasses({ showLoading: false }).catch(() => undefined);
    } else if (latest.type === "access_event.finalized") {
      loadPasses({ showLoading: false }).catch(() => undefined);
    }
  }, [realtime, loadPasses]);

  const openCreate = () => {
    setModalPass(null);
    setModalOpen(true);
  };

  const openEdit = (visitorPass: VisitorPass) => {
    setModalPass(visitorPass);
    setModalOpen(true);
  };

  const openDetails = (visitorPass: VisitorPass) => {
    setDetailPass(visitorPass);
  };

  const closeModal = () => {
    setModalOpen(false);
    setModalPass(null);
  };

  const handlePassUpdated = React.useCallback(async (visitorPass: VisitorPass) => {
    setPasses((current) => [visitorPass, ...current.filter((item) => item.id !== visitorPass.id)]);
    setDetailPass(visitorPass);
    await loadPasses();
  }, [loadPasses]);

  const cancelPass = async (visitorPass: VisitorPass): Promise<VisitorPass | null> => {
    setError("");
    try {
      const cancelled = await api.post<VisitorPass>(`/api/v1/visitor-passes/${visitorPass.id}/cancel`, { reason: "Cancelled from dashboard" });
      await handlePassUpdated(cancelled);
      return cancelled;
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : "Unable to cancel Visitor Pass");
      return null;
    }
  };

  const deletePass = async (visitorPass: VisitorPass): Promise<boolean> => {
    setError("");
    try {
      await api.delete(`/api/v1/visitor-passes/${visitorPass.id}`);
      setPasses((current) => current.filter((item) => item.id !== visitorPass.id));
      setDetailPass(null);
      await loadPasses();
      return true;
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Unable to delete Visitor Pass");
      return false;
    }
  };

  const visiblePasses = passes.filter((visitorPass) => visitorPassMatchesStatus(visitorPass, filters) && visitorPassMatches(visitorPass, query));
  const counts = React.useMemo(() => visitorPassStatuses.reduce<Record<VisitorPassStatus, number>>((acc, status) => {
    acc[status] = passes.filter((visitorPass) => visitorPass.status === status).length;
    return acc;
  }, { active: 0, scheduled: 0, used: 0, expired: 0, cancelled: 0 }), [passes]);

  return (
    <section className="view-stack passes-page">
      <div className="users-hero passes-hero card">
        <div>
          <span className="eyebrow">Anticipatory Access</span>
          <h1>Passes</h1>
          <p>One-shot visitor windows for unknown vehicles, with captured arrival, vehicle, and duration telemetry.</p>
        </div>
        <button className="primary-button" onClick={openCreate} type="button">
          <Plus size={17} /> Visitor Pass
        </button>
      </div>

      <div className="passes-toolbar card">
        <PassFilterBar counts={counts} filters={filters} onChange={setFilters} />
        <button className="secondary-button" onClick={() => loadPasses()} type="button">
          <RefreshCw size={15} /> Refresh
        </button>
      </div>

      {error ? <div className="auth-error inline-error">{error}</div> : null}

      {loading ? (
        <div className="card passes-loading">
          <Loader2 className="spin" size={18} /> Loading Visitor Passes
        </div>
      ) : visiblePasses.length ? (
        <div className="visitor-pass-grid">
          <AnimatePresence initial={false}>
            {visiblePasses.map((visitorPass) => (
              <VisitorPassCard
                key={visitorPass.id}
                onOpen={openDetails}
                visitorPass={visitorPass}
              />
            ))}
          </AnimatePresence>
        </div>
      ) : (
        <div className="card passes-empty-card">
          <EmptyState icon={ClipboardPaste} label="No Visitor Passes match this view" />
        </div>
      )}

      {modalOpen ? (
        <VisitorPassModal
          mode={modalPass ? "edit" : "create"}
          onClose={closeModal}
          onSaved={async () => {
            await loadPasses();
            closeModal();
          }}
          visitorPass={modalPass}
        />
      ) : null}

      {detailPass ? (
        <VisitorPassDetailsModal
          onCancel={cancelPass}
          onClose={() => setDetailPass(null)}
          onDelete={deletePass}
          onEdit={(visitorPass) => {
            setDetailPass(null);
            openEdit(visitorPass);
          }}
          onUpdated={handlePassUpdated}
          visitorPass={detailPass}
        />
      ) : null}
    </section>
  );
}

export function PassFilterBar({
  filters,
  counts,
  onChange
}: {
  filters: Set<VisitorPassStatus>;
  counts: Record<VisitorPassStatus, number>;
  onChange: (filters: Set<VisitorPassStatus>) => void;
}) {
  const allSelected = filters.size === visitorPassStatuses.length;
  const toggleStatus = (status: VisitorPassStatus) => {
    const next = new Set(filters);
    if (next.has(status)) {
      next.delete(status);
    } else {
      next.add(status);
    }
    onChange(next.size ? next : new Set(defaultVisitorPassFilters));
  };
  return (
    <div className="pass-filter-bar" role="group" aria-label="Visitor Pass status filters">
      <button className={allSelected ? "active" : ""} onClick={() => onChange(new Set(visitorPassStatuses))} type="button">
        All
      </button>
      {visitorPassStatuses.map((status) => (
        <button
          aria-pressed={filters.has(status)}
          className={filters.has(status) ? "active" : ""}
          key={status}
          onClick={() => toggleStatus(status)}
          type="button"
        >
          {titleCase(status)}
          <span>{counts[status]}</span>
        </button>
      ))}
    </div>
  );
}

export function VisitorPassCard({
  visitorPass,
  onOpen
}: {
  visitorPass: VisitorPass;
  onOpen: (visitorPass: VisitorPass) => void;
}) {
  const vehicleSummary = visitorPassVehicleSummary(visitorPass);
  const windowLabel = visitorPassWindowLabel(visitorPass);
  const sourceLabel = visitorPassSourceLabel(visitorPass.creation_source);
  const isDuration = visitorPass.pass_type === "duration";
  const visitDuration = visitorPassVisitDurationLabel(visitorPass);
  const passDuration = visitorPassPassDurationLabel(visitorPass);
  const subtitle = isDuration ? formatDate(visitorPass.window_start) : `${formatDate(visitorPass.window_start)} · ${windowLabel}`;
  const vehicleMeta = [visitorPass.vehicle_colour, visitorPass.vehicle_make].filter(Boolean).join(" ");
  const vehiclePrimary = visitorPass.number_plate || vehicleSummary || "Pending";
  const vehicleSecondary = vehicleMeta || "Vehicle";
  return (
    <motion.article
      className={`card visitor-pass-card ${visitorPass.status}`}
      layout
      initial={{ opacity: 0, y: 8, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.985 }}
      onClick={() => onOpen(visitorPass)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(visitorPass);
        }
      }}
      role="button"
      tabIndex={0}
      transition={{ duration: 0.18, ease: "easeOut", layout: { duration: 0.22 } }}
    >
      <div className="visitor-pass-card-head">
        <div className="visitor-pass-icon">
          <Ticket size={18} />
        </div>
        <div>
          <strong>{visitorPass.visitor_name}</strong>
          <span className="visitor-pass-card-subtitle">{subtitle}</span>
        </div>
        <VisitorPassStatusPill showIcon={false} status={visitorPass.status} visitorPass={visitorPass} />
      </div>

      <div className="visitor-pass-window">
        <div className="visitor-pass-window-row">
          <Clock3 size={15} />
          <span>{formatDate(visitorPass.window_start)} to {formatDate(visitorPass.window_end)}</span>
        </div>
        <div className="visitor-pass-window-row">
          <GitBranch size={15} />
          <span>
            {sourceLabel}
            {visitorPass.visitor_phone ? ` · +${visitorPass.visitor_phone}` : visitorPass.created_by ? ` · ${visitorPass.created_by}` : ""}
          </span>
        </div>
      </div>

      <VisitorPassMoreInfo visitorPass={visitorPass} />

      <section className="visitor-pass-card-stats">
        <div className="visitor-pass-card-stat">
          <span className="visitor-pass-stat-icon vehicle">
            <Car size={19} />
          </span>
          <div>
            <strong>{vehiclePrimary}</strong>
            <span>{vehicleSecondary}</span>
          </div>
        </div>
        <div className="visitor-pass-card-stat">
          <span className="visitor-pass-stat-icon duration">
            <Clock3 size={19} />
          </span>
          <div>
            <strong>{visitDuration || passDuration || "Pending"}</strong>
            <span>Duration</span>
          </div>
        </div>
      </section>
    </motion.article>
  );
}

export function VisitorPassStatusPill({
  status,
  visitorPass,
  showIcon = true
}: {
  status: VisitorPassStatus;
  visitorPass?: VisitorPass;
  showIcon?: boolean;
}) {
  const Icon = status === "scheduled" || status === "active" ? Clock3 : status === "used" ? CheckCircle2 : status === "cancelled" ? X : CircleDot;
  const tone = visitorPass ? visitorPassStatusPillTone(visitorPass) : visitorPassBaseStatusTone(status);
  return (
    <span className={`visitor-pass-status-pill ${status} tone-${tone}`}>
      {showIcon ? <Icon size={18} /> : null}
      <span className="visitor-pass-status-label">{titleCase(status)}</span>
    </span>
  );
}

export function VisitorPassAvatar({ visitorPass }: { visitorPass: VisitorPass }) {
  const initials = visitorPassInitials(visitorPass.visitor_name);
  return (
    <span className="visitor-pass-avatar" aria-hidden="true">
      {initials || <ClipboardPaste size={24} />}
    </span>
  );
}

export function VisitorPassDetailTile({
  icon: Icon,
  tone,
  label,
  value,
  detail
}: {
  icon: React.ElementType;
  tone: "blue" | "green" | "amber" | "purple";
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className={`visitor-pass-detail-tile ${tone}`}>
      <span className="visitor-pass-detail-tile-icon">
        <Icon size={24} />
      </span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}

export function WhatsAppIcon({ size = 24, ...props }: React.SVGProps<SVGSVGElement> & { size?: number | string }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      viewBox="0 0 24 24"
      width={size}
      {...props}
    >
      <path d="M4.8 19.2l.9-3.25a7.6 7.6 0 1 1 2.72 2.62l-3.62.63Z" />
      <path d="M9.3 8.55c.22-.48.4-.52.68-.52h.5c.17 0 .35.04.47.34l.64 1.55c.1.27.06.43-.1.62l-.37.43c-.12.14-.16.27-.07.45.33.66.9 1.25 1.48 1.65.42.29.78.48.98.55.18.06.32.03.45-.12l.57-.65c.16-.18.34-.24.58-.14l1.53.7c.27.12.36.3.31.55-.08.48-.4 1.03-.8 1.27-.45.27-1.1.34-1.93.09-1.02-.31-2.12-.93-3.22-2.02-1.1-1.1-1.78-2.22-2.08-3.18-.25-.82-.17-1.23.08-1.57Z" />
    </svg>
  );
}

export function VisitorPassMoreInfo({ visitorPass }: { visitorPass: VisitorPass }) {
  const state = visitorPassMoreInfoState(visitorPass);
  const tooltip = visitorPassWhatsAppStatusTooltip(visitorPass);
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
    if (!tooltip) return;
    const rect = target.getBoundingClientRect();
    const tooltipWidth = Math.min(320, window.innerWidth - 24);
    const estimatedHeight = Math.min(168, 58 + Math.ceil(tooltip.body.length / 48) * 18);
    const gap = 10;
    const placement = rect.bottom + gap + estimatedHeight > window.innerHeight - 8 ? "top" : "bottom";
    const left = Math.max(12 + tooltipWidth / 2, Math.min(rect.left + rect.width / 2, window.innerWidth - tooltipWidth / 2 - 12));
    const top = placement === "bottom"
      ? rect.bottom + gap
      : Math.max(12, rect.top - estimatedHeight - gap);
    setTooltipPosition({ left, placement, top });
  };

  if (!state) return null;
  const Icon = state.icon;

  return (
    <motion.div
      aria-describedby={tooltip && tooltipPosition ? tooltipId : undefined}
      aria-label={tooltip ? `${state.label}. ${tooltip.body}` : state.label}
      className={`visitor-pass-more-info ${state.tone} ${visitorPass.whatsapp_status ?? ""}${tooltip ? " has-tooltip" : ""}`}
      layout
      onClick={tooltip ? (event) => event.stopPropagation() : undefined}
      onBlur={tooltip ? () => setTooltipPosition(null) : undefined}
      onFocus={tooltip ? (event) => showTooltip(event.currentTarget) : undefined}
      onKeyDown={tooltip ? (event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          setTooltipPosition(null);
        }
      } : undefined}
      onMouseEnter={tooltip ? (event) => showTooltip(event.currentTarget) : undefined}
      onMouseLeave={tooltip ? () => setTooltipPosition(null) : undefined}
      tabIndex={tooltip ? 0 : undefined}
      transition={{ duration: 0.18, ease: "easeOut", layout: { duration: 0.2 } }}
    >
      <Icon className={state.spinning ? "spin" : undefined} size={15} />
      <strong>{state.label}</strong>
      {tooltip ? <AlertTriangle size={15} /> : <ChevronRight size={15} />}
      {tooltip && tooltipPosition ? createPortal(
        <span
          className={`iacs-tooltip visitor-pass-error-tooltip ${tooltipPosition.placement}`}
          id={tooltipId}
          role="tooltip"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          <strong>{tooltip.title}</strong>
          <span>{tooltip.body}</span>
        </span>,
        document.body
      ) : null}
    </motion.div>
  );
}

export function VisitorPassDetailsModal({
  visitorPass,
  onClose,
  onEdit,
  onCancel,
  onDelete,
  onUpdated
}: {
  visitorPass: VisitorPass;
  onClose: () => void;
  onEdit: (visitorPass: VisitorPass) => void;
  onCancel: (visitorPass: VisitorPass) => Promise<VisitorPass | null>;
  onDelete: (visitorPass: VisitorPass) => Promise<boolean>;
  onUpdated: (visitorPass: VisitorPass) => Promise<void>;
}) {
  const [activeTab, setActiveTab] = React.useState<"details" | "whatsapp" | "log">("details");
  const [messages, setMessages] = React.useState<VisitorPassWhatsAppMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = React.useState(false);
  const [messagesLoaded, setMessagesLoaded] = React.useState(false);
  const [messagesError, setMessagesError] = React.useState("");
  const [logs, setLogs] = React.useState<VisitorPassLogEntry[]>([]);
  const [logsLoading, setLogsLoading] = React.useState(false);
  const [logsLoaded, setLogsLoaded] = React.useState(false);
  const [logsError, setLogsError] = React.useState("");
  const [messageDraft, setMessageDraft] = React.useState("");
  const [messageSending, setMessageSending] = React.useState(false);
  const [messageSendError, setMessageSendError] = React.useState("");
  const [visitorUnblocking, setVisitorUnblocking] = React.useState(false);
  const [visitorUnblockError, setVisitorUnblockError] = React.useState("");
  const [action, setAction] = React.useState<"cancel" | "delete" | null>(null);
  const [confirmAction, setConfirmAction] = React.useState<"cancel" | "delete" | null>(null);
  const threadRef = React.useRef<HTMLDivElement | null>(null);
  const latestMessageCountRef = React.useRef(0);
  const shouldStickToLatestRef = React.useRef(true);
  const reduceMotion = useReducedMotion();
  const isDuration = visitorPass.pass_type === "duration";
  const canModify = visitorPass.status === "active" || visitorPass.status === "scheduled";
  const vehicleValue = [visitorPass.vehicle_colour, visitorPass.vehicle_make].filter(Boolean).join(" ") || visitorPass.number_plate || "Vehicle details pending";
  const vehicleDetail = visitorPass.number_plate ? `Registration ${visitorPass.number_plate}` : "Plate pending";
  const sourceLabel = visitorPassSourceLabel(visitorPass.creation_source);
  const visitDuration = visitorPassVisitDurationLabel(visitorPass);
  const passDuration = visitorPassPassDurationLabel(visitorPass);
  const whatsappStatusLabel = visitorPassWhatsAppDetailLabel(visitorPass);
  const abuseCooldown = visitorPassWhatsAppAbuseCooldown(visitorPass);
  const visitDetail = visitorPass.departure_time
    ? `Left ${formatDate(visitorPass.departure_time)}`
    : visitorPass.arrival_time
      ? `Arrived ${formatDate(visitorPass.arrival_time)}`
      : passDuration
        ? "Pass window"
        : "No visit telemetry";
  const telemetryLinked = Boolean(visitorPass.arrival_event_id || visitorPass.departure_event_id);
  const trimmedMessageDraft = messageDraft.trim();
  const canSendWhatsAppMessage = Boolean(isDuration && canModify && trimmedMessageDraft && !messageSending);
  const canUnblockVisitor = Boolean(isDuration && activeTab === "whatsapp" && abuseCooldown && !visitorUnblocking);

  const loadWhatsAppMessages = React.useCallback(async (showLoading = false) => {
    if (!isDuration) return;
    if (showLoading) setMessagesLoading(true);
    setMessagesError("");
    try {
      const rows = await api.get<VisitorPassWhatsAppMessage[]>(`/api/v1/visitor-passes/${visitorPass.id}/whatsapp-messages`);
      const nextMessages = rows.map(visitorPassWhatsAppMessageFromApi);
      setMessages((current) => visitorPassWhatsAppMessagesEqual(current, nextMessages) ? current : nextMessages);
      setMessagesLoaded(true);
    } catch (historyError) {
      setMessagesError(historyError instanceof Error ? historyError.message : "Unable to load WhatsApp history");
    } finally {
      if (showLoading) setMessagesLoading(false);
    }
  }, [isDuration, visitorPass.id]);

  const loadLogs = React.useCallback(async (showLoading = false) => {
    if (showLoading) setLogsLoading(true);
    setLogsError("");
    try {
      const rows = await api.get<VisitorPassLogEntry[]>(`/api/v1/visitor-passes/${visitorPass.id}/logs`);
      setLogs((current) => visitorPassLogsEqual(current, rows) ? current : rows);
      setLogsLoaded(true);
    } catch (logError) {
      setLogsError(logError instanceof Error ? logError.message : "Unable to load Visitor Pass log");
    } finally {
      if (showLoading) setLogsLoading(false);
    }
  }, [visitorPass.id]);

  React.useEffect(() => {
    setActiveTab("details");
    setMessages([]);
    setMessagesError("");
    setMessagesLoaded(false);
    setLogs([]);
    setLogsError("");
    setLogsLoaded(false);
    setMessageDraft("");
    setMessageSendError("");
    setMessageSending(false);
    setVisitorUnblockError("");
    setVisitorUnblocking(false);
    latestMessageCountRef.current = 0;
    shouldStickToLatestRef.current = true;
  }, [visitorPass.id]);

  React.useEffect(() => {
    if (activeTab !== "whatsapp" || !isDuration) return undefined;
    loadWhatsAppMessages(!messagesLoaded).catch(() => undefined);
    const interval = window.setInterval(() => {
      loadWhatsAppMessages(false).catch(() => undefined);
    }, 3500);
    return () => {
      window.clearInterval(interval);
    };
  }, [activeTab, isDuration, loadWhatsAppMessages, messagesLoaded, visitorPass.updated_at]);

  React.useEffect(() => {
    if (activeTab !== "log") return undefined;
    loadLogs(!logsLoaded).catch(() => undefined);
    const interval = window.setInterval(() => {
      loadLogs(false).catch(() => undefined);
    }, 5000);
    return () => {
      window.clearInterval(interval);
    };
  }, [activeTab, loadLogs, logsLoaded, visitorPass.updated_at]);

  React.useEffect(() => {
    if (activeTab !== "whatsapp") return;
    window.requestAnimationFrame(() => {
      if (threadRef.current) {
        threadRef.current.scrollTop = threadRef.current.scrollHeight;
      }
    });
  }, [activeTab]);

  React.useLayoutEffect(() => {
    if (activeTab !== "whatsapp") return;
    const thread = threadRef.current;
    if (!thread) return;
    const previousCount = latestMessageCountRef.current;
    const nextCount = messages.length;
    latestMessageCountRef.current = nextCount;
    if (!nextCount) return;
    window.requestAnimationFrame(() => {
      if (!threadRef.current) return;
      if (previousCount === 0) {
        threadRef.current.scrollTop = threadRef.current.scrollHeight;
        return;
      }
      if (nextCount > previousCount && shouldStickToLatestRef.current) {
        threadRef.current.scrollTo({
          top: threadRef.current.scrollHeight,
          behavior: reduceMotion ? "auto" : "smooth"
        });
      }
    });
  }, [activeTab, messages.length, reduceMotion]);

  const updateStickiness = React.useCallback(() => {
    const thread = threadRef.current;
    if (!thread) return;
    shouldStickToLatestRef.current = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 72;
  }, []);

  const confirmVisitorPassAction = async () => {
    if (!confirmAction) return;
    setAction(confirmAction);
    try {
      if (confirmAction === "cancel") {
        await onCancel(visitorPass);
        setConfirmAction(null);
        return;
      }
      const deleted = await onDelete(visitorPass);
      if (!deleted) {
        setAction(null);
        return;
      }
    } finally {
      if (confirmAction === "cancel") setAction(null);
    }
  };

  const sendWhatsAppMessage = async (event?: React.FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    if (!canSendWhatsAppMessage) return;
    setMessageSending(true);
    setMessageSendError("");
    shouldStickToLatestRef.current = true;
    try {
      const result = await api.post<VisitorPassWhatsAppSendResponse>(
        `/api/v1/visitor-passes/${visitorPass.id}/whatsapp-messages`,
        { message: trimmedMessageDraft }
      );
      const sentMessage = visitorPassWhatsAppMessageFromApi(result.message);
      setMessages((current) => visitorPassWhatsAppMessagesWithMessage(current, sentMessage));
      setMessagesLoaded(true);
      setMessageDraft("");
      await onUpdated(result.visitor_pass);
    } catch (sendError) {
      setMessageSendError(sendError instanceof Error ? sendError.message : "Unable to send WhatsApp message");
    } finally {
      setMessageSending(false);
    }
  };

  const unblockVisitorWhatsApp = async () => {
    if (!canUnblockVisitor) return;
    setVisitorUnblocking(true);
    setVisitorUnblockError("");
    try {
      const updatedPass = await api.post<VisitorPass>(`/api/v1/visitor-passes/${visitorPass.id}/whatsapp-unblock`, {});
      await onUpdated(updatedPass);
      await loadWhatsAppMessages(false);
    } catch (unblockError) {
      setVisitorUnblockError(unblockError instanceof Error ? unblockError.message : "Unable to unblock Visitor Concierge replies");
    } finally {
      setVisitorUnblocking(false);
    }
  };

  return (
    <>
      <div className="modal-backdrop" role="presentation">
        <div className="modal-card visitor-pass-detail-modal" role="dialog" aria-modal="true" aria-labelledby="visitor-pass-detail-title">
          <div className="modal-header visitor-pass-detail-header">
            <VisitorPassAvatar visitorPass={visitorPass} />
            <div className="visitor-pass-detail-title">
              <span>{visitorPass.pass_type === "duration" ? "Duration Pass" : "Visitor Pass"}</span>
              <h2 id="visitor-pass-detail-title">{visitorPass.visitor_name}</h2>
              <p><CalendarDays size={17} /> {formatDate(visitorPass.window_start)} to {formatDate(visitorPass.window_end)}</p>
            </div>
            <div className="visitor-pass-detail-header-actions">
              <VisitorPassStatusPill status={visitorPass.status} />
              {activeTab === "whatsapp" && abuseCooldown ? (
                <button
                  aria-label={`Unblock WhatsApp replies for ${visitorPass.visitor_name}`}
                  className="secondary-button visitor-pass-unblock-button"
                  disabled={!canUnblockVisitor}
                  onClick={unblockVisitorWhatsApp}
                  title={`Visitor Concierge replies are paused until ${formatDate(abuseCooldown.until)}`}
                  type="button"
                >
                  {visitorUnblocking ? <Loader2 className="spin" size={15} /> : <Unlock size={15} />}
                  <span>Unblock</span>
                </button>
              ) : null}
              <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
                <X size={16} />
              </button>
            </div>
          </div>

          <div className="visitor-pass-detail-tabs" role="tablist" aria-label="Visitor Pass details">
            <button className={activeTab === "details" ? "active" : ""} onClick={() => setActiveTab("details")} type="button" role="tab" aria-selected={activeTab === "details"}>
              Details
            </button>
            {isDuration ? (
              <button className={activeTab === "whatsapp" ? "active" : ""} onClick={() => setActiveTab("whatsapp")} type="button" role="tab" aria-selected={activeTab === "whatsapp"}>
                WhatsApp
              </button>
            ) : null}
            <button className={activeTab === "log" ? "active" : ""} onClick={() => setActiveTab("log")} type="button" role="tab" aria-selected={activeTab === "log"}>
              Log
            </button>
          </div>

          {activeTab === "whatsapp" && isDuration ? (
            <section className="visitor-pass-whatsapp-panel">
              {messagesError ? <div className="auth-error">{messagesError}</div> : null}
              {messageSendError ? <div className="auth-error">{messageSendError}</div> : null}
              {visitorUnblockError ? <div className="auth-error">{visitorUnblockError}</div> : null}
              <div className="visitor-pass-whatsapp-thread" ref={threadRef} onScroll={updateStickiness}>
                {messagesLoading && !messages.length ? (
                  <div className="visitor-pass-thread-empty">
                    <Loader2 className="spin" size={17} /> Loading WhatsApp history
                  </div>
                ) : messages.length ? (
                  <AnimatePresence initial={false}>
                    {messages.map((message) => <VisitorPassWhatsAppBubble key={message.id} message={message} />)}
                  </AnimatePresence>
                ) : (
                  <div className="visitor-pass-thread-empty">No WhatsApp messages recorded for this pass yet</div>
                )}
                <form className="visitor-pass-whatsapp-composer" onSubmit={sendWhatsAppMessage}>
                  <Smile size={18} />
                  <input
                    aria-label={`Message ${visitorPass.visitor_name} on WhatsApp`}
                    disabled={!canModify || messageSending}
                    maxLength={1024}
                    onChange={(event) => setMessageDraft(event.target.value)}
                    placeholder={canModify ? "Message..." : "Messaging unavailable for this pass"}
                    value={messageDraft}
                  />
                  <button
                    className="icon-button"
                    disabled={!canSendWhatsAppMessage}
                    type="submit"
                    aria-label="Send WhatsApp message"
                  >
                    {messageSending ? <Loader2 className="spin" size={18} /> : <Send size={18} />}
                  </button>
                </form>
              </div>
            </section>
          ) : activeTab === "log" ? (
            <section className="visitor-pass-log-panel">
              {logsError ? <div className="auth-error">{logsError}</div> : null}
              {logsLoading && !logs.length ? (
                <div className="visitor-pass-thread-empty">
                  <Loader2 className="spin" size={17} /> Loading Visitor Pass log
                </div>
              ) : logs.length ? (
                <VisitorPassLogTimeline logs={logs} visitorPass={visitorPass} />
              ) : (
                <div className="visitor-pass-thread-empty">No changes have been logged for this pass yet</div>
              )}
            </section>
          ) : (
            <section className="visitor-pass-detail-body">
              <div className="visitor-pass-detail-window-card">
                <span className="visitor-pass-window-orb">
                  <Clock3 size={34} />
                </span>
                <div className="visitor-pass-window-times">
                  <span>Window</span>
                  <strong>{formatDate(visitorPass.window_start)}</strong>
                  <small>{visitorPassWindowLabel(visitorPass)}</small>
                </div>
                <ArrowRight className="visitor-pass-window-arrow" size={30} />
                <div className="visitor-pass-window-times">
                  <span>Until</span>
                  <strong>{formatDate(visitorPass.window_end)}</strong>
                  <small>{passDuration || "Window duration pending"}</small>
                </div>
                <div className="visitor-pass-window-source">
                  <span className="visitor-pass-window-source-icon"><UserRound size={20} /></span>
                  <div>
                    <span>Source</span>
                    <strong>{sourceLabel}</strong>
                    <small>{visitorPass.created_by ? `Created by ${visitorPass.created_by}` : formatDate(visitorPass.created_at)}</small>
                  </div>
                </div>
              </div>
              <div className="visitor-pass-detail-grid">
                <VisitorPassDetailTile
                  detail={vehicleDetail}
                  icon={Car}
                  label="Vehicle"
                  tone="green"
                  value={vehicleValue}
                />
                <VisitorPassDetailTile
                  detail={visitDetail}
                  icon={Clock3}
                  label="Duration"
                  tone="amber"
                  value={visitDuration || passDuration || "Not available"}
                />
                {isDuration ? (
                  <VisitorPassDetailTile
                    detail={visitorPass.visitor_phone ? `+${visitorPass.visitor_phone}` : "No phone number"}
                    icon={WhatsAppIcon}
                    label="WhatsApp"
                    tone="green"
                    value={whatsappStatusLabel}
                  />
                ) : null}
                <VisitorPassDetailTile
                  detail={visitorPass.telemetry_trace_id || (telemetryLinked ? "Access events linked" : "No access events linked")}
                  icon={Activity}
                  label="Telemetry"
                  tone="purple"
                  value={visitorPass.telemetry_trace_id ? "Trace linked" : "No trace linked"}
                />
              </div>
            </section>
          )}

          <div className="modal-actions visitor-pass-detail-actions">
            <button className="secondary-button" onClick={onClose} disabled={action !== null} type="button">
              Close
            </button>
            <button className="secondary-button" onClick={() => onEdit(visitorPass)} disabled={!canModify || action !== null} type="button">
              <Pencil size={15} /> Edit
            </button>
            <button className="secondary-button danger" onClick={() => setConfirmAction("cancel")} disabled={!canModify || action !== null} type="button">
              <X size={15} /> {action === "cancel" ? "Cancelling..." : "Cancel pass"}
            </button>
            <button className="danger-button" onClick={() => setConfirmAction("delete")} disabled={action !== null} type="button">
              <Trash2 size={15} /> {action === "delete" ? "Deleting..." : "Delete pass"}
            </button>
          </div>
        </div>
      </div>
      {confirmAction ? (
        <VisitorPassActionConfirmModal
          action={confirmAction}
          loading={action === confirmAction}
          onCancel={() => setConfirmAction(null)}
          onConfirm={confirmVisitorPassAction}
          visitorPass={visitorPass}
        />
      ) : null}
    </>
  );
}

export function VisitorPassWhatsAppBubble({ message }: { message: VisitorPassWhatsAppMessage }) {
  const outbound = message.direction === "outbound";
  if (message.direction === "status") {
    return (
      <motion.div
        className="visitor-pass-whatsapp-row status"
        layout
        initial={{ opacity: 0, y: 8, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 8, scale: 0.98 }}
        transition={{ duration: 0.22, ease: "easeOut" }}
      >
        <div className="visitor-pass-whatsapp-status-card">
          <MessageCircle size={24} />
          <span>
            <strong>{message.body}</strong>
            <small>{formatDate(message.created_at)}</small>
          </span>
        </div>
      </motion.div>
    );
  }
  return (
    <motion.div
      className={`visitor-pass-whatsapp-row ${outbound ? "outbound" : "inbound"}`}
      layout
      initial={{ opacity: 0, y: 12, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.98 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
    >
      <span className={`visitor-pass-whatsapp-avatar ${outbound ? "iacs" : ""}`}>
        {outbound ? <ShieldCheck size={22} /> : message.actor_label.slice(0, 2).toUpperCase()}
      </span>
      <div className="visitor-pass-whatsapp-bubble">
        <span>{message.actor_label}</span>
        <p>{message.body}</p>
        <small>{formatDate(message.created_at)}{outbound ? <Check size={14} /> : null}</small>
      </div>
    </motion.div>
  );
}

export function VisitorPassActionConfirmModal({
  action,
  visitorPass,
  loading,
  onCancel,
  onConfirm
}: {
  action: "cancel" | "delete";
  visitorPass: VisitorPass;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const isDelete = action === "delete";
  return createPortal(
    <div className="modal-backdrop stacked-modal" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="visitor-pass-action-confirm-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className={`gate-confirm-icon ${isDelete ? "danger" : ""}`}>
              {isDelete ? <Trash2 size={20} /> : <X size={20} />}
            </span>
            <div>
              <h2 id="visitor-pass-action-confirm-title">
                {isDelete ? "Delete" : "Cancel"} Visitor Pass?
              </h2>
              <p>
                {isDelete
                  ? `This will permanently delete the pass for ${visitorPass.visitor_name}.`
                  : `This will cancel the active window for ${visitorPass.visitor_name}.`}
              </p>
            </div>
          </div>
        </div>
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Keep pass
          </button>
          <button className={isDelete ? "danger-button" : "secondary-button danger"} disabled={loading} onClick={onConfirm} type="button">
            {isDelete ? <Trash2 size={15} /> : <X size={15} />}
            {loading ? (isDelete ? "Deleting..." : "Cancelling...") : isDelete ? "Delete pass" : "Cancel pass"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

export function VisitorPassLogTimeline({ logs, visitorPass }: { logs: VisitorPassLogEntry[]; visitorPass: VisitorPass }) {
  return (
    <div className="visitor-pass-log-list">
      {logs.map((log) => {
        const details = visitorPassLogDetails(log, visitorPass);
        return (
          <article className="visitor-pass-log-entry" key={log.id}>
            <span className={`visitor-pass-log-dot ${details.tone}`} />
            <div>
              <div className="visitor-pass-log-head">
                <span className={`visitor-pass-log-icon ${details.tone}`}>
                  {React.createElement(visitorPassLogIcon(log.action), { size: 24 })}
                </span>
                <div>
                  <strong>{details.title}</strong>
                  <p>{details.description}</p>
                </div>
                <time><Clock3 size={15} /> {formatDate(log.timestamp)}</time>
              </div>
              {details.fields.length ? (
                <div className="visitor-pass-log-fields">
                  {details.fields.map((field) => (
                    <span key={`${log.id}-${field.label}`}>
                      <small>{field.label}</small>
                      <strong>{field.value}</strong>
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}

export function VisitorPassModal({
  mode,
  visitorPass,
  onClose,
  onSaved
}: {
  mode: "create" | "edit";
  visitorPass: VisitorPass | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [visitorName, setVisitorName] = React.useState(visitorPass?.visitor_name ?? "");
  const [passType, setPassType] = React.useState<VisitorPassType>(visitorPass?.pass_type ?? "one-time");
  const [visitorPhone, setVisitorPhone] = React.useState(visitorPass?.visitor_phone ? `+${visitorPass.visitor_phone}` : "");
  const [expectedTime, setExpectedTime] = React.useState(() => visitorPass ? new Date(visitorPass.expected_time) : nextVisitorPassDate());
  const [windowMinutes, setWindowMinutes] = React.useState(visitorPass?.window_minutes ?? 30);
  const [validFrom, setValidFrom] = React.useState(() => visitorPass?.valid_from ? new Date(visitorPass.valid_from) : visitorPass ? new Date(visitorPass.window_start) : nextVisitorPassDate());
  const [validUntil, setValidUntil] = React.useState(() => {
    if (visitorPass?.valid_until) return new Date(visitorPass.valid_until);
    const start = visitorPass ? new Date(visitorPass.window_end) : nextVisitorPassDate();
    start.setHours(start.getHours() + 2);
    return start;
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const isDuration = passType === "duration";
  const updateDateFromInput = (value: string, setter: (date: Date) => void) => {
    const iso = fromDateTimeLocal(value);
    if (!iso) return;
    const next = new Date(iso);
    if (!Number.isNaN(next.getTime())) setter(next);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    if (isDuration && !visitorPhone.trim()) {
      setError("Duration passes need a visitor phone number.");
      return;
    }
    if (isDuration && validUntil <= validFrom) {
      setError("Duration pass end time must be after the start time.");
      return;
    }
    setSubmitting(true);
    const payload = isDuration
      ? {
        visitor_name: visitorName.trim(),
        pass_type: passType,
        visitor_phone: visitorPhone.trim(),
        expected_time: validFrom.toISOString(),
        window_minutes: windowMinutes,
        valid_from: validFrom.toISOString(),
        valid_until: validUntil.toISOString()
      }
      : {
        visitor_name: visitorName.trim(),
        pass_type: passType,
        visitor_phone: null,
        expected_time: expectedTime.toISOString(),
        window_minutes: windowMinutes,
        valid_from: null,
        valid_until: null
      };
    try {
      if (mode === "edit" && visitorPass) {
        await api.patch<VisitorPass>(`/api/v1/visitor-passes/${visitorPass.id}`, payload);
      } else {
        await api.post<VisitorPass>("/api/v1/visitor-passes", payload);
      }
      await onSaved();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to save Visitor Pass");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal-card visitor-pass-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Visitor Pass" : "New Visitor Pass"}</h2>
            <p>{isDuration ? `${formatDate(validFrom.toISOString())} to ${formatDate(validUntil.toISOString())}` : `${formatDate(expectedTime.toISOString())} · +/- ${windowMinutes} minutes`}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}

        <label className="field">
          <span>Visitor name</span>
          <div className="field-control">
            <UserPlus size={17} />
            <input value={visitorName} onChange={(event) => setVisitorName(event.target.value)} required />
          </div>
        </label>

        <div className="visitor-pass-window-select visitor-pass-type-select">
          <span>Pass type</span>
          <div>
            {visitorPassTypes.map((type) => (
              <button
                aria-pressed={passType === type}
                className={passType === type ? "active" : ""}
                key={type}
                onClick={() => setPassType(type)}
                type="button"
              >
                {type === "one-time" ? "One-time" : "Duration"}
              </button>
            ))}
          </div>
        </div>

        {isDuration ? (
          <section className="visitor-pass-duration-fields">
            <label className="field">
              <span>Visitor phone</span>
              <div className="field-control">
                <Smartphone size={17} />
                <input
                  autoComplete="tel"
                  onChange={(event) => setVisitorPhone(event.target.value)}
                  placeholder="+447700900123"
                  required
                  type="tel"
                  value={visitorPhone}
                />
              </div>
            </label>
            <label className="field compact-field">
              <span>Valid from</span>
              <input
                onChange={(event) => updateDateFromInput(event.target.value, setValidFrom)}
                required
                type="datetime-local"
                value={toDateTimeLocal(validFrom.toISOString())}
              />
            </label>
            <label className="field compact-field">
              <span>Valid until</span>
              <input
                onChange={(event) => updateDateFromInput(event.target.value, setValidUntil)}
                required
                type="datetime-local"
                value={toDateTimeLocal(validUntil.toISOString())}
              />
            </label>
          </section>
        ) : (
          <>
            <VisitorDateTimePicker value={expectedTime} onChange={setExpectedTime} />

            <div className="visitor-pass-window-select">
              <span>Time Window</span>
              <div>
                {visitorPassWindowOptions.map((minutes) => (
                  <button
                    aria-pressed={windowMinutes === minutes}
                    className={windowMinutes === minutes ? "active" : ""}
                    key={minutes}
                    onClick={() => setWindowMinutes(minutes)}
                    type="button"
                  >
                    +/- {minutes}m
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        <div className="modal-actions">
          <button className="secondary-button" onClick={onClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            <Save size={16} />
            {submitting ? "Saving..." : mode === "edit" ? "Save Pass" : "Create Pass"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function VisitorDateTimePicker({ value, onChange }: { value: Date; onChange: (value: Date) => void }) {
  const [visibleMonth, setVisibleMonth] = React.useState(() => new Date(value.getFullYear(), value.getMonth(), 1));
  const days = visitorCalendarDays(visibleMonth);
  const selectedKey = visitorDateKey(value);
  const timeValue = `${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`;
  const timeOptions = visitorTimeOptions(timeValue);

  const setDay = (day: Date) => {
    const next = new Date(day);
    next.setHours(value.getHours(), value.getMinutes(), 0, 0);
    onChange(next);
  };

  const setTime = (time: string) => {
    const [hour, minute] = time.split(":").map(Number);
    const next = new Date(value);
    next.setHours(hour, minute, 0, 0);
    onChange(next);
  };

  return (
    <section className="visitor-date-picker">
      <div className="visitor-date-picker-head">
        <button aria-label="Previous month" className="icon-button" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() - 1, 1))} type="button">
          <ChevronDown className="rotate-90" size={15} />
        </button>
        <strong>{visibleMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</strong>
        <button aria-label="Next month" className="icon-button" onClick={() => setVisibleMonth(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + 1, 1))} type="button">
          <ChevronRight size={15} />
        </button>
      </div>
      <div className="visitor-calendar-grid">
        {scheduleDays.map((day) => <span key={day}>{day.slice(0, 2)}</span>)}
        {days.map((day) => (
          <button
            className={`${day.getMonth() === visibleMonth.getMonth() ? "" : "muted"} ${visitorDateKey(day) === selectedKey ? "active" : ""}`}
            key={day.toISOString()}
            onClick={() => setDay(day)}
            type="button"
          >
            {day.getDate()}
          </button>
        ))}
      </div>
      <label className="field">
        <span>Expected time</span>
        <select value={timeValue} onChange={(event) => setTime(event.target.value)}>
          {timeOptions.map((time) => (
            <option key={time} value={time}>{time}</option>
          ))}
        </select>
      </label>
    </section>
  );
}

export function visitorPassMatches(visitorPass: VisitorPass, query: string) {
  return (
    matches(visitorPass.visitor_name, query) ||
    matches(visitorPass.number_plate ?? "", query) ||
    matches(visitorPass.visitor_phone ?? "", query) ||
    matches(visitorPass.pass_type, query) ||
    matches(visitorPass.vehicle_make ?? "", query) ||
    matches(visitorPass.vehicle_colour ?? "", query) ||
    matches(visitorPass.whatsapp_status_label ?? "", query) ||
    matches(visitorPass.status, query)
  );
}

export function visitorPassMatchesStatus(visitorPass: VisitorPass, filters: Set<VisitorPassStatus>) {
  return !filters.size || filters.size === visitorPassStatuses.length || filters.has(visitorPass.status);
}

export function isVisitorPassRealtimeEvent(event: RealtimeMessage) {
  return event.type.startsWith("visitor_pass.");
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
    source_metadata: isRecord(candidate.source_metadata) ? candidate.source_metadata : null,
    whatsapp_status: stringPayload(candidate.whatsapp_status) || null,
    whatsapp_status_label: stringPayload(candidate.whatsapp_status_label) || null,
    whatsapp_status_detail: stringPayload(candidate.whatsapp_status_detail) || null,
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

export function visitorPassWhatsAppMessageFromApi(candidate: unknown): VisitorPassWhatsAppMessage {
  const row = isRecord(candidate) ? candidate : {};
  const direction = stringPayload(row.direction);
  const normalizedDirection = direction === "inbound" || direction === "outbound" || direction === "status" ? direction : "status";
  return {
    id: stringPayload(row.id) || crypto.randomUUID(),
    direction: normalizedDirection,
    kind: stringPayload(row.kind) || "text",
    body: stringPayload(row.body),
    actor_label: stringPayload(row.actor_label) || (normalizedDirection === "inbound" ? "Visitor" : "IACS"),
    provider_message_id: stringPayload(row.provider_message_id) || null,
    status: stringPayload(row.status) || null,
    created_at: stringPayload(row.created_at) || new Date().toISOString(),
    metadata: isRecord(row.metadata) ? row.metadata : null,
  };
}

export function visitorPassWhatsAppMessagesEqual(left: VisitorPassWhatsAppMessage[], right: VisitorPassWhatsAppMessage[]) {
  if (left.length !== right.length) return false;
  return left.every((message, index) => {
    const other = right[index];
    return Boolean(other) &&
      message.id === other.id &&
      message.body === other.body &&
      message.status === other.status &&
      message.created_at === other.created_at;
  });
}

export function visitorPassWhatsAppMessagesWithMessage(messages: VisitorPassWhatsAppMessage[], message: VisitorPassWhatsAppMessage) {
  const next = messages.filter((item) => item.id !== message.id);
  next.push(message);
  next.sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime());
  return next;
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

export type VisitorPassTone = "blue" | "green" | "orange" | "red" | "gray";

export type VisitorPassMoreInfoState = {
  label: string;
  tone: "green" | "orange" | "red";
  icon: React.ElementType;
  spinning?: boolean;
};

export function visitorPassBaseStatusTone(status: VisitorPassStatus): VisitorPassTone {
  if (status === "active" || status === "used") return "green";
  if (status === "scheduled") return "blue";
  if (status === "cancelled") return "red";
  return "gray";
}

export function visitorPassStatusPillTone(visitorPass: VisitorPass): VisitorPassTone {
  const moreInfo = visitorPassMoreInfoState(visitorPass);
  if (!moreInfo) return visitorPassBaseStatusTone(visitorPass.status);
  if (moreInfo.tone === "red") return "red";
  if (moreInfo.label === "Chat Complete") return "green";
  if (moreInfo.label === "Message Sent" || moreInfo.label === "Replying..." || moreInfo.label === "Awaiting Visitor Reply") return "orange";
  if (moreInfo.tone === "green") return "green";
  return moreInfo.tone;
}

export function visitorPassMoreInfoState(visitorPass: VisitorPass): VisitorPassMoreInfoState | null {
  if (visitorPass.pass_type !== "duration" || !visitorPass.visitor_phone) return null;
  const status = (visitorPass.whatsapp_status || "").trim();
  const label = (visitorPass.whatsapp_status_label || "").trim();
  const detail = `${visitorPass.whatsapp_status_detail || ""} ${label} ${status}`.toLowerCase();
  if (status === "message_sending_failed" || status === "failed") {
    return { label: "Sending Message Failed", tone: "red", icon: AlertTriangle };
  }
  if (status === "user_not_on_whatsapp") {
    return { label: "Visitor isn't on WhatsApp", tone: "red", icon: AlertTriangle };
  }
  if (status === "visitor_replied") {
    return { label: "Replying...", tone: "green", icon: Loader2, spinning: true };
  }
  if (status === "welcome_message_sent" || status === "message_received" || status === "message_read") {
    return { label: "Message Sent", tone: "green", icon: Send };
  }
  if (status === "awaiting_visitor_reply" || status === "timeframe_confirmation_pending") {
    return { label: "Awaiting Visitor Reply", tone: "orange", icon: MessageCircle };
  }
  if (status === "timeframe_approval_pending") {
    return { label: "Awaiting Approval", tone: "orange", icon: Clock3 };
  }
  if (status === "complete" || status === "timeframe_approved") {
    return { label: "Chat Complete", tone: "green", icon: CheckCircle2 };
  }
  if (status === "timeframe_denied" || detail.includes("error") || detail.includes("failed") || detail.includes("unable") || detail.includes("rejected")) {
    return { label: "Error Detected", tone: "red", icon: AlertTriangle };
  }
  if (status || label) {
    return { label: label || titleCase(status.replace(/_/g, " ")), tone: "orange", icon: MessageCircle };
  }
  return { label: "Awaiting Visitor Reply", tone: "orange", icon: MessageCircle };
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

export function formatDurationSeconds(seconds: number) {
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

export function visitorPassWhatsAppDetailLabel(visitorPass: VisitorPass) {
  const status = (visitorPass.whatsapp_status || "").trim().toLowerCase();
  const label = (visitorPass.whatsapp_status_label || "").trim();
  if (status === "complete" || status === "timeframe_approved" || label.toLowerCase().startsWith("complete")) {
    return "Complete - Access Arranged";
  }
  return label.replace(/\s*-\s*Vehicle Registration:.*$/i, "") || "Not started";
}

export function visitorPassWhatsAppAbuseCooldown(visitorPass: VisitorPass): { until: string; reason: string } | null {
  if (!isRecord(visitorPass.source_metadata)) return null;
  const until = stringPayload(visitorPass.source_metadata.whatsapp_abuse_muted_until);
  if (!until) return null;
  const timestamp = new Date(until).getTime();
  if (!Number.isFinite(timestamp) || timestamp <= Date.now()) return null;
  return {
    until,
    reason: stringPayload(visitorPass.source_metadata.whatsapp_abuse_muted_reason)
  };
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
  const request = isRecord(log.metadata.request) ? log.metadata.request : null;
  if (request) {
    const currentWindow = visitorPassWindowFromValues(request.current_valid_from, request.current_valid_until);
    const originalWindow = visitorPassWindowFromValues(request.original_valid_from, request.original_valid_until);
    const requestedWindow = visitorPassWindowFromValues(request.requested_valid_from, request.requested_valid_until);
    if (originalWindow || currentWindow) fields.push({ label: "Original", value: originalWindow || currentWindow || "" });
    if (requestedWindow) fields.push({ label: "Requested", value: requestedWindow });
  }

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
  if (log.action === "visitor_pass.timeframe_change_requested") {
    return {
      title: "Time Change Requested",
      description: `Visitor via WhatsApp requested a time change for ${visitorPass.visitor_name}.`,
      tone: "amber",
      fields,
    };
  }
  if (log.action === "visitor_pass.timeframe_change_approved") {
    return {
      title: "Time Change Approved",
      description: `${actor} approved the requested time change.`,
      tone: "green",
      fields,
    };
  }
  if (log.action === "visitor_pass.timeframe_change_denied") {
    return {
      title: "Time Change Denied",
      description: `${actor} denied the requested time change.`,
      tone: "red",
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
  if (action.includes("timeframe") || action === "visitor_pass.update") return Clock3;
  if (action === "visitor_pass.cancel" || action === "visitor_pass.delete") return Trash2;
  if (action.includes("arrival") || action === "visitor_pass.claim") return CheckCircle2;
  if (action.includes("departure")) return ArrowRight;
  return ClipboardPaste;
}

export function visitorPassLogActor(log: VisitorPassLogEntry) {
  const actor = log.actor_user_label || log.actor || "IACS";
  if (log.actor === "Alfred_AI") return `${log.actor_user_label || "Jason"} via Alfred`;
  if (log.actor === "Visitor Concierge" || log.action === "visitor_pass.timeframe_change_requested") return "Visitor via WhatsApp";
  if (log.action === "visitor_pass.timeframe_change_approved" || log.action === "visitor_pass.timeframe_change_denied") {
    return `${actor} via WhatsApp`;
  }
  if (log.actor === "System") return "IACS";
  if (log.actor.toLowerCase().includes("icloud")) return "iCloud Calendar Sync";
  return `${actor} in UI`;
}

export function visitorPassLogChangedFields(oldValue: Record<string, unknown>, newValue: Record<string, unknown>) {
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

export function visitorPassLogFieldValue(key: string, value: unknown) {
  const text = stringPayload(value);
  if (text && ["expected_time", "valid_from", "valid_until", "arrival_time", "departure_time"].includes(key)) {
    return formatDate(text);
  }
  if (key === "window_minutes" && value !== null && value !== undefined) return `+/- ${String(value)}m`;
  if (key === "duration_on_site_seconds" && typeof value === "number") return formatDurationSeconds(value);
  if (key === "status") return titleCase(text);
  return text;
}

export function visitorPassWindowFromValues(start: unknown, end: unknown) {
  const startText = stringPayload(start);
  const endText = stringPayload(end);
  if (!startText || !endText) return "";
  return `${formatDate(startText)} to ${formatDate(endText)}`;
}

export function visitorPassWhatsAppStatusTooltip(visitorPass: VisitorPass): { title: string; body: string } | null {
  const status = visitorPass.whatsapp_status || "";
  const metadataError = isRecord(visitorPass.source_metadata) ? stringPayload(visitorPass.source_metadata.whatsapp_last_error) : "";
  const rawDetail = [visitorPass.whatsapp_status_detail || "", metadataError].filter(Boolean).join(" ");
  const moreInfo = visitorPassMoreInfoState(visitorPass);
  if (moreInfo?.tone !== "red") return null;
  return {
    title: moreInfo?.label || visitorPass.whatsapp_status_label || "WhatsApp message issue",
    body: visitorPassFriendlyWhatsAppError(status, rawDetail),
  };
}

export function visitorPassFriendlyWhatsAppError(status: string, rawDetail: string) {
  const detail = rawDetail.toLowerCase();
  if (status === "timeframe_denied") {
    return "The requested time change was denied. The visitor can still use the current approved pass window.";
  }
  if (status === "user_not_on_whatsapp" || detail.includes("131026") || detail.includes("not a whatsapp") || detail.includes("not on whatsapp") || detail.includes("not registered")) {
    return "WhatsApp could not find an account for this phone number. Check the number includes the country code, or ask the visitor to message Alfred from WhatsApp first.";
  }
  if (detail.includes("131047") || detail.includes("re-engagement") || detail.includes("customer service window")) {
    return "Meta blocked this message because the visitor has not messaged the WhatsApp business number recently. Ask the visitor to send \"Begin\" to Alfred, then try again.";
  }
  if (detail.includes("template")) {
    return "Meta did not accept the WhatsApp template. Check the approved template name and language in API & Integrations, or ask the visitor to message \"Begin\" while templates are pending.";
  }
  if (detail.includes("access token") || detail.includes("oauth") || detail.includes("permission") || detail.includes("401") || detail.includes("403")) {
    return "Meta rejected the WhatsApp credentials. Check the access token and WhatsApp Business permissions in API & Integrations.";
  }
  if (detail.includes("phone_number_id") || detail.includes("phone number id") || detail.includes("sender")) {
    return "The WhatsApp business sender is not configured correctly. Check the WhatsApp Phone Number ID in API & Integrations.";
  }
  if (detail.includes("rate")) {
    return "WhatsApp is limiting messages right now. Wait a moment, then try sending the message again.";
  }
  return "WhatsApp could not send this message. Check the visitor's phone number and the WhatsApp integration settings, then try again.";
}

export function nextVisitorPassDate() {
  const next = new Date();
  next.setMinutes(Math.ceil(next.getMinutes() / 15) * 15, 0, 0);
  if (next.getMinutes() === 60) {
    next.setHours(next.getHours() + 1, 0, 0, 0);
  }
  return next;
}

export function visitorDateKey(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

export function visitorCalendarDays(month: Date) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const mondayOffset = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(first.getDate() - mondayOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    return day;
  });
}

export function visitorTimeOptions(selected?: string) {
  const options = Array.from({ length: 96 }, (_, index) => {
    const minutes = index * 15;
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  });
  if (selected && !options.includes(selected)) {
    options.push(selected);
    options.sort();
  }
  return options;
}
