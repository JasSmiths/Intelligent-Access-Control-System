import { getUsableViewportBounds, observeOverlayPlacement, placeOverlay, type OverlayPlacement } from "../lib/viewportPlacement";
import {
ArrowLeft,
ArrowRight,
FileImage,
RefreshCcw,
Search,
Trophy
} from "lucide-react";
import React from "react";
import { createPortal } from "react-dom";

import { api, isAbortError } from "../api/client";
import { formatDate, initials, matches, titleCase } from "../lib/format";
import { mediaSource, mediaVariantUrl } from "../lib/media";
import { Badge, EmptyState, ErrorState, LoadingState, Toolbar } from "../ui/primitives";
import type { RealtimeMessage } from "../api/types";
import type { BadgeTone } from "../ui/primitives";



export type LeaderboardPerson = {
  id: string | null;
  first_name: string;
  last_name: string;
  display_name: string;
  profile_photo_data_url: string | null;
  profile_photo_url?: string | null;
};

export type LeaderboardVehicle = {
  id: string | null;
  registration_number: string;
  vehicle_photo_data_url: string | null;
  vehicle_photo_url?: string | null;
  make: string;
  model: string;
  color: string;
  description: string;
  display_name: string;
};

export type LeaderboardKnownEntry = {
  rank: number;
  registration_number: string;
  read_count: number;
  last_seen_at: string | null;
  vehicle_id: string;
  person_id: string;
  first_name: string;
  display_name: string;
  vehicle_name: string;
  person: LeaderboardPerson;
  vehicle: LeaderboardVehicle;
};

export type LeaderboardDvla = {
  status: string;
  vehicle: Record<string, unknown> | null;
  display_vehicle: Record<string, unknown> | null;
  label: string;
  error?: string;
};

export type LeaderboardSnapshot = {
  event_id: string;
  url: string;
  captured_at: string | null;
  bytes: number | null;
  width: number | null;
  height: number | null;
  camera: string | null;
};

export type LeaderboardUnknownEntry = {
  rank: number;
  registration_number: string;
  read_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  latest_snapshot: LeaderboardSnapshot | null;
  dvla: LeaderboardDvla;
};

export type LeaderboardResponse = {
  known: LeaderboardKnownEntry[];
  unknown: LeaderboardUnknownEntry[];
  top_known: LeaderboardKnownEntry | null;
  generated_at: string;
};

const TOP_CHARTS_PAGE_SIZE = 5;

