import { useId, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, ChevronLeft, ChevronRight } from "lucide-react";
import { formatTime, pulseSlices } from "./model.js";

const kinds = ["entry", "exit", "denied"];
const kindLabels = { entry: "Entry", exit: "Exit", denied: "Denied" };
const period = (slice) => `${formatTime(slice.start)} – ${formatTime(slice.end)}`;

function countDescription(slice) {
  return kinds
    .filter((kind) => slice.counts[kind] > 0)
    .map((kind) => {
      const count = slice.counts[kind];
      const label = kind === "entry" ? count === 1 ? "entry" : "entries" : kind === "exit" ? count === 1 ? "exit" : "exits" : "denied";
      return `${count} ${label}`;
    })
    .join(" · ") || "No recorded events";
}

export function Pulse({ demo, hours, setHours, selected, select, expandedId, setExpandedId, animate }) {
  const [hovered, setHovered] = useState(null);
  const buttonRefs = useRef([]);
  const rangeId = useId();
  const slices = useMemo(() => pulseSlices(demo.events, demo.now, hours), [demo.events, demo.now, hours]);
  const all = slices.flatMap((slice) => slice.events);
  const counts = kinds.map((kind) => ({ kind, value: all.filter((event) => event.kind === kind).length }));
  const peak = Math.max(0, ...slices.map((slice) => slice.events.length));
  const busiest = peak ? slices.find((slice) => slice.events.length === peak) : null;
  const activeIndex = hovered ?? selected;
  const activeSlice = slices[activeIndex] ?? null;
  const windowStart = slices[0].start;
  const duration = demo.now - windowStart;
  const position = (timestamp) => (timestamp - windowStart) / duration * 100;
  const expandedEvent = activeSlice?.events.find((event) => event.id === expandedId);
  const anchor = activeSlice
    ? hovered !== null || !expandedEvent
      ? (activeSlice.start + activeSlice.end) / 2
      : Date.parse(expandedEvent.occurredAt)
    : null;
  const anchorPercent = anchor === null ? 0 : position(anchor);
  const edgeClass = anchorPercent < 15 ? " edge-left" : anchorPercent > 85 ? " edge-right" : "";
  const transition = { duration: animate ? 0.22 : 0, ease: [0.22, 1, 0.36, 1] };

  function selectSlice(index) {
    setHovered(null);
    if (selected === index) {
      select(null);
      return;
    }
    select(index);
    if (slices[index].events.length) setExpandedId(slices[index].events[0].id);
  }

  function handleSliceKey(event, index) {
    const key = event.key;
    if (key === "Escape") {
      event.preventDefault();
      select(null);
      setHovered(null);
      return;
    }
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(key)) return;
    event.preventDefault();
    const next = key === "Home" ? 0 : key === "End" ? 11 : (index + (key === "ArrowRight" ? 1 : 11)) % 12;
    buttonRefs.current[next]?.focus();
  }

  function stepInterval(offset) {
    const current = activeSlice ? activeIndex : 11;
    selectSlice((current + offset + 12) % 12);
  }

  return (
    <section className="pulse-panel panel" aria-labelledby={`${rangeId}-title`}>
      <header className="section-heading">
        <div>
          <h2 id={`${rangeId}-title`}>Access pulse</h2>
          <p>{all.length} recorded {all.length === 1 ? "event" : "events"} · Past {hours} hours</p>
        </div>
        <div className="range-control" role="group" aria-label="Activity time window">
          {[6, 12].map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={hours === value}
              onClick={() => {
                setHours(value);
                select(null);
                setHovered(null);
              }}
            >
              {hours === value && <motion.span className="range-indicator" layoutId={`${rangeId}-range`} transition={transition} />}
              <span>{value}h</span>
            </button>
          ))}
        </div>
      </header>

      <div
        id={`${rangeId}-timeline`}
        className="timeline"
        role="group"
        aria-label={`${hours}-hour activity timeline; select a time slice`}
        onPointerLeave={() => setHovered(null)}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setHovered(null);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            select(null);
            setHovered(null);
          }
        }}
      >
        <div className="timeline-line" aria-hidden="true" />
        {Array.from({ length: 49 }, (_, index) => (
          <span key={`tick-${index}`} className={`time-tick${index % 8 === 0 ? " major" : ""}`} style={{ left: `${index / 48 * 100}%` }} aria-hidden="true" />
        ))}
        {slices.map((slice, index) => (
          <button
            key={`slice-${index}`}
            ref={(element) => { buttonRefs.current[index] = element; }}
            type="button"
            className={`timeline-slice${selected === index ? " selected" : ""}`}
            style={{ left: `${index / 12 * 100}%`, width: `${100 / 12}%` }}
            aria-label={`${period(slice)}: ${slice.events.length} ${slice.events.length === 1 ? "event" : "events"}, ${slice.counts.denied} denied`}
            aria-pressed={selected === index}
            onClick={() => selectSlice(index)}
            onKeyDown={(event) => handleSliceKey(event, index)}
            onPointerEnter={(event) => { if (event.pointerType === "mouse") setHovered(index); }}
            onFocus={() => setHovered(index)}
          />
        ))}
        {slices.flatMap((slice) => slice.events.map((event) => (
          <button
            key={event.id}
            type="button"
            className={`event-marker ${event.kind}`}
            style={{ left: `${position(Date.parse(event.occurredAt))}%` }}
            aria-label={`${event.person}, ${kindLabels[event.kind]} at ${formatTime(event.occurredAt)}; view event`}
            aria-pressed={expandedId === event.id}
            onPointerEnter={(pointerEvent) => { if (pointerEvent.pointerType === "mouse") setHovered(slice.index); }}
            onFocus={() => setHovered(slice.index)}
            onClick={() => {
              setHovered(null);
              select(slice.index);
              setExpandedId(event.id);
            }}
          >
            <span className={`status-dot ${event.kind}`} aria-hidden="true" />
            <time>{formatTime(event.occurredAt)}</time>
          </button>
        )))}
        <AnimatePresence initial={false}>
          {activeSlice && (
            <motion.div
              key="cursor"
              className="timeline-cursor"
              initial={{ opacity: 0, left: `${anchorPercent}%` }}
              animate={{ opacity: 1, left: `${anchorPercent}%` }}
              exit={{ opacity: 0 }}
              transition={transition}
              aria-hidden="true"
            />
          )}
          {activeSlice && (
            <motion.div
              key="tooltip"
              className={`timeline-tooltip${edgeClass}`}
              initial={{ opacity: 0, left: `${anchorPercent}%` }}
              animate={{ opacity: 1, left: `${anchorPercent}%` }}
              exit={{ opacity: 0 }}
              transition={transition}
              aria-hidden="true"
            >
              <span>{period(activeSlice)} · {countDescription(activeSlice)}</span>
              {activeSlice.events.length > 0 && <strong>{[...new Set(activeSlice.events.map((event) => event.person))].join(", ")}</strong>}
            </motion.div>
          )}
        </AnimatePresence>
        <div className="time-labels" aria-hidden="true">
          {Array.from({ length: 7 }, (_, index) => (
            <span key={index} className={index > 0 && index < 6 ? "intermediate-label" : ""} style={{ left: `${index / 6 * 100}%` }}>
              {index === 6 ? <>Now<br /><time>{formatTime(demo.now)}</time></> : <time>{formatTime(windowStart + duration / 6 * index)}</time>}
            </span>
          ))}
        </div>
      </div>

      <div className="timeline-navigation" role="group" aria-label="Timeline interval navigation">
        <button type="button" className="icon-button" aria-label="Previous interval" aria-controls={`${rangeId}-timeline`} onClick={() => stepInterval(-1)}>
          <ChevronLeft size={18} aria-hidden="true" />
        </button>
        <span className="interval-label" aria-live="polite" aria-atomic="true">{activeSlice ? period(activeSlice) : "Select an interval"}</span>
        <button type="button" className="icon-button" aria-label="Next interval" aria-controls={`${rangeId}-timeline`} onClick={() => stepInterval(1)}>
          <ChevronRight size={18} aria-hidden="true" />
        </button>
      </div>

      <footer className="pulse-footer">
        <div className="activity-counts" aria-label="Time window event counts">
          {counts.map(({ kind, value }) => <span key={kind}><span className={`status-dot ${kind}`} aria-hidden="true" /><strong>{value}</strong> {kindLabels[kind]}</span>)}
        </div>
        <span className="busiest">{busiest ? `Busiest: ${period(busiest)}` : "Waiting for activity"}</span>
        <button type="button" className="text-link" onClick={() => document.getElementById("recent-events")?.scrollIntoView({ behavior: animate ? "smooth" : "auto", block: "start" })}>
          View events <ArrowRight size={16} aria-hidden="true" />
        </button>
      </footer>
      <p className="disclosure">Recent feed only · local times. Events do not confirm physical passage.</p>
    </section>
  );
}
