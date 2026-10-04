import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, MotionConfig, motion, useReducedMotion } from "motion/react";
import {
  ArrowLeftRight, ArrowRight, Bell, Bot, CalendarDays, Camera, Car,
  ChartNoAxesColumnIncreasing, CheckCircle2, ChevronDown, ChevronRight,
  ChevronUp, Clock3, DoorClosed, FileSearch, Home, Info, LogOut, Menu,
  Moon, Play, Radio, RefreshCw, RotateCcw, Search, Settings, ShieldCheck,
  Ticket, Trophy, UserRound, UsersRound, Warehouse, X,
} from "lucide-react";
import "@fontsource-variable/inter";
import { createDemo, simulateArrival, pulseSlices, formatTime, formatDate, PEOPLE } from "./model.js";
import { Pulse } from "./Pulse.jsx";
import { Presence } from "./Presence.jsx";

const ease = [.2, .75, .3, 1];
const names = { entry: "Entry", exit: "Exit", denied: "Denied" };
const groups = [
  ["Operations", [["Dashboard", Home], ["Events", CalendarDays], ["Movements", ArrowLeftRight], ["Alerts", Bell]]],
  ["Access", [["People", UserRound], ["Groups", UsersRound], ["Vehicles", Car], ["Schedules", Clock3], ["Passes", Ticket]]],
  ["Insights", [["Reports", ChartNoAxesColumnIncreasing], ["Top Charts", Trophy], ["Investigations", FileSearch]]],
];

