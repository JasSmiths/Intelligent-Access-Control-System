import {
ArrowLeft,
ArrowRight,
Clock3,
FileImage,
RefreshCw,
} from "lucide-react";
import React from "react";

import { api } from "../api/client";
import { formatDate, movementSagaDisplay, visitorEventDisplayName } from "../lib/format";
import { mediaVariantUrl } from "../lib/media";
import { Badge, EmptyState, Toolbar } from "../ui/primitives";
import type { AccessEvent } from "../api/types";
import { useHistoryPage } from "./useHistoryPage";



export const EventSnapshotThumb = React.memo(function EventSnapshotThumb({ event }: { event: AccessEvent }) {
  const thumbRef = React.useRef<HTMLSpanElement | null>(null);
  const [thumbVisible, setThumbVisible] = React.useState(false);
  const [previewOpen, setPreviewOpen] = React.useState(false);
  const label = `Snapshot for ${visitorEventDisplayName(event) || event.registration_number}`;
  React.useEffect(() => {
    if (!event.snapshot_url || thumbVisible) return undefined;
    const target = thumbRef.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setThumbVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      setThumbVisible(true);
      observer.disconnect();
    }, { rootMargin: "180px" });
    observer.observe(target);
    return () => observer.disconnect();
  }, [event.snapshot_url, thumbVisible]);

  if (!event.snapshot_url) {
    return (
      <span className="event-snapshot-placeholder" aria-hidden="true">
        <FileImage size={16} />
      </span>
    );
  }
  return (
    <span
      className="event-snapshot-thumb"
      ref={thumbRef}
      onBlur={() => setPreviewOpen(false)}
      onFocus={() => {
        setThumbVisible(true);
        setPreviewOpen(true);
      }}
      onMouseEnter={() => {
        setThumbVisible(true);
        setPreviewOpen(true);
      }}
      onMouseLeave={() => setPreviewOpen(false)}
      tabIndex={0}
    >
      {thumbVisible ? (
        <img alt={label} decoding="async" loading="lazy" src={mediaVariantUrl(event.snapshot_url, "thumb")} />
      ) : (
        <FileImage size={16} aria-hidden="true" />
      )}
      {previewOpen ? (
        <span className="event-snapshot-preview" aria-hidden="true">
          <img alt="" decoding="async" loading="lazy" src={event.snapshot_url} />
        </span>
      ) : null}
    </span>
  );
});

