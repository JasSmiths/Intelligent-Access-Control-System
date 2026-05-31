import {
ArrowLeft,
ArrowRight,
Clock3,
FileImage,
} from "lucide-react";
import React from "react";

import {
AccessEvent,
Badge,
formatDate,
matches,
mediaVariantUrl,
movementSagaDisplay,
Toolbar,
visitorEventDisplayName
} from "../shared";



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

const EVENTS_PAGE_SIZE = 7;

export function EventsView({ events, query }: { events: AccessEvent[]; query: string }) {
  const deferredQuery = React.useDeferredValue(query);
  const [page, setPage] = React.useState(0);
  const filtered = React.useMemo(
    () => events.filter(
      (item) =>
        matches(item.registration_number, deferredQuery) ||
        matches(item.source, deferredQuery) ||
        matches(item.visitor_name || "", deferredQuery)
    ),
    [deferredQuery, events]
  );

  const pageCount = React.useMemo(
    () => Math.max(1, Math.ceil(filtered.length / EVENTS_PAGE_SIZE)),
    [filtered.length]
  );
  const pagedEvents = React.useMemo(
    () => filtered.slice(page * EVENTS_PAGE_SIZE, (page + 1) * EVENTS_PAGE_SIZE),
    [page, filtered]
  );

  const firstItem = page * EVENTS_PAGE_SIZE + 1;
  const lastItem = Math.min(filtered.length, (page + 1) * EVENTS_PAGE_SIZE);

  React.useEffect(() => {
    setPage(0);
  }, [deferredQuery]);

  React.useEffect(() => {
    setPage((p) => Math.min(p, pageCount - 1));
  }, [pageCount]);

  return (
    <section className="view-stack">
      <Toolbar title="Timeline" count={filtered.length} icon={Clock3} />
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
            {pagedEvents.map((event) => {
              const movement = movementSagaDisplay(event.movement_saga);
              return (
                <tr key={event.id}>
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
        {filtered.length > EVENTS_PAGE_SIZE ? (
          <div
            className="top-charts-pagination"
            style={{ padding: "8px 12px", borderTop: "1px solid var(--line)" }}
            aria-label="Events pagination"
          >
            <span>{firstItem}-{lastItem} of {filtered.length}</span>
            <div className="top-charts-pagination-controls">
              <button
                aria-label="Previous page"
                className="icon-button top-charts-page-button"
                disabled={page === 0}
                onClick={() => setPage(Math.max(0, page - 1))}
                type="button"
              >
                <ArrowLeft size={15} />
              </button>
              <span>Page {page + 1} of {pageCount}</span>
              <button
                aria-label="Next page"
                className="icon-button top-charts-page-button"
                disabled={page >= pageCount - 1}
                onClick={() => setPage(Math.min(pageCount - 1, page + 1))}
                type="button"
              >
                <ArrowRight size={15} />
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