export function TopChartsView({ query, latestRealtime, refreshToken }: { query: string; latestRealtime: RealtimeMessage | null; refreshToken: number }) {
  const [leaderboard, setLeaderboard] = React.useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [refreshing, setRefreshing] = React.useState(false);
  const [error, setError] = React.useState("");
  const [knownPage, setKnownPage] = React.useState(0);
  const [unknownPage, setUnknownPage] = React.useState(0);
  const lastRefreshTokenRef = React.useRef(refreshToken);
  const loadSequenceRef = React.useRef(0);
  const loadAbortRef = React.useRef<AbortController | null>(null);

  const load = React.useCallback(async () => {
    const sequence = loadSequenceRef.current + 1;
    loadSequenceRef.current = sequence;
    loadAbortRef.current?.abort();
    const controller = new AbortController();
    loadAbortRef.current = controller;
    setRefreshing(true);
    setError("");
    try {
      const nextLeaderboard = await api.get<LeaderboardResponse>("/api/v1/leaderboard", { signal: controller.signal });
      if (loadSequenceRef.current !== sequence) return;
      setLeaderboard(nextLeaderboard);
    } catch (loadError) {
      if (isAbortError(loadError)) return;
      if (loadSequenceRef.current !== sequence) return;
      setError(loadError instanceof Error ? loadError.message : "Unable to load Top Charts.");
    } finally {
      if (loadSequenceRef.current === sequence) {
        setLoading(false);
        setRefreshing(false);
        if (loadAbortRef.current === controller) {
          loadAbortRef.current = null;
        }
      }
    }
  }, []);

  React.useEffect(() => () => {
    loadSequenceRef.current += 1;
    loadAbortRef.current?.abort();
  }, []);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    load().catch(() => undefined);
  }, [load, refreshToken]);

  React.useEffect(() => {
    if (!latestRealtime) return;
    if (latestRealtime.type === "access_event.finalized" || latestRealtime.type === "leaderboard_overtake") {
      load().catch(() => undefined);
    }
  }, [latestRealtime, load]);

  const knownRows = React.useMemo(
    () => (leaderboard?.known ?? []).filter((item) => leaderboardKnownMatches(item, query)),
    [leaderboard?.known, query]
  );
  const unknownRows = React.useMemo(
    () => (leaderboard?.unknown ?? []).filter((item) => leaderboardUnknownMatches(item, query)),
    [leaderboard?.unknown, query]
  );
  const knownReadCount = React.useMemo(
    () => knownRows.reduce((total, item) => total + item.read_count, 0),
    [knownRows]
  );
  const unknownReadCount = React.useMemo(
    () => unknownRows.reduce((total, item) => total + item.read_count, 0),
    [unknownRows]
  );
  const knownPageCount = Math.max(1, Math.ceil(knownRows.length / TOP_CHARTS_PAGE_SIZE));
  const unknownPageCount = Math.max(1, Math.ceil(unknownRows.length / TOP_CHARTS_PAGE_SIZE));
  const visibleKnownRows = React.useMemo(
    () => knownRows.slice(knownPage * TOP_CHARTS_PAGE_SIZE, (knownPage + 1) * TOP_CHARTS_PAGE_SIZE),
    [knownPage, knownRows]
  );
  const visibleUnknownRows = React.useMemo(
    () => unknownRows.slice(unknownPage * TOP_CHARTS_PAGE_SIZE, (unknownPage + 1) * TOP_CHARTS_PAGE_SIZE),
    [unknownPage, unknownRows]
  );

  React.useEffect(() => {
    setKnownPage(0);
    setUnknownPage(0);
  }, [query]);

  React.useEffect(() => {
    setKnownPage((page) => Math.min(page, knownPageCount - 1));
  }, [knownPageCount]);

  React.useEffect(() => {
    setUnknownPage((page) => Math.min(page, unknownPageCount - 1));
  }, [unknownPageCount]);

  return (
    <section className="view-stack top-charts-page">
      <Toolbar title="Top Charts" icon={Trophy}>
        <button className="secondary-button" onClick={() => load().catch(() => undefined)} disabled={refreshing} type="button">
          <RefreshCcw size={15} /> {refreshing ? "Refreshing" : "Refresh"}
        </button>
      </Toolbar>
      <p className="top-charts-scope">All recorded access events through {leaderboard?.generated_at ? formatDate(leaderboard.generated_at) : "the latest refresh"}. Known counts include granted entries linked to a vehicle; unknown counts include denied events without a linked vehicle. Each chart shows up to 25 plates.</p>

      {error ? <ErrorState title="Top Charts unavailable" description={error} onRetry={() => void load()} retrying={refreshing} /> : null}
      {loading || (!leaderboard && refreshing) ? (
        <LoadingState label="Loading Top Charts" />
      ) : leaderboard ? (
        <div className="top-charts-grid">
          <section className="card top-charts-card top-charts-known-card">
            <div className="top-charts-card-header">
              <div>
                <span className="eyebrow">Known Plates</span>
                <h2>Known vehicle entries</h2>
                <p>Ranked by granted entry events.</p>
              </div>
              <Badge tone="green">{knownReadCount} shown events</Badge>
            </div>

            {knownRows.length ? (
              <>
                <div className="top-charts-list">
                  {visibleKnownRows.map((entry) => (
                    <LeaderboardKnownRow entry={entry} key={`${entry.vehicle_id}-${entry.registration_number}`} />
                  ))}
                </div>
                <TopChartsPagination
                  page={knownPage}
                  pageCount={knownPageCount}
                  total={knownRows.length}
                  onPageChange={setKnownPage}
                />
              </>
            ) : (
              <EmptyState icon={Trophy} label={query ? "No known entries match this filter" : "No known vehicle entries recorded"} description={query ? "Try searching for another name or registration." : "Rankings will build as known vehicles receive entry access."} />
            )}
          </section>

          <section className="card top-charts-card top-charts-unknown-card">
            <div className="top-charts-card-header">
              <div>
                <span className="eyebrow">Unknown Plates</span>
                <h2>Unknown denied plates</h2>
                <p>Ranked by denied events without a linked vehicle.</p>
              </div>
              <Badge tone="amber">{unknownReadCount} shown events</Badge>
            </div>

            {unknownRows.length ? (
              <>
                <div className="top-charts-list">
                  {visibleUnknownRows.map((entry) => (
                    <LeaderboardUnknownRow entry={entry} key={entry.registration_number} />
                  ))}
                </div>
                <TopChartsPagination
                  page={unknownPage}
                  pageCount={unknownPageCount}
                  total={unknownRows.length}
                  onPageChange={setUnknownPage}
                />
              </>
            ) : (
              <EmptyState icon={Search} label={query ? "No unknown plates match this filter" : "No unknown denied plates recorded"} description={query ? "Try searching for another registration." : "Denied events for unrecognised plates will appear here."} />
            )}
          </section>
        </div>
      ) : null}
    </section>
  );
}

