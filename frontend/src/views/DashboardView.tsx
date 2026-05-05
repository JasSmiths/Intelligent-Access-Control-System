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
  AccessEvent,
  activeManagedCovers,
  AlertSeverity,
  Anomaly,
  api,
  Badge,
  BadgeTone,
  createActionConfirmation,
  displayUserName,
  EmptyState,
  HomeAssistantManagedCover,
  IntegrationStatus,
  isActionableAlert,
  MaintenanceStatus,
  NavigateToView,
  PanelHeader,
  Person,
  Presence,
  titleCase,
  UserAccount,
  Vehicle,
  visitorEventDisplayName
} from "../shared";



export type DoorCommandAction = "open" | "close";

export type DashboardCommand = {
  kind: "gate" | "garage_door";
  entity_id?: string;
  label: string;
  action: DoorCommandAction;
};

export function Dashboard({
  presence,
  events,
  anomalies,
  integrationStatus,
  maintenanceStatus,
  people,
  vehicles,
  refresh,
  currentUser,
  navigateToView,
  onMaintenanceStatusChanged
}: {
  presence: Presence[];
  events: AccessEvent[];
  anomalies: Anomaly[];
  integrationStatus: IntegrationStatus | null;
  maintenanceStatus: MaintenanceStatus | null;
  people: Person[];
  vehicles: Vehicle[];
  refresh: () => Promise<void>;
  currentUser: UserAccount;
  navigateToView: NavigateToView;
  onMaintenanceStatusChanged: (status: MaintenanceStatus) => void;
}) {
  const [now, setNow] = React.useState(() => new Date());
  const [pendingCommand, setPendingCommand] = React.useState<DashboardCommand | null>(null);
  const [maintenanceDisableOpen, setMaintenanceDisableOpen] = React.useState(false);
  const [maintenanceLoading, setMaintenanceLoading] = React.useState(false);
  const [maintenanceError, setMaintenanceError] = React.useState("");
  const [commandLoading, setCommandLoading] = React.useState(false);
  const [commandError, setCommandError] = React.useState("");
  const maintenanceActive = maintenanceStatus?.is_active === true;
  const isAdmin = currentUser.role === "admin";
  const present = presence.filter((item) => item.state === "present").length;
  const exited = presence.filter((item) => item.state === "exited").length;
  const unknown = Math.max(presence.length - present - exited, 0);
  const actionableAlerts = anomalies.filter(isActionableAlert);
  const critical = actionableAlerts.filter((item) => item.severity === "critical").length;
  const warning = actionableAlerts.filter((item) => item.severity === "warning").length;
  const displayEvents = getDashboardEvents(events, vehicles, people);
  const displayAnomalies = getDashboardAnomalies(actionableAlerts);
  const expected = Math.max(people.length, presence.length);
  const todayEvents = events.filter((event) => isToday(event.occurred_at, now));
  const exitedToday = todayEvents.filter((event) => event.direction === "exit").length;
  const deniedToday = todayEvents.filter((event) => event.decision === "denied").length;
  const activeVehicles = vehicles.filter((vehicle) => vehicle.is_active !== false).length;
  const liveSources = new Set(events.map((event) => event.source).filter(Boolean)).size;
  const gateEntities = activeManagedCovers(integrationStatus?.gate_entities);
  const garageDoorEntities = activeManagedCovers(integrationStatus?.garage_door_entities);
  const topGateState = gateEntities[0]?.state ?? integrationStatus?.current_gate_state ?? integrationStatus?.last_gate_state ?? "unknown";
  const siteStatusTitle = maintenanceActive ? "Maintenance Mode Enabled" : critical ? "Critical alerts" : warning ? "Action needed" : "All systems normal";
  const siteStatusDetail = maintenanceActive ? "All automated actions disabled" : critical
    ? `${critical} critical alert${critical === 1 ? "" : "s"}`
    : warning
      ? `${warning} warning alert${warning === 1 ? "" : "s"}`
      : "No actionable alerts";
  const greeting = greetingForDate(now);
  const firstName = currentUser.first_name || displayUserName(currentUser).split(" ")[0] || "there";

  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const runDashboardCommand = async () => {
    if (!pendingCommand || commandLoading) return;
    setCommandLoading(true);
    setCommandError("");
    try {
      if (pendingCommand.kind === "gate") {
        const payload = { reason: "Dashboard Top Gate status command" };
        const confirmation = await createActionConfirmation("gate.open", payload, {
          target_entity: "Gate",
          target_label: pendingCommand.label,
          reason: payload.reason
        });
        await api.post("/api/v1/integrations/gate/open", {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      } else {
        const payload = {
          entity_id: pendingCommand.entity_id,
          action: pendingCommand.action,
          reason: `Dashboard ${pendingCommand.label} ${pendingCommand.action} command`
        };
        const confirmation = await createActionConfirmation(`cover.${pendingCommand.action}`, payload, {
          target_entity: "Cover",
          target_id: pendingCommand.entity_id,
          target_label: pendingCommand.label,
          reason: payload.reason
        });
        await api.post("/api/v1/integrations/cover/command", {
          ...payload,
          confirmation_token: confirmation.confirmation_token
        });
      }
      setPendingCommand(null);
      await refresh();
      window.setTimeout(() => refresh().catch(() => undefined), 2500);
    } catch (error) {
      setCommandError(error instanceof Error ? error.message : `Unable to ${pendingCommand.action} ${pendingCommand.label}.`);
    } finally {
      setCommandLoading(false);
    }
  };

  const disableMaintenanceMode = async () => {
    if (maintenanceLoading) return;
    setMaintenanceLoading(true);
    setMaintenanceError("");
    try {
      const payload = { reason: "Disabled from Dashboard Site Status icon" };
      const confirmation = await createActionConfirmation("maintenance_mode.disable", payload, {
        target_entity: "MaintenanceMode",
        target_label: "Maintenance Mode",
        reason: payload.reason
      });
      const status = await api.post<MaintenanceStatus>("/api/v1/maintenance/disable", {
        ...payload,
        confirmation_token: confirmation.confirmation_token
      });
      onMaintenanceStatusChanged(status);
      setMaintenanceDisableOpen(false);
      await refresh();
    } catch (error) {
      setMaintenanceError(error instanceof Error ? error.message : "Unable to disable Maintenance Mode.");
    } finally {
      setMaintenanceLoading(false);
    }
  };

  return (
    <section className="dashboard-page">
      <div className="dashboard-intro">
        <div>
          <h1>{greeting}, {firstName}</h1>
          <p>Here's what's happening at Crest House today.</p>
        </div>
        <div className="intro-clock">
          <Clock3 size={18} />
          <span>{formatLongDate(now)}</span>
        </div>
      </div>

      <div className="dashboard-grid">
        <div className="card site-status-card">
          <PanelHeader title="Site Status" />
          <div className={maintenanceActive ? "site-status-main maintenance" : "site-status-main"}>
            {maintenanceActive ? (
              <button
                className="maintenance-status-icon"
                disabled={!isAdmin}
                onClick={() => {
                  if (!isAdmin) return;
                  setMaintenanceError("");
                  setMaintenanceDisableOpen(true);
                }}
                type="button"
                aria-label="Disable Maintenance Mode"
              >
                <Construction size={54} strokeWidth={1} />
              </button>
            ) : (
              <ShieldCheck size={54} />
            )}
            <div>
              <strong>{siteStatusTitle}</strong>
              <span>{siteStatusDetail}</span>
            </div>
          </div>
          <div className="status-metrics">
            <StatusMetric label="People tracked" mobileLabel="People" value={String(people.length)} />
            <StatusMetric label="Active vehicles" mobileLabel="Vehicles" value={String(activeVehicles)} />
            <StatusMetric label="Live sources" mobileLabel="Sources" value={String(liveSources)} />
          </div>
        </div>

        <div className="card gate-card">
          <PanelHeader title="Status" action="View all" />
          <div className={maintenanceActive ? "gate-control-section maintenance-disabled" : "gate-control-section"}>
          <div className="gate-list">
            {gateEntities.length ? gateEntities.map((gate) => (
              <GateRow
                icon={Car}
                key={gate.entity_id}
                label={gate.name || "Gate"}
                state={commandLoading && pendingCommand?.kind === "gate" ? "opening" : gate.state ?? topGateState}
                onActionClick={maintenanceActive || !isAdmin ? undefined : commandForGate(gate.name || "Gate", gate.state ?? topGateState, setPendingCommand, setCommandError)}
              />
            )) : (
              <GateRow
                icon={Car}
                label="Top Gate"
                state={commandLoading && pendingCommand?.kind === "gate" ? "opening" : topGateState}
                onActionClick={maintenanceActive || !isAdmin ? undefined : commandForGate("Top Gate", topGateState, setPendingCommand, setCommandError)}
              />
            )}
            {garageDoorEntities.map((door) => (
              <GarageDoorRow
                key={door.entity_id}
                label={door.name || door.entity_id}
                state={commandLoading && pendingCommand?.kind === "garage_door" && pendingCommand.entity_id === door.entity_id ? inProgressState(pendingCommand.action) : door.state ?? "unknown"}
                onActionClick={maintenanceActive || !isAdmin ? undefined : commandForGarageDoor(door, setPendingCommand, setCommandError)}
              />
            ))}
            <DoorRow label="Back Door" state={integrationStatus?.back_door_state ?? "unknown"} />
          </div>
          {maintenanceActive ? (
            <div className="maintenance-control-overlay" aria-hidden="true">
              <HardHat size={96} strokeWidth={1} />
            </div>
          ) : null}
          </div>
        </div>

        <div className="card presence-summary-card">
          <PanelHeader title="Presence Summary" action="View all" />
          <div className="presence-stats">
            <PresenceStat label="Inside Now" value={String(present)} trend="current" tone="green" />
            <PresenceStat label="Expected" value={String(expected)} trend="profiles" tone="blue" />
            <PresenceStat label="Exited Today" value={String(exitedToday)} trend="events" tone="gray" />
          </div>
          <div className="presence-bar" aria-label="Presence mix">
            <span className="residents" style={{ width: `${presenceSegmentWidth(present, presence.length)}%` }} />
            <span className="staff" style={{ width: `${presenceSegmentWidth(exited, presence.length)}%` }} />
            <span className="visitors" style={{ width: `${presenceSegmentWidth(unknown, presence.length)}%` }} />
          </div>
          <div className="presence-legend">
            <LegendDot className="residents" label="Present" value={String(present)} />
            <LegendDot className="staff" label="Exited" value={String(exited)} />
            <LegendDot className="visitors" label="Unknown" value={String(unknown)} />
          </div>
        </div>

        <div className="card recent-events-card">
          <PanelHeader title="Recent Events" action="View all" />
          <div className="event-feed">
            {displayEvents.length ? displayEvents.map((event) => {
              const Icon = event.icon;
              return (
                <div
                  className={event.snapshot_url ? "event-feed-row has-snapshot" : "event-feed-row"}
                  key={event.id}
                  tabIndex={event.snapshot_url ? 0 : undefined}
                >
                  <time>{event.time}</time>
                  <span className={`feed-line ${event.tone}`} />
                  <span className={`event-chip ${event.tone}`}>
                    <Icon size={18} />
                  </span>
                  <div>
                    <strong>{event.label}</strong>
                    <span>{event.subtitle}</span>
                  </div>
                  <EventStatusBadge event={event} />
                  <DashboardEventSnapshotPreview event={event} />
                </div>
              );
            }) : <EmptyState icon={CalendarDays} label="No recent events" />}
          </div>
          <p className="card-footnote">Showing latest 5 events</p>
        </div>

        <div className="card anomaly-card">
          <PanelHeader title="Alerts" action="View all" onAction={() => navigateToView("alerts")} />
          <div className="anomaly-feed">
            {displayAnomalies.length ? displayAnomalies.map((item) => (
              <button
                className="anomaly-feed-row"
                key={item.id}
                onClick={() => navigateToView("alerts", { search: `?alert=${encodeURIComponent(item.id)}` })}
                type="button"
              >
                <span className={`anomaly-icon ${item.severity}`}>
                  <AlertTriangle size={20} />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <span>{item.detail}</span>
                </div>
                <time>{item.time}</time>
              </button>
            )) : <EmptyState icon={CheckCircle2} label="No actionable alerts" />}
          </div>
          {actionableAlerts.length ? <p className="unresolved-count">{actionableAlerts.length} action needed</p> : null}
        </div>

        <div className="card chart-card">
          <PanelHeader title="Daily Entries vs Exits" action="7 Days" actionKind="select" />
          <DailyEntriesChart events={events} />
        </div>
      </div>

      {pendingCommand ? (
        <GateConfirmModal
          action={pendingCommand.action}
          error={commandError}
          label={pendingCommand.label}
          loading={commandLoading}
          onCancel={() => {
            if (commandLoading) return;
            setPendingCommand(null);
            setCommandError("");
          }}
          onConfirm={runDashboardCommand}
        />
      ) : null}
      {maintenanceDisableOpen ? (
        <MaintenanceDisableModal
          error={maintenanceError}
          loading={maintenanceLoading}
          onCancel={() => {
            if (maintenanceLoading) return;
            setMaintenanceDisableOpen(false);
            setMaintenanceError("");
          }}
          onConfirm={disableMaintenanceMode}
        />
      ) : null}
    </section>
  );
}

export function MaintenanceDisableModal({
  error,
  loading,
  onCancel,
  onConfirm
}: {
  error: string;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="maintenance-disable-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className="gate-confirm-icon maintenance">
              <Construction size={20} strokeWidth={1} />
            </span>
            <div>
              <h2 className="maintenance-disable-title" id="maintenance-disable-title">Disable Maintenance Mode</h2>
              <p>Allow automated actions to resume normal operation</p>
            </div>
          </div>
        </div>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Cancel
          </button>
          <button className="primary-button" disabled={loading} onClick={onConfirm} type="button">
            <Check size={16} />
            {loading ? "Resuming..." : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function GateConfirmModal({
  action,
  error,
  label,
  loading,
  onCancel,
  onConfirm
}: {
  action: DoorCommandAction;
  error: string;
  label: string;
  loading: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const actionLabel = titleCase(action);
  const isGarage = label.toLowerCase().includes("garage");
  const Icon = isGarage
    ? Warehouse
    : action === "open" ? DoorOpen : DoorClosed;
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal-card gate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="gate-confirm-title">
        <div className="modal-header">
          <div className="gate-confirm-title">
            <span className="gate-confirm-icon">
              <Icon size={20} />
            </span>
            <div>
              <h2 id="gate-confirm-title">{actionLabel} {label}?</h2>
            </div>
          </div>
        </div>
        {error ? <div className="auth-error inline-error">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary-button" disabled={loading} onClick={onCancel} type="button">
            Cancel
          </button>
          <button className="primary-button" disabled={loading} onClick={onConfirm} type="button">
            <Icon size={16} />
            {loading ? `${titleCase(inProgressState(action))}...` : `${actionLabel} ${label}`}
          </button>
        </div>
      </div>
    </div>
  );
}

export function StatusMetric({ label, mobileLabel, value }: { label: string; mobileLabel?: string; value: string }) {
  return (
    <div>
      <span>
        <i />
        <span className="status-label status-label-desktop">{label}</span>
        <span className="status-label status-label-mobile">{mobileLabel ?? label}</span>
      </span>
      <strong>{value}</strong>
    </div>
  );
}

export function GateRow({
  icon: Icon,
  label,
  state,
  onActionClick
}: {
  icon: React.ElementType;
  label: string;
  state: string;
  onActionClick?: () => void;
}) {
  const normalized = normalizeGateState(state);
  const display = gateStateDisplay(state);
  const hasAction = (normalized === "closed" || normalized === "open") && display.actionable && onActionClick;
  return (
    <div className="gate-row">
      <Icon size={18} />
      <strong>{label}</strong>
      {hasAction ? (
        <button className={`badge ${display.tone} badge-action`} onClick={onActionClick} type="button">
          {display.label}
        </button>
      ) : (
        <Badge tone={display.tone}>{display.label}</Badge>
      )}
    </div>
  );
}

export function DoorRow({ label, state }: { label: string; state: string }) {
  const normalized = normalizeGateState(state);
  const Icon = normalized === "open" ? DoorOpen : DoorClosed;
  return <GateRow icon={Icon} label={label} state={state} />;
}

export function GarageDoorRow({ label, state, onActionClick }: { label: string; state: string; onActionClick?: () => void }) {
  return <GateRow icon={Warehouse} label={label} state={state} onActionClick={onActionClick} />;
}

export function commandForGate(
  label: string,
  state: string,
  setPendingCommand: React.Dispatch<React.SetStateAction<DashboardCommand | null>>,
  setCommandError: React.Dispatch<React.SetStateAction<string>>
) {
  const normalized = normalizeGateState(state);
  if (normalized !== "closed") return undefined;
  return () => {
    setCommandError("");
    setPendingCommand({ kind: "gate", label, action: "open" });
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

export function PresenceStat({ label, value, trend, tone }: { label: string; value: string; trend: string; tone: "green" | "blue" | "gray" }) {
  return (
    <div className="presence-stat">
      <span>{label}</span>
      <strong className={tone}>{value}</strong>
      <small>{trend}</small>
    </div>
  );
}

export function presenceSegmentWidth(value: number, total: number) {
  if (!total || !value) return 0;
  return Math.max((value / total) * 100, 6);
}

export function LegendDot({ className, label, value }: { className: string; label: string; value: string }) {
  return (
    <span>
      <i className={className} />
      {label}
      <strong>{value}</strong>
    </span>
  );
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

export function DashboardEventSnapshotPreview({ event }: { event: DashboardEvent }) {
  if (!event.snapshot_url) return null;
  return (
    <span className="dashboard-event-snapshot-preview" aria-hidden="true">
      <img alt="" loading="lazy" src={event.snapshot_url} />
    </span>
  );
}

export function EventStatusBadge({ event }: { event: DashboardEvent }) {
  if (event.statusIcon) {
    const Icon = event.statusIcon;
    return (
      <Badge tone={event.statusTone}>
        <span className="event-status-icon" aria-label={event.statusLabel} title={event.statusLabel}>
          <Icon size={13} aria-hidden="true" />
        </span>
      </Badge>
    );
  }
  return <Badge tone={event.statusTone}>{event.status}</Badge>;
}

export type DashboardAnomaly = {
  id: string;
  title: string;
  detail: string;
  time: string;
  severity: AlertSeverity;
};

export function getDashboardAnomalies(anomalies: Anomaly[]): DashboardAnomaly[] {
  return anomalies.slice(0, 4).map((item) => ({
    id: item.id,
    title: titleCase(item.type),
    detail: item.message,
    time: formatTime(item.last_seen_at || item.created_at),
    severity: item.severity
  }));
}

export function DailyEntriesChart({ events }: { events: AccessEvent[] }) {
  const days = lastSevenDayBuckets(events);
  const max = Math.max(...days.flatMap((item) => [item.entries, item.exits]), 1);

  return (
    <div className="daily-chart">
      <div className="chart-grid-lines" aria-hidden="true">
        <span>{max}</span>
        <span>{Math.ceil(max * 0.66)}</span>
        <span>{Math.ceil(max * 0.33)}</span>
        <span>0</span>
      </div>
      <div className="chart-bars">
        {days.map((item) => (
          <div className="chart-day" key={item.day}>
            <div className="chart-pair">
              <span className="entry" style={{ height: `${(item.entries / max) * 100}%` }} />
              <span className="exit" style={{ height: `${(item.exits / max) * 100}%` }} />
            </div>
            <small>{item.day}</small>
          </div>
        ))}
      </div>
      <div className="chart-legend">
        <LegendDot className="residents" label="Entries" value="" />
        <LegendDot className="exits" label="Exits" value="" />
      </div>
    </div>
  );
}

export function lastSevenDayBuckets(events: AccessEvent[]) {
  const formatter = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date();
    date.setHours(0, 0, 0, 0);
    date.setDate(date.getDate() - (6 - index));
    const nextDate = new Date(date);
    nextDate.setDate(date.getDate() + 1);
    const dayEvents = events.filter((event) => {
      const occurred = new Date(event.occurred_at);
      return occurred >= date && occurred < nextDate;
    });
    return {
      day: formatter.format(date),
      entries: dayEvents.filter((event) => event.direction === "entry").length,
      exits: dayEvents.filter((event) => event.direction === "exit").length
    };
  });
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