export function EventsView({ refreshToken, resetToken, targetId }: { refreshToken: number; resetToken: number; targetId: string | null }) {
  const [query, setQuery] = React.useState("");
  const [from, setFrom] = React.useState("");
  const [to, setTo] = React.useState("");
  const [decision, setDecision] = React.useState("");
  const [focused, setFocused] = React.useState<AccessEvent | null>(null);
  const [focusError, setFocusError] = React.useState("");
  const deferredQuery = React.useDeferredValue(query);
  const filters = new URLSearchParams();
  if (deferredQuery.trim()) filters.set("q", deferredQuery.trim());
  if (from) filters.set("from", from);
  if (to) filters.set("to", to);
  if (decision) filters.set("decision", decision);
  const history = useHistoryPage<AccessEvent>("/api/v1/events/history", filters, refreshToken, resetToken);
  React.useEffect(() => {
    if (!targetId) { setFocused(null); return; }
    if (history.loading) return;
    if (history.items.some((item) => item.id === targetId)) { setFocused(null); return; }
    let active = true;
    void api.get<AccessEvent>(`/api/v1/events/${encodeURIComponent(targetId)}`)
      .then((item) => { if (active) { setFocused(item); setFocusError(""); } })
      .catch((error: unknown) => { if (active) setFocusError(error instanceof Error ? error.message : "Event unavailable."); });
    return () => { active = false; };
  }, [targetId, history.items, history.loading]);
  const visible = focused ? [focused, ...history.items.filter((item) => item.id !== focused.id)] : history.items;
  const firstItem = history.index * 50 + 1;
  const lastItem = history.index * 50 + history.items.length;

  return (
    <section className="view-stack">
      <Toolbar title="Events" icon={Clock3}>
        <button className="secondary-button" type="button" onClick={history.refresh} disabled={history.loading}><RefreshCw size={15} /> Refresh</button>
      </Toolbar>
      <div className="history-filters" aria-label="Event filters">
        <label>Search events<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Plate or source" /></label>
        <label>From<input type="date" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
        <label>Before<input type="date" value={to} onChange={(event) => setTo(event.target.value)} /></label>
        <label>Decision<select value={decision} onChange={(event) => setDecision(event.target.value)}><option value="">All decisions</option><option value="granted">Granted</option><option value="denied">Denied</option></select></label>
        {(query || from || to || decision) ? <button className="secondary-button" type="button" onClick={() => { setQuery(""); setFrom(""); setTo(""); setDecision(""); }}>Clear filters</button> : null}
        <p className="history-date-hint">Dates use the site timezone. Before excludes the selected date.</p>
      </div>
      {history.newActivity ? <button className="history-new-activity" type="button" onClick={history.refresh}>New activity available. Return to latest.</button> : null}
      {history.error ? <div className="callout danger" role="alert">Events unavailable: {history.error}</div> : null}
      {focusError ? <div className="callout danger" role="alert">{focusError}</div> : null}
      {focused ? <div className="callout">Showing the selected event from outside this page.</div> : null}
      <div className="table-card events-table-card">
        <table>
          <thead>
            <tr>
              <th>Snapshot</th>
              <th>Plate</th>
              <th>Direction</th>
              <th>Decision</th>
              <th>Movement</th>
              <th>Confidence</th>
              <th>When</th>
              <th>Alerts</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((event) => {
              const movement = movementSagaDisplay(event.movement_saga);
              return (
                <tr key={event.id} id={`event-${event.id}`}>
                  <td className="event-snapshot-cell">
                    <EventSnapshotThumb event={event} />
                  </td>
                  <td>
                    <strong>{event.registration_number}</strong>
                    {event.visitor_name ? <span className="table-muted-line">{visitorEventDisplayName(event)}</span> : null}
                  </td>
                  <td>{event.direction}</td>
                  <td><Badge tone={event.decision === "granted" ? "green" : "red"}>{event.decision}</Badge></td>
                  <td>{movement ? <Badge tone={movement.tone}>{movement.label}</Badge> : <span className="table-muted-line">--</span>}</td>
                  <td>{Math.round(event.confidence * 100)}%</td>
                  <td>{formatDate(event.occurred_at)}</td>
                  <td>{event.anomaly_count}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {history.items.length || history.index ? (
          <div
            className="top-charts-pagination"
            style={{ padding: "8px 12px", borderTop: "1px solid var(--line)" }}
            aria-label="Events pagination"
          >
            <span>{history.items.length ? `${firstItem}-${lastItem}` : "0"} records on this page{history.nextCursor ? "; more available" : ""}</span>
            <div className="top-charts-pagination-controls">
              <button
                aria-label="Previous page"
                className="icon-button top-charts-page-button"
                disabled={history.index === 0 || history.loading}
                onClick={history.previous}
                type="button"
              >
                <ArrowLeft size={15} />
              </button>
              <span>Page {history.index + 1}</span>
              <button
                aria-label="Next page"
                className="icon-button top-charts-page-button"
                disabled={!history.nextCursor || history.loading}
                onClick={history.next}
                type="button"
              >
                <ArrowRight size={15} />
              </button>
            </div>
          </div>
        ) : null}
        {history.loading ? <div className="loading-panel">Loading events…</div> : null}
        {!history.loading && !history.error && !visible.length ? <EmptyState icon={Clock3} label={query || from || to || decision ? "No events match these filters." : "No events recorded yet."} /> : null}
      </div>
    </section>
  );
}