function LeaderboardKnownRow({ entry }: { entry: LeaderboardKnownEntry }) {
  const firstName = entry.person.first_name || entry.first_name || entry.display_name.split(" ")[0] || "VIP";
  return (
    <article className="top-charts-row">
      <span className={rankBadgeClass(entry.rank)}>{entry.rank}</span>
      <LeaderboardAvatar
        imageUrl={mediaSource(entry.person.profile_photo_url, entry.person.profile_photo_data_url, "thumb")}
        name={entry.person.display_name || firstName}
      />
      <div className="top-charts-row-main">
        <strong>{firstName}</strong>
        <span>{entry.vehicle_name || entry.vehicle.display_name || "Vehicle details pending"}</span>
        <small>{entry.registration_number}</small>
      </div>
      <div className="top-charts-read-count">
        <strong>{entry.read_count}</strong>
        <span>{entry.read_count === 1 ? "Detection" : "Detections"}</span>
      </div>
    </article>
  );
}

function LeaderboardUnknownRow({ entry }: { entry: LeaderboardUnknownEntry }) {
  const label = entry.dvla.label || "DVLA details unavailable";
  const showStatus = entry.dvla.status && entry.dvla.status !== "ok";
  return (
    <article className="top-charts-row">
      <span className={rankBadgeClass(entry.rank)}>{entry.rank}</span>
      <LeaderboardSnapshotThumb entry={entry} />
      <div className="top-charts-row-main">
        <strong>{entry.registration_number}</strong>
        <span>{label}</span>
        <small>{entry.last_seen_at ? `Last seen ${formatDate(entry.last_seen_at)}` : "Last seen time unavailable"}</small>
      </div>
      <div className="top-charts-read-count">
        {showStatus ? <Badge tone={leaderboardDvlaTone(entry.dvla.status)}>{leaderboardDvlaLabel(entry.dvla.status)}</Badge> : null}
        <strong>{entry.read_count}</strong>
        <span>{entry.read_count === 1 ? "Detection" : "Detections"}</span>
      </div>
    </article>
  );
}

