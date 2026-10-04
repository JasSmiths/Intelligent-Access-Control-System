import { useCallback, useEffect, useId, useRef, useState } from "react";
import { AnimatePresence, motion, useAnimationControls } from "motion/react";
import { BookOpen, Info, X } from "lucide-react";
import { PEOPLE } from "./model.js";

const GROUPS = {
  inside: { label: "Inside now", state: "Inside the property" },
  expected: { label: "Expected today", state: "Expected today" },
  exited: { label: "Exited today", state: "Left the property" },
};
const EASE = [0.22, 1, 0.36, 1];

export function Avatar({ id, className = "" }) {
  const person = PEOPLE[id] ?? { name: "Sample person", avatar: null };
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => setUnavailable(false), [id, person.avatar]);
  const hasImage = person.avatar && !unavailable;
  const initials = id === "jason" ? "JS" : person.name.charAt(0).toUpperCase();

  return (
    <span className={`avatar ${hasImage ? "" : "initials"} ${className}`.trim()} title={person.name}>
      {hasImage ? (
        <img src={person.avatar} alt={person.name} onError={() => setUnavailable(true)} />
      ) : (
        <span aria-label={person.name}>{initials}</span>
      )}
    </span>
  );
}

function AnimatedNumber({ value, animate }) {
  const previous = useRef(value);
  const controls = useAnimationControls();
  useEffect(() => {
    if (previous.current !== value && animate) {
      controls.start({ filter: ["brightness(1)", "brightness(1.6)", "brightness(1)"], transition: { duration: 0.55 } });
    } else if (!animate) {
      controls.set({ filter: "brightness(1)" });
    }
    previous.current = value;
  }, [value, animate, controls]);
  return <motion.span className="metric-number" animate={controls}>{value}</motion.span>;
}

export function Presence({ demo, active, setActive, animate }) {
  const panelRef = useRef(null);
  const metricRefs = useRef({});
  const closeRef = useRef(null);
  const setCloseRef = useCallback(node => {
    closeRef.current = node;
    if (node) node.focus({ preventScroll: true });
  }, []);
  const rosterId = useId();
  const group = GROUPS[active] ? active : null;
  const insideCount = demo.inside.length;
  const mixExited = 12 - insideCount;
  const transition = { duration: animate ? 0.22 : 0, ease: EASE };

  function closeRoster() {
    setActive(null);
    metricRefs.current[group]?.focus({ preventScroll: true });
  }

  useEffect(() => {
    if (!group) return undefined;
    function onPointerDown(event) {
      if (!panelRef.current?.contains(event.target)) {
        setActive(null);
        metricRefs.current[group]?.focus({ preventScroll: true });
      }
    }
    function onKeyDown(event) {
      // Only handle this disclosure's keyboard interaction; a separate dialog
      // can own Escape when focus is outside this presence panel.
      if (event.key === "Escape" && !event.defaultPrevented && panelRef.current?.contains(event.target)) {
        event.preventDefault();
        setActive(null);
        metricRefs.current[group]?.focus({ preventScroll: true });
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [group, setActive]);

  return (
    <section className="presence-panel panel" aria-label="Presence overview" ref={panelRef}>
      <div className="presence-metrics">
        {Object.entries(GROUPS).map(([key, details]) => (
          <div className={`metric-wrap ${key}`} key={key}>
            <button
              type="button"
              className="metric"
              ref={(node) => { metricRefs.current[key] = node; }}
              aria-expanded={group === key}
              aria-controls={group === key ? rosterId : undefined}
              aria-label={`${demo[key].length} ${details.label.toLowerCase()}. View sample roster`}
              onClick={() => setActive(group === key ? null : key)}
            >
              {group === key && (
                <motion.span className="metric-selection" layoutId="presence-selection" transition={transition} aria-hidden="true" />
              )}
              <AnimatedNumber value={demo[key].length} animate={animate} />
              <span className="metric-copy">
                <span>{details.label}</span>
                {key === "expected" && <small><BookOpen size={19} /> Learning <span aria-hidden="true">·</span> <Info size={15} aria-label="Sample learning mode" /></small>}
              </span>
              {key === "inside" && (
                <span className="avatar-stack" aria-hidden="true">
                  {demo.inside.slice(0, 3).map((id) => <Avatar key={id} id={id} />)}
                  {insideCount > 3 && <span className="avatar more">+{insideCount - 3}</span>}
                </span>
              )}
            </button>
          </div>
        ))}
      </div>
      <div className="presence-bar" role="img" aria-label={`Presence mix: ${insideCount} present, ${mixExited} exited, zero unknown`}>
        <motion.span className="present-bar" animate={{ width: `${insideCount / 12 * 100}%` }} transition={transition} />
        <motion.span className="exited-bar" animate={{ width: `${mixExited / 12 * 100}%` }} transition={transition} />
      </div>
      <div className="presence-legend">
        <span><i className="status-dot entry" aria-hidden="true" /> Present <strong>{insideCount}</strong></span>
        <span><i className="status-dot exit" aria-hidden="true" /> Exited <strong>{mixExited}</strong></span>
        <span><i className="status-dot unknown" aria-hidden="true" /> Unknown <strong>0</strong></span>
      </div>
      <AnimatePresence initial={false} mode="wait">
        {group && (
          <motion.div
            className="roster-popover"
            id={rosterId}
            key={group}
            role="dialog"
            aria-modal="false"
            aria-labelledby={`${rosterId}-title`}
            initial={{ opacity: 0, y: animate ? -5 : 0 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: animate ? -3 : 0 }}
            transition={{ duration: animate ? 0.18 : 0, ease: EASE }}
          >
            <div className="popover-heading">
              <h3 id={`${rosterId}-title`}>{GROUPS[group].label} <span>{demo[group].length}</span></h3>
              <button className="icon-button" type="button" onClick={closeRoster} ref={setCloseRef} aria-label="Close presence roster"><X size={18} /></button>
            </div>
            {demo[group].slice(0, 6).map((id) => (
              <div className="roster-person" key={id}>
                <Avatar id={id} />
                <div><strong>{PEOPLE[id]?.name ?? "Sample person"}</strong><span>{GROUPS[group].state}</span></div>
                <i className={`status-dot ${group === "inside" ? "entry" : group === "exited" ? "exit" : "unknown"}`} aria-hidden="true" />
              </div>
            ))}
            <p>Sample presence · Preview only</p>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
