import { AlertTriangle, ArrowRight, CircleCheck, Repeat2, Sparkles } from "lucide-react";
import type { ActivityEpisode, InvestigationOverview as OverviewType, OverviewRepeat } from "./types";
import { dispatchExplanation, episodeSubject, formatInvestigationTime, OutcomeLabel } from "./presentation";

function OverviewEpisode({ episode, onSelect, timezone }: { episode: ActivityEpisode; onSelect: (id: string) => void; timezone: string }) {
  const dispatch = dispatchExplanation(episode);
  return (
    <li>
      <button onClick={() => onSelect(episode.episode_id)} type="button">
        <time dateTime={episode.occurred_at}>{formatInvestigationTime(episode.occurred_at, timezone, true)}</time>
        <span className="investigation-overview-copy">
          <strong>{episodeSubject(episode)} · {episode.title}</strong>
          <small>{episode.summary}</small>
          {episode.dispatch_state && episode.dispatch_state !== "not_applicable" ? <em>{dispatch.label}</em> : null}
        </span>
        <OutcomeLabel outcome={episode.outcome} />
        <ArrowRight aria-hidden="true" className="investigation-row-arrow" size={15} />
      </button>
    </li>
  );
}

function RepeatItem({ item, onSelect, timezone }: { item: OverviewRepeat; onSelect: (id: string) => void; timezone: string }) {
  const content = (
    <>
      <span className="investigation-repeat-count">{item.count}×</span>
      <span>
        <strong>{item.title}</strong>
        <small>{item.summary || item.reason_code || "The same reason was recorded repeatedly."}</small>
        {item.latest_at ? <time dateTime={item.latest_at}>Latest {formatInvestigationTime(item.latest_at, timezone, true)}</time> : null}
      </span>
      {item.episode_id ? <ArrowRight aria-hidden="true" size={15} /> : null}
    </>
  );
  return <li>{item.episode_id ? <button onClick={() => onSelect(item.episode_id!)} type="button">{content}</button> : <div>{content}</div>}</li>;
}

export function InvestigationOverview({ overview, onSelect }: { overview: OverviewType; onSelect: (id: string) => void }) {
  const problems = [...overview.recent_problems, ...overview.incomplete_runs.filter((item) => !overview.recent_problems.some((problem) => problem.episode_id === item.episode_id))];
  return (
    <section className="investigation-overview" aria-labelledby="investigation-overview-title">
      <div className="investigation-section-heading">
        <div>
          <span className="investigation-eyebrow"><Sparkles aria-hidden="true" size={14} /> Starting points</span>
          <h2 id="investigation-overview-title">What may need attention</h2>
        </div>
        <span>Last 24 hours · {overview.site_timezone}</span>
      </div>
      <div className="investigation-overview-grid">
        <section aria-labelledby="investigation-problems-title" className="investigation-overview-block problems">
          <h3 id="investigation-problems-title"><AlertTriangle aria-hidden="true" size={15} /> Problems and blocked actions</h3>
          {problems.length ? (
            <ol>{problems.slice(0, 6).map((episode) => <OverviewEpisode episode={episode} key={episode.episode_id} onSelect={onSelect} timezone={overview.site_timezone} />)}</ol>
          ) : (
            <div className="investigation-overview-empty"><CircleCheck aria-hidden="true" size={18} /> No recent problems or incomplete runs were recorded.</div>
          )}
        </section>
        <section aria-labelledby="investigation-repeated-title" className="investigation-overview-block repeated">
          <h3 id="investigation-repeated-title"><Repeat2 aria-hidden="true" size={15} /> Repeated problems</h3>
          {overview.repeated_problems.length ? <ol>{overview.repeated_problems.slice(0, 5).map((item) => <RepeatItem item={item} key={item.key} onSelect={onSelect} timezone={overview.site_timezone} />)}</ol> : <div className="investigation-overview-empty">No repeated failure pattern was found.</div>}
        </section>
      </div>
      {overview.important_activity.length ? (
        <section aria-labelledby="investigation-important-title" className="investigation-important-strip">
          <h3 id="investigation-important-title">Important recent activity</h3>
          <ol>{overview.important_activity.slice(0, 4).map((episode) => <OverviewEpisode episode={episode} key={episode.episode_id} onSelect={onSelect} timezone={overview.site_timezone} />)}</ol>
        </section>
      ) : null}
    </section>
  );
}
