import { useModalFocus } from "../ui/useModalFocus";
import { useModalClose } from "../ui/useModalClose";
import { useEditorDismiss } from "../ui/useEditorDismiss";
import {
AlertTriangle,
Bell,
Check,
CheckCircle2,
ChevronDown,
RefreshCcw,
Search
} from "lucide-react";
import React from "react";

import { api } from "../api/client";
import { alertSeverityLabel, alertSeverityTone, formatDate, isActionableAlert, titleCase } from "../lib/format";
import { Badge, EmptyState, ErrorState, LoadingState, Toolbar } from "../ui/primitives";
import type { AlertSeverity, Anomaly } from "../api/types";
import { useHistoryPage } from "./useHistoryPage";



function alertMatchesFocus(alert: Anomaly, focusedAlertId: string) {
  return Boolean(focusedAlertId && (alert.id === focusedAlertId || alert.alert_ids.includes(focusedAlertId)));
}

function alertDomId(alertId: string) {
  return `alert-row-${alertId.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

export type AlertActionTarget = {
  alert: Anomaly;
  action: "resolve" | "reopen";
};

export function AlertsView({ refreshDashboard, refreshToken, resetToken, targetId }: { refreshDashboard: () => Promise<void>; refreshToken: number; resetToken: number; targetId: string | null }) {
  const [focused, setFocused] = React.useState<Anomaly | null>(null);
  const [statusFilter, setStatusFilter] = React.useState<"open" | "resolved" | "all">("open");
  const [severityFilter, setSeverityFilter] = React.useState<"all" | AlertSeverity>("all");
  const [typeFilter, setTypeFilter] = React.useState("all");
  const [query, setQuery] = React.useState("");
  const [from, setFrom] = React.useState("");
  const [to, setTo] = React.useState("");
  const focusedAlertId = targetId ?? "";
  const [error, setError] = React.useState("");
  const [actionTarget, setActionTarget] = React.useState<AlertActionTarget | null>(null);
  const [resolutionNote, setResolutionNote] = React.useState("");
  const [actionLoading, setActionLoading] = React.useState(false);
  const actionInFlightRef = React.useRef(false);
  const modalRef = React.useRef<HTMLFormElement>(null);
  const closeResolution = useModalClose(modalRef, () => { setActionTarget(null); setResolutionNote(""); });
  const dismissResolution = useEditorDismiss(closeResolution,
    Boolean(resolutionNote.trim()), actionLoading, "resolution note");
  useModalFocus(modalRef, actionTarget?.action === "resolve", dismissResolution);
  const deferredQuery = React.useDeferredValue(query);
  const params = new URLSearchParams({ status: statusFilter });
  if (severityFilter !== "all") params.set("severity", severityFilter);
  if (typeFilter !== "all") params.set("type", typeFilter);
  if (deferredQuery.trim()) params.set("q", deferredQuery.trim());
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const history = useHistoryPage<Anomaly>("/api/v1/alerts/history", params, refreshToken, resetToken);
  const alerts = focused && !history.items.some((alert) => alertMatchesFocus(alert, focusedAlertId))
    ? [focused, ...history.items] : history.items;

  React.useEffect(() => {
    if (history.loading) return;
    if (!focusedAlertId || history.items.some((alert) => alertMatchesFocus(alert, focusedAlertId))) {
      setFocused(null);
      return;
    }
    let active = true;
    void api.get<Anomaly>(`/api/v1/alerts/${encodeURIComponent(focusedAlertId)}`)
      .then((item) => { if (active) setFocused(item); })
      .catch((focusError: unknown) => { if (active) setError(focusError instanceof Error ? focusError.message : "Alert unavailable."); });
    return () => { active = false; };
  }, [focusedAlertId, history.items, history.loading]);

  React.useEffect(() => {
    if (!focusedAlertId || history.loading) return;
    const target = alerts.find((alert) => alertMatchesFocus(alert, focusedAlertId));
    if (!target) return;
    window.requestAnimationFrame(() => {
      const node = document.getElementById(alertDomId(target.id));
      node?.scrollIntoView({ behavior: "smooth", block: "center" });
      node?.focus({ preventScroll: true });
    });
  }, [alerts, focusedAlertId, history.loading]);

  const actOnAlert = async (target: AlertActionTarget, note?: string) => {
    if (actionInFlightRef.current) return;
    actionInFlightRef.current = true;
    setActionLoading(true);
    setError("");
    try {
      const cleanedNote = note?.trim() || null;
      if (target.alert.grouped) {
        if (!target.alert.member_hash) throw new Error("This group must be refreshed before resolving.");
        const confirmation = await api.post<{ confirmation_token: string; count: number }>("/api/v1/alerts/groups/confirmation", {
          group_id: target.alert.id, as_of: target.alert.as_of || history.asOf,
          member_hash: target.alert.member_hash, count: target.alert.count, note: cleanedNote
        });
        if (confirmation.count !== target.alert.count) throw new Error("Alert group changed. Refresh and review it again.");
        await api.patch("/api/v1/alerts/action", {
          group_id: target.alert.id, confirmation_token: confirmation.confirmation_token,
          action: target.action, note: cleanedNote
        });
      } else {
        await api.patch("/api/v1/alerts/action", {
          alert_ids: target.alert.alert_ids, action: target.action, note: cleanedNote
        });
      }
      await closeResolution();
      history.refresh();
      try { await refreshDashboard(); } catch { setError("Alert saved, but dashboard refresh is unavailable."); }
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Unable to update alert");
    } finally {
      actionInFlightRef.current = false;
      setActionLoading(false);
    }
  };

  const openCount = history.items.filter((alert) => alert.status === "open").length;
  const actionableCount = history.items.filter(isActionableAlert).length;
  const resolvedCount = history.items.filter((alert) => alert.status === "resolved").length;
  const hasFilters = Boolean(query || from || to || severityFilter !== "all" || typeFilter !== "all" || statusFilter !== "open");
  const statusOptions = ["open", "resolved", "all"] as const;
  const handleStatusKey = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    const index = statusOptions.indexOf(statusFilter);
    const nextIndex = event.key === "ArrowRight" ? (index + 1) % statusOptions.length
      : event.key === "ArrowLeft" ? (index + statusOptions.length - 1) % statusOptions.length
      : event.key === "Home" ? 0 : event.key === "End" ? statusOptions.length - 1 : null;
    if (nextIndex === null) return;
    event.preventDefault();
    setStatusFilter(statusOptions[nextIndex]);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role=tab]")[nextIndex]?.focus();
  };

  return (
    <section className="view-stack alerts-page">
      <Toolbar title="Alerts" icon={Bell}>
        <button className="secondary-button" onClick={history.refresh} disabled={history.loading} type="button">
          <RefreshCcw size={15} /> Refresh
        </button>
      </Toolbar>

      {!history.loading && !history.error ? <div className="alerts-count-strip" aria-label="Alert group counts on this page">
        <span><strong>{actionableCount}</strong> need action</span>
        <span><strong>{openCount}</strong> open</span>
        <span><strong>{resolvedCount}</strong> resolved</span>
      </div> : null}

      <div className="alerts-controls">
        <div className="alert-status-tabs" role="tablist" aria-label="Alert status">
          {statusOptions.map((value) => (
            <button className={statusFilter === value ? "active" : ""} aria-selected={statusFilter === value} aria-controls="alert-results" id={`alert-status-${value}`} tabIndex={statusFilter === value ? 0 : -1} role="tab" key={value} onClick={() => setStatusFilter(value)} onKeyDown={handleStatusKey} type="button">
              {titleCase(value)}
            </button>
          ))}
        </div>
        <label className="search alerts-search">
          <Search size={16} />
          <input aria-label="Search alerts" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search plate, message, or context..." />
        </label>
        <div className="alerts-select">
        <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value as typeof severityFilter)} aria-label="Filter by severity">
          <option value="all">All severities</option>
          <option value="critical">Critical</option>
          <option value="warning">Warning</option>
          <option value="info">Informational</option>
        </select>
        <ChevronDown size={16} aria-hidden="true" />
        </div>
        <div className="alerts-select">
        <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)} aria-label="Filter by alert type">
          <option value="all">All types</option>
          <option value="unauthorized_plate">Unknown plate</option>
          <option value="outside_schedule">Outside schedule</option>
          <option value="duplicate_entry">Duplicate entry</option>
          <option value="duplicate_exit">Duplicate exit</option>
        </select>
        <ChevronDown size={16} aria-hidden="true" />
        </div>
        <div className="history-date-range">
        <label className="history-date-field">From<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
        <label className="history-date-field">Before<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
        </div>
        {(query || from || to || severityFilter !== "all" || typeFilter !== "all" || statusFilter !== "open") ? <button className="secondary-button" type="button" onClick={() => { setQuery(""); setFrom(""); setTo(""); setSeverityFilter("all"); setTypeFilter("all"); setStatusFilter("open"); }}>Clear filters</button> : null}
        <p className="history-date-hint">Dates use the site timezone. Before excludes the selected date.</p>
      </div>

      <div className="alert-results" id="alert-results" role="tabpanel" aria-labelledby={`alert-status-${statusFilter}`}>
      {history.newActivity ? <button className="history-new-activity" type="button" onClick={history.refresh}>New activity available. Return to latest.</button> : null}
      {history.error ? <ErrorState title="Alerts unavailable" description={history.error} onRetry={history.refresh} retrying={history.loading} /> : null}
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      {focused ? <div className="callout">Showing the selected alert from outside this page.</div> : null}
      {history.loading ? (
        <LoadingState label="Loading alerts" />
      ) : alerts.length ? (
        <div className="alerts-list">
          {alerts.map((alert) => (
            <AlertReviewRow
              alert={alert}
              disabled={actionLoading}
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
        !history.error ? <EmptyState icon={CheckCircle2} label={hasFilters ? "No alerts match these filters." : "No alerts recorded yet."} description={hasFilters ? "Try another status, a wider date range, or clear your filters." : "There are no open alerts to review. New alerts will appear here as they are detected."} /> : null
      )}
      {(history.items.length || history.index) ? <div className="history-pagination" aria-label="Alerts pagination">
        <span>{history.items.length ? `${history.index * 50 + 1}-${history.index * 50 + history.items.length}` : "0"} groups or alerts on this page{history.nextCursor ? "; more available" : ""}</span>
        <div><button className="secondary-button" type="button" disabled={history.index === 0 || history.loading} onClick={history.previous}>Previous</button><span>Page {history.index + 1}</span><button className="secondary-button" type="button" disabled={!history.nextCursor || history.loading} onClick={history.next}>Next</button></div>
      </div> : null}

      </div>

      {actionTarget?.action === "resolve" ? (
        <div className="modal-backdrop" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) dismissResolution(); }}>
          <form
            ref={modalRef}
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
              <button className="secondary-button" disabled={actionLoading} onClick={dismissResolution} type="button">
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

function AlertReviewRow({
  alert,
  focused,
  disabled,
  onResolve,
  onReopen
}: {
  alert: Anomaly;
  focused: boolean;
  disabled: boolean;
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
            <button className="secondary-button" disabled={disabled} onClick={onReopen} type="button">Reopen</button>
          ) : (
            <button className="primary-button" disabled={disabled} onClick={onResolve} type="button">
              <Check size={15} /> Resolve
            </button>
          )}
        </div>
      </div>
    </article>
  );
}