function LeaderboardSnapshotThumb({ entry }: { entry: LeaderboardUnknownEntry }) {
  const snapshot = entry.latest_snapshot;
  const tooltipId = React.useId();
  const [tooltipPosition, setTooltipPosition] = React.useState<OverlayPlacement | null>(null);
  const anchorRef = React.useRef<HTMLElement | null>(null);
  const tooltipRef = React.useRef<HTMLDivElement | null>(null);
  const tooltipOpen = tooltipPosition !== null;

  React.useLayoutEffect(() => {
    if (!tooltipOpen) return;
    const anchor = anchorRef.current;
    const overlay = tooltipRef.current;
    if (!anchor || !overlay) return;
    const update = () => {
      if (!anchor.isConnected) { setTooltipPosition(null); return; }
      const rect = overlay.getBoundingClientRect();
      setTooltipPosition(placeOverlay(anchor.getBoundingClientRect(), {
        width: rect.width, height: Math.max(rect.height, overlay.scrollHeight),
      }, getUsableViewportBounds(), { alignment: "center", gap: 10 }));
    };
    update();
    return observeOverlayPlacement(anchor, overlay, update);
  }, [tooltipOpen]);

  const showTooltip = (target: HTMLElement) => {
    if (!snapshot?.url) return;
    anchorRef.current = target;
    // The mounted tooltip is measured before paint; this only establishes its initial bounds.
    setTooltipPosition(placeOverlay(target.getBoundingClientRect(), { width: 336, height: 0 }, getUsableViewportBounds(), { alignment: "center", gap: 10 }));
  };

  if (!snapshot?.url) {
    return (
      <span className="top-charts-plate-avatar top-charts-snapshot-placeholder" aria-label={`No stored snapshot for ${entry.registration_number}`}>
        <FileImage size={17} />
      </span>
    );
  }

  return (
    <button
      aria-describedby={tooltipPosition ? tooltipId : undefined}
      aria-label={`Latest snapshot for ${entry.registration_number}`}
      className="top-charts-snapshot-thumb"
      onBlur={() => setTooltipPosition(null)}
      onFocus={(event) => showTooltip(event.currentTarget)}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          setTooltipPosition(null);
        }
      }}
      onMouseEnter={(event) => showTooltip(event.currentTarget)}
      onMouseLeave={(event) => { if (document.activeElement !== event.currentTarget) setTooltipPosition(null); }}
      type="button"
    >
      <img alt="" decoding="async" loading="lazy" src={mediaVariantUrl(snapshot.url, "thumb")} />
      {tooltipPosition ? createPortal(
        <div
          className={`iacs-tooltip top-charts-snapshot-tooltip ${tooltipPosition.side}`}
          id={tooltipId}
          role="tooltip"
          ref={tooltipRef}
          style={{ left: tooltipPosition.left, top: tooltipPosition.top, maxWidth: tooltipPosition.maxWidth, maxHeight: tooltipPosition.maxHeight, overflow: "auto", pointerEvents: "auto", transform: "none" }}
        >
          <img alt="" loading="lazy" src={snapshot.url} />
          <strong>{entry.registration_number}</strong>
          <span>{snapshot.captured_at ? `Captured ${formatDate(snapshot.captured_at)}` : "Latest stored vehicle snapshot"}</span>
        </div>,
        document.body
      ) : null}
    </button>
  );
}

function TopChartsPagination({
  page,
  pageCount,
  total,
  onPageChange,
}: {
  page: number;
  pageCount: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  const firstItem = page * TOP_CHARTS_PAGE_SIZE + 1;
  const lastItem = Math.min(total, (page + 1) * TOP_CHARTS_PAGE_SIZE);
  return (
    <div className="top-charts-pagination" aria-label="Top Charts pagination">
      <span>{firstItem}-{lastItem} of {total}</span>
      <div className="top-charts-pagination-controls">
        <button
          aria-label="Previous page"
          className="icon-button top-charts-page-button"
          disabled={page === 0}
          onClick={() => onPageChange(Math.max(0, page - 1))}
          type="button"
        >
          <ArrowLeft size={15} />
        </button>
        <span>Page {page + 1} of {pageCount}</span>
        <button
          aria-label="Next page"
          className="icon-button top-charts-page-button"
          disabled={page >= pageCount - 1}
          onClick={() => onPageChange(Math.min(pageCount - 1, page + 1))}
          type="button"
        >
          <ArrowRight size={15} />
        </button>
      </div>
    </div>
  );
}

function LeaderboardAvatar({ imageUrl, name }: { imageUrl: string | null; name: string }) {
  return (
    <span className="top-charts-avatar" aria-label={name}>
      {imageUrl ? <img alt="" decoding="async" loading="lazy" src={imageUrl} /> : initials(name).toUpperCase()}
    </span>
  );
}

function leaderboardKnownMatches(entry: LeaderboardKnownEntry, query: string) {
  return (
    matches(entry.registration_number, query) ||
    matches(entry.display_name, query) ||
    matches(entry.person.display_name, query) ||
    matches(entry.vehicle_name, query)
  );
}

function leaderboardUnknownMatches(entry: LeaderboardUnknownEntry, query: string) {
  return (
    matches(entry.registration_number, query) ||
    matches(entry.dvla.label, query) ||
    matches(String(entry.dvla.error ?? ""), query)
  );
}

function rankBadgeClass(rank: number) {
  if (rank === 1) return "rank-badge rank-badge-gold";
  if (rank === 2) return "rank-badge rank-badge-silver";
  if (rank === 3) return "rank-badge rank-badge-bronze";
  return "rank-badge";
}

function leaderboardDvlaTone(status: string): BadgeTone {
  if (status === "unconfigured") return "gray";
  if (status === "failed") return "amber";
  return "gray";
}

function leaderboardDvlaLabel(status: string) {
  if (status === "unconfigured") return "DVLA off";
  if (status === "failed") return "DVLA failed";
  return titleCase(status);
}
