import {
  ArrowDown,
  ChevronDown,
  Clipboard,
  Clock3,
  Code2,
  Database,
  FileQuestion,
  Link2,
  LoaderCircle,
  SearchX
} from "lucide-react";
import React from "react";
import type { ActivityEpisode, ActivityEpisodeDetail, InvestigationEvidence } from "./types";
import {
  CorrelationNote,
  dispatchExplanation,
  episodeSubject,
  formatDuration,
  formatExactEvidenceTime,
  formatInvestigationTime,
  OutcomeLabel
} from "./presentation";
import { evidenceJson } from "./redaction";

function domId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-");
}

function CopyButton({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = React.useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      setCopied(false);
    }
  }
  return <button aria-label={`Copy ${label}`} className="investigation-copy" onClick={copy} type="button"><Clipboard aria-hidden="true" size={13} /> {copied ? "Copied" : "Copy"}</button>;
}

function RawEvidence({ value }: { value: unknown }) {
  const [open, setOpen] = React.useState(false);
  const rendered = React.useMemo(() => open ? evidenceJson(value) : "", [open, value]);
  return (
    <details className="investigation-raw" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary><Code2 aria-hidden="true" size={14} /> Sanitised raw evidence</summary>
      {open ? <><CopyButton label="raw evidence" value={rendered} /><pre>{rendered}</pre></> : null}
    </details>
  );
}

function EvidenceStep({ evidence, episodeId, focused, timezone }: { evidence: InvestigationEvidence; episodeId: string; focused: boolean; timezone: string }) {
  const id = `evidence-${domId(episodeId)}-${domId(evidence.id)}`;
  const exactTime = formatExactEvidenceTime(evidence.timestamp, timezone);
  return (
    <li className={focused ? "focused" : ""} id={id} tabIndex={focused ? -1 : undefined}>
      <time dateTime={evidence.timestamp} title={exactTime}>{exactTime}</time>
      <div className="investigation-evidence-marker" aria-hidden="true" />
      <div className="investigation-evidence-copy">
        <div>
          <strong>{evidence.title}</strong>
          {evidence.outcome ? <OutcomeLabel outcome={evidence.outcome} /> : null}
        </div>
        {evidence.description ? <p>{evidence.description}</p> : null}
        <dl className="investigation-evidence-facts">
          {evidence.source ? <><dt>Source</dt><dd>{evidence.source}</dd></> : null}
          {evidence.reason_code ? <><dt>Reason code</dt><dd><code>{evidence.reason_code}</code></dd></> : null}
          {evidence.command_sent != null ? <><dt>Command sent</dt><dd>{evidence.command_sent ? "Yes" : "No"}</dd></> : null}
          {evidence.event_id ? <><dt>Event ID</dt><dd><code>{evidence.event_id}</code></dd></> : null}
        </dl>
        {evidence.raw != null ? <RawEvidence value={evidence.raw} /> : null}
      </div>
    </li>
  );
}

function ConfigurationContext({ context }: { context: ActivityEpisodeDetail["configuration_context"][number] }) {
  const redacted = evidenceJson(context.value);
  const isSimple = context.value == null || ["string", "number", "boolean"].includes(typeof context.value);
  const facts = !isSimple && context.value && typeof context.value === "object" && !Array.isArray(context.value)
    ? Object.entries(context.value as Record<string, unknown>).slice(0, 6)
    : [];
  return (
    <div className="investigation-configuration-context">
      <Database aria-hidden="true" size={14} />
      <span>
        <strong>{context.label}</strong>
        <small>{context.recorded_at_decision_time ? "Captured at evaluation" : context.warning || "Current configuration; historical value not recorded"}</small>
        {isSimple ? <code className="investigation-configuration-value">{redacted}</code> : null}
        {facts.length ? <dl>{facts.map(([key, value]) => <React.Fragment key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{typeof value === "object" ? evidenceJson(value) : String(value)}</dd></React.Fragment>)}</dl> : null}
        {!isSimple ? <RawEvidence value={context.value} /> : null}
      </span>
    </div>
  );
}

