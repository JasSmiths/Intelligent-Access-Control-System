import React from "react";
import { ArrowDownLeft, ArrowUpRight, ChevronRight, ShieldAlert } from "lucide-react";
import type { AccessEvent } from "../../api/types";
import "./access-pulse.css";

type Kind = "entry" | "exit" | "denied" | "unknown";
type Slice = { start: number; end: number; events: AccessEvent[]; counts: Record<Kind, number> };
const kinds: Kind[] = ["entry", "exit", "denied", "unknown"];
const labels: Record<Kind, string> = { entry: "Entry", exit: "Exit", denied: "Denied", unknown: "Other" };
const time = (value: number) => new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(value);
const period = (slice: Slice) => `${time(slice.start)}–${time(slice.end)}`;

export function pulseKind(event: AccessEvent): Kind {
  if (event.decision === "denied" || event.direction === "denied") return "denied";
  if (event.direction === "entry" || event.direction === "exit") return event.direction;
  return "unknown";
}

// This is a view of the bounded recent feed, not a historical total or proof of passage.
export function pulseSlices(events: AccessEvent[], now: number, minutes: number): Slice[] {
  const duration = minutes * 60_000;
  const start = now - duration;
  const slices: Slice[] = Array.from({ length: 12 }, (_, index) => ({
    start: start + index * duration / 12, end: start + (index + 1) * duration / 12,
    events: [], counts: { entry: 0, exit: 0, denied: 0, unknown: 0 }
  }));
  const seen = new Set<string>();
  for (const event of events) {
    const timestamp = Date.parse(event.occurred_at);
    if (!Number.isFinite(timestamp) || timestamp < start || timestamp > now || seen.has(event.id)) continue;
    seen.add(event.id);
    const slice = slices[Math.min(11, Math.floor((timestamp - start) / (duration / 12)))];
    slice.events.push(event);
    slice.counts[pulseKind(event)] += 1;
  }
  for (const slice of slices) slice.events.sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at) || a.id.localeCompare(b.id));
  return slices;
}

function point(radius: number, degrees: number) {
  const angle = degrees * Math.PI / 180;
  return [140 + radius * Math.cos(angle), 127 + radius * Math.sin(angle)];
}

function arc(inner: number, outer: number, start: number, end: number) {
  const a = point(outer, start), b = point(outer, end), c = point(inner, end), d = point(inner, start);
  return `M${a} A${outer},${outer} 0 0 1 ${b} L${c} A${inner},${inner} 0 0 0 ${d} Z`;
}

