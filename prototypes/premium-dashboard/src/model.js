export const DEMO_NOW = Date.parse("2026-10-02T21:15:00Z");

export const PEOPLE = {
  jason: { id: "jason", name: "Jason Smith", avatar: null },
  steph: { id: "steph", name: "Steph", avatar: "/assets/steph.png" },
  sylvia: { id: "sylvia", name: "Sylvia", avatar: "/assets/sylvia.png" },
  jamie: { id: "jamie", name: "Jamie", avatar: null },
  alex: { id: "alex", name: "Alex", avatar: null },
  morgan: { id: "morgan", name: "Morgan", avatar: null },
  casey: { id: "casey", name: "Casey", avatar: null },
  reese: { id: "reese", name: "Reese", avatar: null },
};

const timeFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/London",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const dateFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/London",
  day: "2-digit",
  month: "short",
  year: "numeric",
});

function timestampValue(value) {
  return typeof value === "number" ? value : Date.parse(value);
}

export function formatTime(timestamp) {
  const value = timestampValue(timestamp);
  return Number.isFinite(value) ? timeFormatter.format(value) : "—";
}

export function formatDate(timestamp) {
  const value = timestampValue(timestamp);
  return Number.isFinite(value) ? dateFormatter.format(value) : "—";
}

function demoEvent(id, personId, plate, occurredAt, kind, snapshot = null) {
  return { id, personId, person: personId === "jason" ? "Jason" : PEOPLE[personId].name, plate, occurredAt, kind, snapshot };
}

export function createDemo() {
  return {
    now: DEMO_NOW,
    events: [
      demoEvent("steph-entry", "steph", "PE70DHX", "2026-10-02T18:23:00Z", "entry", "/assets/sample-snapshot.png"),
      demoEvent("sylvia-entry", "sylvia", "SVA673", "2026-10-02T16:41:00Z", "entry", "/assets/sample-snapshot.png"),
      demoEvent("sylvia-exit", "sylvia", "SVA673", "2026-10-02T11:24:00Z", "exit"),
      demoEvent("steph-exit", "steph", "PE70DHX", "2026-10-02T06:19:00Z", "exit"),
      demoEvent("jason-entry", "jason", "MD25VNO", "2026-10-01T20:19:00Z", "entry"),
    ],
    inside: ["jason", "steph", "sylvia"],
    expected: ["jamie", "alex", "morgan"],
    exited: ["casey", "reese"],
    hasArrival: false,
  };
}

export function simulateArrival(state) {
  if (state.hasArrival) return state;
  const now = DEMO_NOW + 60_000;
  return {
    ...state,
    now,
    events: [demoEvent("jamie-entry", "jamie", "JM26IAC", new Date(now).toISOString(), "entry"), ...state.events],
    inside: [...state.inside, "jamie"],
    expected: state.expected.filter((id) => id !== "jamie"),
    exited: [...state.exited],
    hasArrival: true,
  };
}

// A bounded view of fixture events, following the production timeline's inclusive boundaries.
export function pulseSlices(events, now, hours) {
  const end = timestampValue(now);
  const duration = hours * 60 * 60_000;
  const start = end - duration;
  const sliceDuration = duration / 12;
  const slices = Array.from({ length: 12 }, (_, index) => ({
    index,
    start: start + index * sliceDuration,
    end: start + (index + 1) * sliceDuration,
    events: [],
    counts: { entry: 0, exit: 0, denied: 0 },
  }));
  const seen = new Set();
  for (const event of events) {
    const timestamp = timestampValue(event.occurredAt);
    if (!Number.isFinite(timestamp) || timestamp < start || timestamp > end || seen.has(event.id)) continue;
    if (!["entry", "exit", "denied"].includes(event.kind)) continue;
    seen.add(event.id);
    const index = Math.min(11, Math.floor((timestamp - start) / sliceDuration));
    slices[index].events.push(event);
    slices[index].counts[event.kind] += 1;
  }
  for (const slice of slices) {
    slice.events.sort((left, right) => timestampValue(right.occurredAt) - timestampValue(left.occurredAt) || left.id.localeCompare(right.id));
  }
  return slices;
}
