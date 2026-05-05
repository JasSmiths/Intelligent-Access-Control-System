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
  formatDate,
  initials,
  matches,
  RealtimeMessage,
  titleCase,
  Toolbar,
  TooltipPositionState
} from "../shared";



export type LeaderboardPerson = {
  id: string | null;
  first_name: string;
  last_name: string;
  display_name: string;
  profile_photo_data_url: string | null;
};

export type LeaderboardVehicle = {
  id: string | null;
  registration_number: string;
  vehicle_photo_data_url: string | null;
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

export const TOP_CHARTS_PAGE_SIZE = 5;

export function TopChartsView({ query, realtime, refreshToken }: { query: string; realtime: RealtimeMessage[]; refreshToken: number }) {
  const [leaderboard, setLeaderboard] = React.useState<LeaderboardResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [refreshing, setRefreshing] = React.useState(false);
  const [error, setError] = React.useState("");
  const [knownPage, setKnownPage] = React.useState(0);
  const [unknownPage, setUnknownPage] = React.useState(0);
  const lastRefreshTokenRef = React.useRef(refreshToken);

  const load = React.useCallback(async () => {
    setRefreshing(true);
    setError("");
    try {
      setLeaderboard(await api.get<LeaderboardResponse>("/api/v1/leaderboard"));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load Top Charts.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  React.useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  React.useEffect(() => {
    if (lastRefreshTokenRef.current === refreshToken) return;
    lastRefreshTokenRef.current = refreshToken;
    load().catch(() => undefined);
  }, [load, refreshToken]);

  const latestRealtime = realtime[0];
  React.useEffect(() => {
    if (!latestRealtime) return;
    if (latestRealtime.type === "access_event.finalized" || latestRealtime.type === "leaderboard_overtake") {
      load().catch(() => undefined);
    }
  }, [latestRealtime?.created_at, latestRealtime?.type, load]);

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

      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? (
        <div className="loading-panel">Loading Top Charts</div>
      ) : (
        <div className="top-charts-grid">
          <section className="card top-charts-card top-charts-known-card">
            <div className="top-charts-card-header">
              <div>
                <span className="eyebrow">Known Plates</span>
                <h2>The VIP Lounge</h2>
                <p>Known plates battling for driveway supremacy.</p>
              </div>
              <Badge tone="green">{knownReadCount} Detectiions</Badge>
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
              <EmptyState icon={Trophy} label="No VIP Detectiions yet" />
            )}
          </section>

          <section className="card top-charts-card top-charts-unknown-card">
            <div className="top-charts-card-header">
              <div>
                <span className="eyebrow">Unknown Plates</span>
                <h2>The Mystery Guests</h2>
                <p>Unrecognized plates ranked by repeat visits.</p>
              </div>
              <Badge tone="amber">{unknownReadCount} Detectiions</Badge>
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
              <EmptyState icon={Search} label="No mystery guests yet" />
            )}
          </section>
        </div>
      )}
    </section>
  );
}

export function LeaderboardKnownRow({ entry }: { entry: LeaderboardKnownEntry }) {
  const firstName = entry.person.first_name || entry.first_name || entry.display_name.split(" ")[0] || "VIP";
  return (
    <article className="top-charts-row">
      <span className={rankBadgeClass(entry.rank)}>{entry.rank}</span>
      <LeaderboardAvatar imageUrl={entry.person.profile_photo_data_url} name={entry.person.display_name || firstName} />
      <div className="top-charts-row-main">
        <strong>{firstName}</strong>
        <span>{entry.vehicle_name || entry.vehicle.display_name || "Vehicle details pending"}</span>
        <small>{entry.registration_number}</small>
      </div>
      <div className="top-charts-read-count">
        <strong>{entry.read_count}</strong>
        <span>{entry.read_count === 1 ? "Detectiion" : "Detectiions"}</span>
      </div>
    </article>
  );
}