function EpisodeEvidence({
  detail,
  error,
  focusedEvidenceId,
  loading,
  timezone
}: {
  detail?: ActivityEpisodeDetail;
  error?: string;
  focusedEvidenceId?: string;
  loading: boolean;
  timezone: string;
}) {
  if (loading) return <div className="investigation-detail-loading"><LoaderCircle aria-hidden="true" className="spin" size={16} /> Loading supporting evidence…</div>;
  if (error) return <div className="investigation-inline-error" role="alert">{error}</div>;
  if (!detail) return null;
  return (
    <div className="investigation-episode-detail">
      {detail.timeline.length ? (
        <ol className="investigation-evidence-list">
          {detail.timeline.map((evidence) => <EvidenceStep evidence={evidence} episodeId={detail.episode.episode_id} focused={focusedEvidenceId === evidence.id} key={evidence.id} timezone={timezone} />)}
        </ol>
      ) : <div className="investigation-no-evidence"><FileQuestion aria-hidden="true" size={17} /> No lower-level evidence was retained for this activity.</div>}
      {detail.configuration_context.length ? (
        <section className="investigation-configuration-list" aria-label="Configuration context">
          <h4>Configuration used</h4>
          {detail.configuration_context.map((context, index) => <ConfigurationContext context={context} key={`${context.label}-${index}`} />)}
        </section>
      ) : null}
      <RawEvidence value={detail.raw} />
    </div>
  );
}

function EpisodeRow({
  detail,
  error,
  episode,
  expanded,
  focusedEvidenceId,
  loading,
  onToggle,
  timezone
}: {
  detail?: ActivityEpisodeDetail;
  error?: string;
  episode: ActivityEpisode;
  expanded: boolean;
  focusedEvidenceId?: string;
  loading: boolean;
  onToggle: () => void;
  timezone: string;
}) {
  const dispatch = dispatchExplanation(episode);
  const duration = formatDuration(episode.duration_ms);
  return (
    <article className={`investigation-episode ${expanded ? "expanded" : ""}`} id={`episode-${domId(episode.episode_id)}`}>
      <button aria-expanded={expanded} className="investigation-episode-toggle" onClick={onToggle} type="button">
        <time dateTime={episode.occurred_at}>
          <strong>{formatInvestigationTime(episode.occurred_at, timezone)}</strong>
          <span>{new Intl.DateTimeFormat(undefined, { timeZone: timezone, day: "2-digit", month: "short" }).format(new Date(episode.occurred_at))}</span>
        </time>
        <span className="investigation-episode-summary">
          <span className="investigation-episode-kicker">{episodeSubject(episode)}{episode.automation?.name ? ` · ${episode.automation.name}` : ""}</span>
          <strong>{episode.title}</strong>
          <small>{episode.summary}</small>
          <CorrelationNote episode={episode} />
        </span>
        <span className="investigation-episode-status">
          <OutcomeLabel outcome={episode.outcome} />
          {duration ? <small><Clock3 aria-hidden="true" size={12} /> {duration}</small> : null}
        </span>
        <ChevronDown aria-hidden="true" className="investigation-expand-icon" size={17} />
      </button>
      {expanded ? (
        <div className="investigation-episode-expanded">
          <div className={`investigation-dispatch-state ${episode.dispatch_state || "unknown"}`}>
            <Link2 aria-hidden="true" size={15} />
            <span><strong>{dispatch.label}</strong><small>{dispatch.detail}</small></span>
          </div>
          <dl className="investigation-episode-identifiers">
            {episode.reason_code ? <><dt>Reason code</dt><dd><code>{episode.reason_code}</code></dd></> : null}
            {episode.trace_id ? <><dt>Trace ID</dt><dd><code>{episode.trace_id}</code><CopyButton label="trace ID" value={episode.trace_id} /></dd></> : null}
            {episode.automation?.run_id ? <><dt>Automation run</dt><dd><code>{episode.automation.run_id}</code></dd></> : null}
            {episode.actor ? <><dt>Initiated by</dt><dd>{episode.actor}</dd></> : null}
            {episode.source ? <><dt>Source subsystem</dt><dd>{episode.source}</dd></> : null}
          </dl>
          <EpisodeEvidence detail={detail} error={error} focusedEvidenceId={focusedEvidenceId} loading={loading} timezone={timezone} />
        </div>
      ) : null}
    </article>
  );
}