function Sidebar({ open, mobile, close, showPreview, openModal, triggerRef }) {
  const sidebarRef = useRef(null);
  const closeRef = useRef(null);
  useEffect(() => {
    if (!mobile || !open) return;
    closeRef.current?.focus({ preventScroll: true });
    return () => triggerRef.current?.focus({ preventScroll: true });
  }, [mobile, open, triggerRef]);
  function trapFocus(event) {
    if (!mobile || !open || event.key !== "Tab") return;
    const nodes = [...sidebarRef.current.querySelectorAll("button:not(:disabled)")].filter(node => node.getClientRects().length);
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
  return <>
    {mobile && open && <button className="nav-backdrop" onClick={close} aria-label="Close navigation" tabIndex={-1} />}
    <aside id="site-navigation" ref={sidebarRef} className={"sidebar " + (open ? "is-open" : "")} role={mobile && open ? "dialog" : undefined} aria-modal={mobile && open ? true : undefined} aria-label="Site navigation" inert={mobile && !open} onKeyDown={trapFocus}>
      <div className="brand"><ShieldCheck size={36} strokeWidth={1.5} /><div><strong>Intelligent</strong><span>Access Control</span></div><button ref={closeRef} className="mobile-close icon-button" onClick={close} aria-label="Close navigation"><X size={19} /></button></div>
      <nav aria-label="Main navigation">{groups.map(([title, items]) => <div className="nav-group" key={title}><p>{title}</p>{items.map(([label, Icon]) => <button key={label} className={"nav-item " + (label === "Dashboard" ? "active" : "")} aria-label={label} aria-current={label === "Dashboard" ? "page" : undefined} onClick={() => { close(); if (label !== "Dashboard") showPreview(label); }}><Icon size={20} strokeWidth={1.5} /><span>{label}</span></button>)}</div>)}<div className="settings-nav"><button className="nav-item" aria-label="Settings" onClick={() => { close(); showPreview("Settings"); }}><Settings size={20} strokeWidth={1.5} /><span>Settings</span></button></div></nav>
      <div className="sidebar-footer"><button className="profile" aria-label="Open Jason Smith's account" onClick={() => { close(); openModal("account"); }}><img src="/assets/jason-landscape.png" alt="Lake landscape" /><span><strong>Jason Smith</strong><small>Owner</small></span><ChevronDown size={14} /></button><div className="connection"><i className="status-dot entry" /><div><strong>Realtime live</strong><small>Sample stream · design preview</small></div></div></div>
    </aside>
  </>;
}

function EventFeed({ demo, search, expandedId, chooseEvent, matchingIds, animate }) {
  const events = demo.events.filter(event => (event.person + " " + event.plate + " " + event.kind).toLowerCase().includes(search.toLowerCase()));
  return <section className="events-panel panel" id="recent-events" aria-label="Recent events"><div className="feed-heading"><h2>Recent events</h2><span className="feed-caption">{search ? `${events.length} of ${demo.events.length}` : `Latest ${demo.events.length}`}</span></div><div className="event-list"><AnimatePresence initial={false}>{events.map(event => {
    const open = expandedId === event.id;
    const today = formatDate(event.occurredAt) === formatDate(demo.now);
    return <motion.div key={event.id} layout={animate ? "position" : false} className={"event-item " + (open ? "is-expanded " : "") + (matchingIds.has(event.id) ? "matches-slice" : "")} initial={{ opacity: 0, y: animate ? 6 : 0 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: animate ? .18 : 0, ease }}>
      <button className="event-row" aria-label={(open ? "Collapse" : "Show") + " snapshot and details for " + event.person + " " + names[event.kind] + " at " + formatTime(event.occurredAt)} aria-expanded={open} onClick={() => chooseEvent(event)}><i className={"status-dot " + event.kind} /><span className="event-time">{!today && <small>01 Oct </small>}{formatTime(event.occurredAt)}</span>{event.kind === "entry" ? <Car size={19} strokeWidth={1.5} /> : <LogOut size={19} strokeWidth={1.5} />}<strong className="event-person">{event.person}</strong><span className="event-plate">{event.plate}</span><span className={"event-kind " + event.kind}>{names[event.kind]}</span>{open ? <ChevronUp size={15} /> : <ChevronRight size={15} />}</button>
      <AnimatePresence initial={false}>{open && <motion.div key="evidence" className="evidence-reveal" initial={{ height: animate ? 0 : "auto", opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: animate ? 0 : "auto", opacity: 0 }} transition={{ height: { duration: animate ? .22 : 0, ease }, opacity: { duration: animate ? .18 : 0 } }}><div className="evidence-content">{event.snapshot ? <img src={event.snapshot} alt={"Sample LPR snapshot for " + event.person} /> : <div className="snapshot-unavailable"><Camera size={23} strokeWidth={1.5} /><span>Snapshot unavailable</span></div>}<div className="evidence-meta"><span>{event.snapshot ? "Sample snapshot" : "Event details"}</span><time dateTime={event.occurredAt}>{formatTime(event.occurredAt)}</time><span>{event.plate}</span><span className={"badge " + event.kind}>{names[event.kind]}</span></div></div></motion.div>}</AnimatePresence>
    </motion.div>;
  })}</AnimatePresence>{events.length === 0 && <div className="empty-state"><Search size={21} /><strong>No matching events</strong><span>Try a person, plate or event type.</span></div>}</div></section>;
}

function Devices({ openAlerts }) {
  return <section className="devices-panel panel" aria-label="Access points"><h2>Access points</h2><div className="device-list">{[["Top Gate", Car], ["Main Garage Door", Warehouse], ["Mums Garage Door", Warehouse], ["Back Door", DoorClosed]].map(([name, Icon]) => <div className="device-row" key={name}><Icon size={25} strokeWidth={1.3} /><span>{name}</span><small>Closed</small></div>)}</div><div className="health-heading"><h3>Site health</h3><button className="text-link" onClick={openAlerts}>View all</button></div><div className="health-metrics">{[[UserRound, 16, "People tracked"], [Car, 21, "Active vehicles"], [Radio, 2, "Live sources"]].map(([Icon, value, label]) => <div key={label}><Icon size={22} strokeWidth={1.4} /><strong>{value}</strong><span>{label}</span></div>)}</div><div className="health-footnote"><i className="status-dot entry" />No actionable alerts</div></section>;
}

function DashboardPreview() {
  const [demo, setDemo] = useState(createDemo);
  const [hours, setHours] = useState(6);
  const [selectedSlice, setSelectedSlice] = useState(6);
  const [expandedId, setExpandedId] = useState("steph-entry");
  const [roster, setRoster] = useState(null);
  const [search, setSearch] = useState("");
  const [sidebar, setSidebar] = useState(false);
  const [desktopCollapsed, setDesktopCollapsed] = useState(() => window.matchMedia("(min-width: 768px) and (max-width: 1199px)").matches);
  const [mobile, setMobile] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  const [controls, setControls] = useState(false);
  const [modal, setModal] = useState(null);
  const [motionEnabled, setMotionEnabled] = useState(true);
  const [toast, setToast] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const prefersReduced = useReducedMotion();
  const animate = motionEnabled && !prefersReduced;
  const toastTimer = useRef(null);
  const controlsRef = useRef(null);
  const controlsTriggerRef = useRef(null);
  const navigationTriggerRef = useRef(null);
  const slices = useMemo(() => pulseSlices(demo.events, demo.now, hours), [demo.events, demo.now, hours]);
  const matchingIds = new Set(selectedSlice === null ? [] : slices[selectedSlice].events.map(event => event.id));
  function showToast(message) { clearTimeout(toastTimer.current); setToast(message); toastTimer.current = setTimeout(() => setToast(""), 2400); }
  function closeControls(restoreFocus = false) {
    setControls(false);
    if (restoreFocus) controlsTriggerRef.current?.focus({ preventScroll: true });
  }
  function closeSidebar() {
    setSidebar(false);
    if (mobile) navigationTriggerRef.current?.focus({ preventScroll: true });
  }
  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const update = event => {
      setMobile(event.matches);
      if (!event.matches) setSidebar(false);
    };
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const query = window.matchMedia("(min-width: 768px) and (max-width: 1199px)");
    const update = event => { if (event.matches) setDesktopCollapsed(true); };
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => () => clearTimeout(toastTimer.current), []);
  useEffect(() => {
    const key = event => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (modal) setModal(null); else if (controls) closeControls(true); else if (sidebar) closeSidebar(); else if (roster) setRoster(null); else { setExpandedId(null); setSelectedSlice(null); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [modal, controls, sidebar, roster, mobile]);
  useEffect(() => {
    if (!controls) return;
    const outside = event => { if (!controlsRef.current?.contains(event.target)) setControls(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [controls]);
  function reset() { const restoreFocus = controlsRef.current?.contains(document.activeElement); setDemo(createDemo()); setHours(6); setSelectedSlice(6); setExpandedId("steph-entry"); setRoster(null); setSearch(""); setAnnouncement(""); closeControls(restoreFocus); showToast("Preview reset"); }
  function arrival() { if (demo.hasArrival) return; setDemo(previous => simulateArrival(previous)); setAnnouncement("Jamie arrived. Four people inside, two expected."); closeControls(true); }
  function chooseEvent(event) { setExpandedId(expandedId === event.id ? null : event.id); const index = slices.findIndex(slice => slice.events.some(candidate => candidate.id === event.id)); setSelectedSlice(index < 0 ? null : index); }
  return <MotionConfig reducedMotion={motionEnabled ? "user" : "always"} transition={{ duration: animate ? .22 : 0, ease }}>
    <div className={"app-shell" + (!animate ? " reduce-motion" : "") + (desktopCollapsed ? " nav-collapsed" : "")}>
      <Sidebar open={sidebar} mobile={mobile} close={closeSidebar} triggerRef={navigationTriggerRef} showPreview={label => showToast(label + " · Preview only")} openModal={setModal} />
      <div className="workspace"><header className="topbar"><button ref={navigationTriggerRef} className="icon-button navigation-toggle" aria-label={mobile ? sidebar ? "Close navigation" : "Open navigation" : desktopCollapsed ? "Expand navigation" : "Collapse navigation"} aria-controls="site-navigation" aria-expanded={mobile ? sidebar : !desktopCollapsed} onClick={() => mobile ? setSidebar(!sidebar) : setDesktopCollapsed(!desktopCollapsed)}><Menu size={20} strokeWidth={1.5} /></button><span className="site-name">Crest House</span><div className="preview-controls" ref={controlsRef}><button ref={controlsTriggerRef} className="preview-button" aria-expanded={controls} aria-controls="preview-controls-panel" onClick={() => controls ? closeControls(true) : setControls(true)}>Preview controls <ChevronDown size={12} /></button><AnimatePresence>{controls && <motion.div id="preview-controls-panel" className="controls-popover" initial={{ opacity: 0, y: animate ? -4 : 0 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: animate ? .18 : 0 }}><span className="eyebrow">Sample data · local preview</span><button className="control-action" disabled={demo.hasArrival} onClick={arrival}><Play size={17} /><span>{demo.hasArrival ? "Arrival simulated" : "Simulate arrival"}</span></button><button className="control-action" onClick={reset}><RotateCcw size={17} /><span>Reset preview</span></button><label className="motion-choice"><span>Animations{prefersReduced && <small>System prefers reduced motion</small>}</span><input type="checkbox" checked={motionEnabled} onChange={event => setMotionEnabled(event.target.checked)} aria-label="Enable animations" /></label></motion.div>}</AnimatePresence></div><label className="search-box"><Search size={16} strokeWidth={1.6} /><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search anything…" aria-label="Search recent events" />{search && <button onClick={() => setSearch("")} aria-label="Clear search"><X size={14} /></button>}</label><div className="topbar-actions"><button className="icon-button" aria-label="Open alerts" onClick={() => setModal("alerts")}><Bell size={20} strokeWidth={1.5} /></button><button className="icon-button" aria-label="Reset preview" onClick={reset}><RefreshCw size={20} strokeWidth={1.5} /></button><button className="icon-button" aria-label="Appearance" onClick={() => setModal("appearance")}><Moon size={20} strokeWidth={1.5} /></button><time className="header-time">{formatDate(demo.now)}<span> · </span>{formatTime(demo.now)}</time></div></header>
        <main><div className="page-heading"><div><h1>Good evening, Jason</h1><p>Here’s what’s happening at Crest House today.</p></div><div className="normal-status"><i className="status-dot entry" /><span>All systems normal</span></div></div>
          <Presence demo={demo} active={roster} setActive={setRoster} animate={animate} />
          <Pulse demo={demo} hours={hours} setHours={setHours} selected={selectedSlice} select={setSelectedSlice} expandedId={expandedId} setExpandedId={setExpandedId} animate={animate} />
          <div className="lower-grid"><EventFeed demo={demo} search={search} expandedId={expandedId} chooseEvent={chooseEvent} matchingIds={matchingIds} animate={animate} /><Devices openAlerts={() => setModal("alerts")} /></div>
        </main>
      </div>
      <button className="alfred-launcher" aria-label="Open Alfred" onClick={() => setModal("alfred")}><Bot size={27} strokeWidth={1.4} /></button>
      <AnimatePresence>{toast && <motion.div className="toast" role="status" initial={{ opacity: 0, y: animate ? 6 : 0 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: animate ? .18 : 0 }}><Info size={16} />{toast}</motion.div>}</AnimatePresence>
      <div className="sr-only" aria-live="polite" aria-atomic="true">{announcement}</div>
      <AnimatePresence>{modal && <PreviewModal type={modal} demo={demo} close={() => setModal(null)} animate={animate} />}</AnimatePresence>
    </div>
  </MotionConfig>;
}

function PreviewModal({ type, demo, close, animate }) {
  const closeRef = useRef(null);
  const returnFocus = useRef(document.activeElement);
  const containerRef = useRef(null);
  const [answer, setAnswer] = useState(false);
  useEffect(() => { closeRef.current?.focus(); return () => returnFocus.current?.focus(); }, []);
  const titles = { alerts: "Alerts", appearance: "Appearance", account: "Your account", alfred: "Alfred" };
  return <motion.div className="modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: animate ? .18 : 0 }} onClick={close}><motion.section ref={containerRef} className="preview-modal" role="dialog" aria-modal="true" aria-label={titles[type]} initial={{ opacity: 0, y: animate ? 8 : 0 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: animate ? .22 : 0, ease }} onClick={event => event.stopPropagation()} onKeyDown={event => { if (event.key !== "Tab") return; const nodes = containerRef.current.querySelectorAll("button:not(:disabled), input"); const first = nodes[0]; const last = nodes[nodes.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }}><div className="modal-heading"><h2>{titles[type]}</h2><button className="icon-button" ref={closeRef} onClick={close} aria-label="Close dialog"><X size={20} /></button></div>
      {type === "alerts" && <div className="modal-content"><CheckCircle2 size={29} className="entry-text" strokeWidth={1.4} /><h3>No actionable alerts</h3><p>All systems normal at Crest House.</p><small>Sample data</small></div>}
      {type === "appearance" && <div className="modal-content"><Moon size={29} strokeWidth={1.4} /><h3>Dark appearance</h3><p>The dark theme is active in this preview.</p></div>}
      {type === "account" && <div className="modal-content"><img className="account-avatar" src="/assets/jason-landscape.png" alt="Lake landscape" /><h3>Jason Smith</h3><p>Owner · Crest House</p><small>Preview account</small></div>}
      {type === "alfred" && <div className="alfred-content"><p>Good evening, Jason. How can I help?</p><button className="query-chip" onClick={() => setAnswer(true)}>Who is inside now? <ArrowRight size={14} /></button>{answer && <motion.div className="alfred-answer" initial={{ opacity: 0 }} animate={{ opacity: 1 }}><strong>{demo.inside.length} people are inside.</strong><p>{demo.inside.map(id => PEOPLE[id].name).join(", ")}.</p></motion.div>}<small>Sample data · Alfred preview</small></div>}
    </motion.section></motion.div>;
}

export function App() { return <DashboardPreview />; }