export function LeaderboardUnknownRow({ entry }: { entry: LeaderboardUnknownEntry }) {
  const label = entry.dvla.label || "DVLA details unavailable";
  const showStatus = entry.dvla.status && entry.dvla.status !== "ok";
  return (
    <article className="top-charts-row">
      <span className={rankBadgeClass(entry.rank)}>{entry.rank}</span>
      <LeaderboardSnapshotThumb entry={entry} />
      <div className="top-charts-row-main">
        <strong>{entry.registration_number}</strong>
        <span>{label}</span>
        <small>{mysteryGuestQuip(entry.rank)}</small>
      </div>
      <div className="top-charts-read-count">
        {showStatus ? <Badge tone={leaderboardDvlaTone(entry.dvla.status)}>{leaderboardDvlaLabel(entry.dvla.status)}</Badge> : null}
        <strong>{entry.read_count}</strong>
        <span>{entry.read_count === 1 ? "Detectiion" : "Detectiions"}</span>
      </div>
    </article>
  );
}

export function LeaderboardSnapshotThumb({ entry }: { entry: LeaderboardUnknownEntry }) {
  const snapshot = entry.latest_snapshot;
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
    if (!snapshot?.url) return;
    const tooltipWidth = Math.min(336, window.innerWidth - 24);
    const tooltipHeight = Math.round((tooltipWidth - 16) * 9 / 16) + 56;
    const rect = target.getBoundingClientRect();
    const gap = 10;
    const placement = rect.bottom + gap + tooltipHeight > window.innerHeight - 8 ? "top" : "bottom";
    const left = Math.max(12 + tooltipWidth / 2, Math.min(rect.left + rect.width / 2, window.innerWidth - tooltipWidth / 2 - 12));
    const top = placement === "bottom"
      ? Math.min(window.innerHeight - tooltipHeight - 8, rect.bottom + gap)
      : Math.max(8, rect.top - tooltipHeight - gap);
    setTooltipPosition({ left, placement, top });
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
      onMouseLeave={() => setTooltipPosition(null)}
      type="button"
    >
      <img alt="" loading="lazy" src={snapshot.url} />
      {tooltipPosition ? createPortal(
        <div
          className={`iacs-tooltip top-charts-snapshot-tooltip ${tooltipPosition.placement}`}
          id={tooltipId}
          role="tooltip"
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
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

export function TopChartsPagination({
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

export function LeaderboardAvatar({ imageUrl, name }: { imageUrl: string | null; name: string }) {
  return (
    <span className="top-charts-avatar" aria-label={name}>
      {imageUrl ? <img alt="" src={imageUrl} /> : initials(name).toUpperCase()}
    </span>
  );
}

export function leaderboardKnownMatches(entry: LeaderboardKnownEntry, query: string) {
  return (
    matches(entry.registration_number, query) ||
    matches(entry.display_name, query) ||
    matches(entry.person.display_name, query) ||
    matches(entry.vehicle_name, query)
  );
}

export function leaderboardUnknownMatches(entry: LeaderboardUnknownEntry, query: string) {
  return (
    matches(entry.registration_number, query) ||
    matches(entry.dvla.label, query) ||
    matches(String(entry.dvla.error ?? ""), query)
  );
}

export function rankBadgeClass(rank: number) {
  if (rank === 1) return "rank-badge rank-badge-gold";
  if (rank === 2) return "rank-badge rank-badge-silver";
  if (rank === 3) return "rank-badge rank-badge-bronze";
  return "rank-badge";
}

export function leaderboardDvlaTone(status: string): BadgeTone {
  if (status === "unconfigured") return "gray";
  if (status === "failed") return "amber";
  return "gray";
}

export function leaderboardDvlaLabel(status: string) {
  if (status === "unconfigured") return "DVLA off";
  if (status === "failed") return "DVLA failed";
  return titleCase(status);
}

export function mysteryGuestQuip(rank: number) {
  if (rank === 1) return "Chief driveway plot twist";
  if (rank === 2) return "Strong encore energy";
  if (rank === 3) return "Podium-level mystery";
  return "Still under investigation";
}
