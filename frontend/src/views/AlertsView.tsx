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
  AlertSeverity,
  alertSeverityLabel,
  alertSeverityTone,
  Anomaly,
  api,
  Badge,
  EmptyState,
  formatDate,
  isActionableAlert,
  MetricCard,
  titleCase,
  Toolbar
} from "../shared";



export function alertIdFromLocation() {
  return new URLSearchParams(window.location.search).get("alert") ?? "";
}

export function alertMatchesFocus(alert: Anomaly, focusedAlertId: string) {
  return Boolean(focusedAlertId && (alert.id === focusedAlertId || alert.alert_ids.includes(focusedAlertId)));
}

export function alertDomId(alertId: string) {
  return `alert-row-${alertId.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

export type AlertActionTarget = {
  alert: Anomaly;
  action: "resolve" | "reopen";
};

export function AlertsView({ refreshDashboard, refreshToken }: { refreshDashboard: () => Promise<void>; refreshToken: number }) {
  const [alerts, setAlerts] = React.useState<Anomaly[]>([]);
  const [statusFilter, setStatusFilter] = React.useState<"open" | "resolved" | "all">("open");
  const [severityFilter, setSeverityFilter] = React.useState<"all" | AlertSeverity>("all");
  const [typeFilter, setTypeFilter] = React.useState("all");
  const [query, setQuery] = React.useState("");
  const [focusedAlertId, setFocusedAlertId] = React.useState(() => alertIdFromLocation());
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [actionTarget, setActionTarget] = React.useState<AlertActionTarget | null>(null);
  const [resolutionNote, setResolutionNote] = React.useState("");
  const [actionLoading, setActionLoading] = React.useState(false);
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const loadAlerts = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ status: statusFilter, limit: "200" });
      if (severityFilter !== "all") params.set("severity", severityFilter);
      if (typeFilter !== "all") params.set("type", typeFilter);
      if (query.trim()) params.set("q", query.trim());
      setAlerts(await api.get<Anomaly[]>(`/api/v1/alerts?${params}`));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load alerts");
    } finally {
      setLoading(false);
    }
  }, [query, severityFilter, statusFilter, typeFilter]);

  React.useEffect(() => {
    loadAlerts().catch(() => undefined);
  }, [loadAlerts]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    loadAlerts().catch(() => undefined);
  }, [loadAlerts, refreshToken]);

  React.useEffect(() => {
    const updateFocusedAlert = () => setFocusedAlertId(alertIdFromLocation());
    window.addEventListener("popstate", updateFocusedAlert);
    return () => window.removeEventListener("popstate", updateFocusedAlert);
  }, []);

  React.useEffect(() => {
    if (!focusedAlertId || loading) return;
    const target = alerts.find((alert) => alertMatchesFocus(alert, focusedAlertId));
    if (!target) return;
    window.requestAnimationFrame(() => {
      const node = document.getElementById(alertDomId(target.id));
      node?.scrollIntoView({ behavior: "smooth", block: "center" });
      node?.focus({ preventScroll: true });
    });
  }, [alerts, focusedAlertId, loading]);

  const actOnAlert = async (target: AlertActionTarget, note?: string) => {
    setActionLoading(true);
    setError("");
    try {
      await api.patch("/api/v1/alerts/action", {
        alert_ids: target.alert.alert_ids,
        action: target.action,
        note: note ?? null
      });
      setActionTarget(null);
      setResolutionNote("");
      await Promise.all([loadAlerts(), refreshDashboard()]);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Unable to update alert");
    } finally {
      setActionLoading(false);
    }
  };

  const openCount = alerts.filter((alert) => alert.status === "open").length;
  const actionableCount = alerts.filter(isActionableAlert).length;

  return (
    <section className="view-stack alerts-page">
      <Toolbar title="Alerts" count={alerts.length} icon={Bell}>
        <button className="secondary-button" onClick={() => loadAlerts().catch(() => undefined)} type="button">
          <RefreshCcw size={15} /> Refresh
        </button>
      </Toolbar>

      <div className="alerts-summary-grid">
        <MetricCard icon={AlertTriangle} label="Action Needed" value={String(actionableCount)} detail="warning and critical" tone={actionableCount ? "amber" : "gray"} />
        <MetricCard icon={Bell} label="Open Alerts" value={String(openCount)} detail="including informational" tone={openCount ? "blue" : "green"} />
        <MetricCard icon={CheckCircle2} label="Resolved View" value={statusFilter === "resolved" ? String(alerts.length) : "available"} detail="audit trail retained" tone="green" />
      </div>

      <div className="alerts-controls">
        <div className="alert-status-tabs" role="tablist" aria-label="Alert status">
          {(["open", "resolved", "all"] as const).map((value) => (
            <button className={statusFilter === value ? "active" : ""} key={value} onClick={() => setStatusFilter(value)} type="button">
              {titleCase(value)}
            </button>
          ))}
        </div>
        <label className="search alerts-search">
          <Search size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search plate, message, or context..." />
        </label>
        <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value as typeof severityFilter)} aria-label="Filter by severity">
          <option value="all">All severities</option>
          <option value="critical">Critical</option>
          <option value="warning">Warning</option>
          <option value="info">Informational</option>
        </select>
        <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)} aria-label="Filter by alert type">
          <option value="all">All types</option>
          <option value="unauthorized_plate">Unknown plate</option>
          <option value="outside_schedule">Outside schedule</option>
          <option value="duplicate_entry">Duplicate entry</option>
          <option value="duplicate_exit">Duplicate exit</option>
        </select>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? (
        <div className="loading-panel">Loading alerts</div>
      ) : alerts.length ? (
        <div className="alerts-list">
          {alerts.map((alert) => (
            <AlertReviewRow
              alert={alert}
              focused={alertMatchesFocus(alert, focusedAlertId)}
              key={alert.id}
              onReopen={() => actOnAlert({ alert, action: "reopen" })}
              onResolve={() => {
                setResolutionNote("");
                setActionTarget({ alert, action: "resolve" });
              }}
            />
          ))}
        </div>
      ) : (
        <EmptyState icon={CheckCircle2} label="No alerts match this view" />
      )}

      {actionTarget?.action === "resolve" ? (
        <div className="modal-backdrop" role="presentation">
          <form
            className="modal-card alert-resolution-modal"
            onSubmit={(event) => {
              event.preventDefault();
              actOnAlert(actionTarget, resolutionNote).catch(() => undefined);
            }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="alert-resolution-title"
          >
            <div className="modal-header">
              <h2 id="alert-resolution-title">Resolve alert?</h2>
              <p>{actionTarget.alert.grouped ? `${actionTarget.alert.count} grouped alert records` : titleCase(actionTarget.alert.type)}</p>
            </div>
            <label className="field">
              <span>Resolution note</span>
              <textarea value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} placeholder="Optional note for the audit trail" rows={4} />
            </label>
            <div className="modal-actions">
              <button className="secondary-button" disabled={actionLoading} onClick={() => setActionTarget(null)} type="button">
                Cancel
              </button>
              <button className="primary-button" disabled={actionLoading} type="submit">
                <Check size={16} />
                {actionLoading ? "Resolving..." : "Resolve Alert"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}

export function AlertReviewRow({
  alert,
  focused,
  onResolve,
  onReopen
}: {
  alert: Anomaly;
  focused: boolean;
  onResolve: () => void;
  onReopen: () => void;
}) {
  const isResolved = alert.status === "resolved";
  const isUnknownPlate = alert.type === "unauthorized_plate";
  const title = isUnknownPlate
    ? alert.registration_number || "Unknown registration"
    : titleCase(alert.type);
  const message = isUnknownPlate ? "Unauthorised Plate, Access Denied" : alert.message;
  const showReadCount = alert.grouped && alert.count > 1;
  return (
    <article
      className={`alert-review-row ${alert.status}${focused ? " focused" : ""}`}
      id={alertDomId(alert.id)}
      tabIndex={focused ? -1 : undefined}
    >
      <span className={`alert-review-icon ${alert.severity}`}>
        {isResolved ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}
      </span>
      <div className="alert-review-main">
        <div className={alert.snapshot_url ? "alert-review-content has-snapshot" : "alert-review-content"}>
          {alert.snapshot_url ? (
            <img
              alt={`${title} snapshot from camera.gate`}
              className="alert-review-snapshot"
              loading="lazy"
              src={alert.snapshot_url}
            />
          ) : null}
          <div className="alert-review-copy">
            <strong>{title}</strong>
            <span>{message}</span>
          </div>
        </div>
        <div className="alert-review-meta">
          <span>First Seen: <strong>{formatDate(alert.first_seen_at || alert.created_at)}</strong></span>
          <span>Last Seen: <strong>{formatDate(alert.last_seen_at || alert.created_at)}</strong></span>
        </div>
        {isResolved ? (
          <div className="alert-resolution-detail">
            <span>Resolved {alert.resolved_at ? formatDate(alert.resolved_at) : ""}{alert.resolved_by ? ` by ${alert.resolved_by.display_name}` : ""}</span>
            {alert.resolution_note ? <p>{alert.resolution_note}</p> : null}
          </div>
        ) : null}
      </div>
      <div className="alert-review-side">
        <div className="alert-review-badges">
          <Badge tone={alertSeverityTone(alert.severity)}>{alertSeverityLabel(alert.severity)}</Badge>
          {showReadCount ? <Badge tone="gray">{alert.count} reads</Badge> : null}
          <Badge tone={isResolved ? "green" : "blue"}>{isResolved ? "Resolved" : "Open"}</Badge>
        </div>
        <div className="alert-review-actions">
          {isResolved ? (
            <button className="secondary-button" onClick={onReopen} type="button">Reopen</button>
          ) : (
            <button className="primary-button" onClick={onResolve} type="button">
              <Check size={15} /> Resolve
            </button>
          )}
        </div>
      </div>
    </article>
  );
}