export function ActivityTimeline({
  details,
  detailErrors,
  focusedEvidenceId,
  hasFilters,
  items,
  loading,
  loadingDetailIds,
  loadingMore,
  nextCursor,
  onLoadDetail,
  onLoadMore,
  partial,
  requestedEpisodeId,
  timezone
}: {
  details: Record<string, ActivityEpisodeDetail>;
  detailErrors: Record<string, string>;
  focusedEvidenceId?: string;
  hasFilters: boolean;
  items: ActivityEpisode[];
  loading: boolean;
  loadingDetailIds: Set<string>;
  loadingMore: boolean;
  nextCursor: string | null;
  onLoadDetail: (episodeId: string) => void;
  onLoadMore: () => void;
  partial: boolean;
  requestedEpisodeId?: string;
  timezone: string;
}) {
  const [expandedIds, setExpandedIds] = React.useState<Set<string>>(() => new Set());

  React.useEffect(() => {
    if (!requestedEpisodeId) return;
    setExpandedIds((current) => new Set(current).add(requestedEpisodeId));
    onLoadDetail(requestedEpisodeId);
    window.setTimeout(() => document.getElementById(`episode-${domId(requestedEpisodeId)}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }, [onLoadDetail, requestedEpisodeId]);

  React.useEffect(() => {
    if (!requestedEpisodeId || !focusedEvidenceId || !details[requestedEpisodeId]) return;
    window.setTimeout(() => document.getElementById(`evidence-${domId(requestedEpisodeId)}-${domId(focusedEvidenceId)}`)?.focus(), 0);
  }, [details, focusedEvidenceId, requestedEpisodeId]);

  function toggle(episodeId: string) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(episodeId)) next.delete(episodeId);
      else {
        next.add(episodeId);
        onLoadDetail(episodeId);
      }
      return next;
    });
  }

  const pinnedDetail = requestedEpisodeId && !items.some((item) => item.episode_id === requestedEpisodeId) ? details[requestedEpisodeId] : undefined;
  if (loading) return <section className="investigation-timeline-shell" aria-busy="true" aria-label="Loading activity"><div className="investigation-timeline-loading"><LoaderCircle aria-hidden="true" className="spin" size={20} /><strong>Building the activity timeline</strong><span>Correlating recorded events into understandable episodes…</span></div></section>;
  return (
    <section className="investigation-timeline-shell" aria-labelledby="investigation-timeline-title">
      <div className="investigation-section-heading timeline">
        <div><span className="investigation-eyebrow"><Clock3 aria-hidden="true" size={14} /> Evidence timeline</span><h2 id="investigation-timeline-title">Recorded activity</h2></div>
        <span>{timezone}</span>
      </div>
      {partial ? <div className="investigation-partial" role="status">Some evidence sources were unavailable. Results below may be incomplete.</div> : null}
      {pinnedDetail ? (
        <div className="investigation-pinned-episode">
          <span>Selected investigation</span>
          <EpisodeRow detail={pinnedDetail} episode={pinnedDetail.episode} error={detailErrors[pinnedDetail.episode.episode_id]} expanded focusedEvidenceId={focusedEvidenceId} loading={false} onToggle={() => toggle(pinnedDetail.episode.episode_id)} timezone={timezone} />
        </div>
      ) : null}
      {!items.length ? (
        <div className="investigation-timeline-empty">
          {hasFilters ? <SearchX aria-hidden="true" size={24} /> : <FileQuestion aria-hidden="true" size={24} />}
          <strong>{hasFilters ? "No activity matched these filters" : "No activity was recorded in this period"}</strong>
          <span>{hasFilters ? "Change or reset the filters. IACS will not invent missing evidence." : "Try a longer time range or include routine activity."}</span>
        </div>
      ) : (
        <div className="investigation-timeline-list">
          {items.map((episode) => <EpisodeRow detail={details[episode.episode_id]} episode={episode} error={detailErrors[episode.episode_id]} expanded={expandedIds.has(episode.episode_id)} focusedEvidenceId={requestedEpisodeId === episode.episode_id ? focusedEvidenceId : undefined} key={episode.episode_id} loading={loadingDetailIds.has(episode.episode_id)} onToggle={() => toggle(episode.episode_id)} timezone={timezone} />)}
        </div>
      )}
      {nextCursor ? <button className="investigation-load-more" disabled={loadingMore} onClick={onLoadMore} type="button">{loadingMore ? <LoaderCircle aria-hidden="true" className="spin" size={15} /> : <ArrowDown aria-hidden="true" size={15} />}{loadingMore ? "Loading…" : "Load older activity"}</button> : null}
    </section>
  );
}