export function AccessPulse({ events, now, onOpenEvents }: { events: AccessEvent[]; now: Date; onOpenEvents: () => void }) {
  const [minutes, setMinutes] = React.useState(60);
  const [selected, setSelected] = React.useState<number | null>(null);
  const id = React.useId();
  const slices = pulseSlices(events, now.getTime(), minutes);
  const all = slices.flatMap((slice) => slice.events);
  const peak = Math.max(1, ...slices.map((slice) => slice.events.length));
  const chosen = selected === null ? null : slices[selected];
  const displayed = chosen ? chosen.events : all;
  const latest = [...displayed].sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at))[0];
  const counts = kinds.map((kind) => ({ kind, count: displayed.filter((event) => pulseKind(event) === kind).length }));
  const busiest = all.length ? slices.find((slice) => slice.events.length === peak) : null;

  return <section className="access-pulse" aria-labelledby={`${id}-title`}>
    <header className="access-pulse-header">
      <div><h2 id={`${id}-title`}>Access pulse</h2><p>Patterns in the recent feed</p></div>
      <div className="access-pulse-range" role="group" aria-label="Activity time window">
        {[60, 360].map((value) => <button key={value} type="button" aria-pressed={minutes === value} onClick={() => { setMinutes(value); setSelected(null); }}>{value / 60}h</button>)}
      </div>
    </header>
    <div className="access-pulse-visual">
      <svg viewBox="0 0 280 240" role="group" aria-label={`${minutes / 60}-hour activity timeline; select a time slice`}>
        <circle className="access-pulse-orbit" cx="140" cy="127" r="62" />
        <circle className="access-pulse-orbit access-pulse-orbit-outer" cx="140" cy="127" r="114" />
        {Array.from({ length: 49 }, (_, index) => {
          const angle = -225 + index * 270 / 48;
          return <path key={index} className="access-pulse-tick" d={`M${point(118, angle)} L${point(index % 4 ? 120 : 123, angle)}`} />;
        })}
        {slices.map((slice, index) => {
          const start = -225 + index * 22.5 + 1;
          const end = start + 20.5;
          let radius = 72;
          return <g key={index}>
            <path className="access-pulse-track" d={arc(72, 109, start, end)} />
            {kinds.map((kind) => {
              const inner = radius;
              radius += slice.counts[kind] / peak * 37;
              return slice.counts[kind] ? <path key={kind} className={`access-pulse-band ${kind}`} d={arc(inner, radius, start, end)} /> : null;
            })}
            <path className="access-pulse-hit" d={arc(67, 113, start, end)} role="button" tabIndex={0}
              aria-pressed={selected === index} aria-label={`${period(slice)}: ${slice.events.length} events, ${slice.counts.denied} denied`}
              onClick={() => setSelected(selected === index ? null : index)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(selected === index ? null : index); }
                if (event.key === "Escape") setSelected(null);
                if (["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) {
                  event.preventDefault();
                  const next = event.key === "Home" ? 0 : event.key === "End" ? 11 : (index + (event.key === "ArrowRight" ? 1 : 11)) % 12;
                  event.currentTarget.closest("svg")?.querySelectorAll<SVGElement>("[role=button]")[next]?.focus();
                }
              }}><title>{period(slice)} · {slice.events.length} events</title></path>
          </g>;
        })}
        <g className="access-pulse-centre" aria-hidden="true">
          <text x="140" y="121" className="access-pulse-number">{displayed.length}</text>
          <text x="140" y="145">{displayed.length === 1 ? "recorded event" : "recorded events"}</text>
          <text x="140" y="162" className="access-pulse-window">{chosen ? "SELECTED SLICE" : `PAST ${minutes / 60} ${minutes === 60 ? "HOUR" : "HOURS"}`}</text>
        </g>
        <text x="39" y="225" className="access-pulse-axis">{minutes / 60}h ago</text>
        <text x="241" y="225" className="access-pulse-axis">Now</text>
      </svg>
    </div>
    <div className="access-pulse-counts" aria-label={chosen ? "Selected slice event counts" : "Time window event counts"}>
      {counts.filter(({ kind, count }) => kind !== "unknown" || count > 0).map(({ kind, count }) => {
        const Icon = kind === "entry" ? ArrowDownLeft : kind === "exit" ? ArrowUpRight : ShieldAlert;
        return <div key={kind} className={kind}><Icon size={14} aria-hidden="true" /><strong>{count}</strong><span>{labels[kind]}</span></div>;
      })}
    </div>
    <div className="access-pulse-detail" aria-live="polite" aria-atomic="true">
      <div className="access-pulse-detail-heading"><strong>{chosen ? period(chosen) : busiest ? `Busiest · ${period(busiest)}` : "Waiting for activity"}</strong>
        {chosen ? <button type="button" onClick={() => setSelected(null)}>Reset</button> : <span>{minutes / 12}-min slices</span>}
      </div>
      <p>{latest ? <>Latest: <b>{latest.registration_number}</b> · {labels[pulseKind(latest)]} · {time(Date.parse(latest.occurred_at))}</> : chosen ? "No events in this slice of the loaded feed." : "No events in this time window of the loaded feed."}</p>
    </div>
    <footer className="access-pulse-footer"><p>Recent feed only · local times<br />Events do not confirm physical passage.</p><button type="button" onClick={onOpenEvents}>Events <ChevronRight size={14} aria-hidden="true" /></button></footer>
  </section>;
}
