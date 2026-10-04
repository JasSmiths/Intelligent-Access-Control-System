import test from "node:test";
import assert from "node:assert/strict";
import { DEMO_NOW, PEOPLE, createDemo, formatDate, formatTime, pulseSlices, simulateArrival } from "./model.js";

function totals(slices) {
  return slices.reduce((counts, slice) => ({
    entry: counts.entry + slice.counts.entry,
    exit: counts.exit + slice.counts.exit,
    denied: counts.denied + slice.counts.denied,
  }), { entry: 0, exit: 0, denied: 0 });
}

function freezeDeep(value) {
  Object.freeze(value);
  for (const child of Object.values(value)) {
    if (child && typeof child === "object") freezeDeep(child);
  }
  return value;
}

test("fixture matches the mock's London date, times, people and local assets", () => {
  const state = createDemo();
  assert.equal(state.now, DEMO_NOW);
  assert.equal(formatTime(state.now), "22:15");
  assert.equal(formatDate(state.now), "02 Oct 2026");
  assert.deepEqual(state.events.map((event) => formatTime(event.occurredAt)), ["19:23", "17:41", "12:24", "07:19", "21:19"]);
  assert.equal(formatDate(state.events[4].occurredAt), "01 Oct 2026");
  assert.deepEqual(state.events.map((event) => event.id), ["steph-entry", "sylvia-entry", "sylvia-exit", "steph-exit", "jason-entry"]);
  assert.deepEqual(state.events.map((event) => event.snapshot), ["/assets/sample-snapshot.png", "/assets/sample-snapshot.png", null, null, null]);
  assert.equal(PEOPLE.steph.avatar, "/assets/steph.png");
  assert.equal(PEOPLE.sylvia.avatar, "/assets/sylvia.png");
  assert.equal(formatTime("invalid"), "—");
});

test("6h and 12h ranges have twelve slices and count only events in their window", () => {
  const state = freezeDeep(createDemo());
  const six = pulseSlices(state.events, state.now, 6);
  const twelve = pulseSlices(state.events, state.now, 12);
  assert.equal(six.length, 12);
  assert.equal(twelve.length, 12);
  assert.deepEqual(totals(six), { entry: 2, exit: 0, denied: 0 });
  assert.deepEqual(totals(twelve), { entry: 2, exit: 1, denied: 0 });
  assert.equal(six[0].end - six[0].start, 30 * 60_000);
  assert.equal(twelve[0].end - twelve[0].start, 60 * 60_000);
  assert.deepEqual(six.map((slice) => slice.index), Array.from({ length: 12 }, (_, index) => index));
});

test("timeline includes exact start and now, excludes older/future/invalid events and deduplicates IDs", () => {
  const start = DEMO_NOW - 6 * 60 * 60_000;
  const event = (id, timestamp, kind = "entry") => ({ id, occurredAt: new Date(timestamp).toISOString(), kind });
  const slices = pulseSlices([
    event("start", start),
    event("edge", start + 30 * 60_000, "exit"),
    event("now", DEMO_NOW, "denied"),
    event("now", DEMO_NOW),
    event("older", start - 1),
    event("future", DEMO_NOW + 1),
    { id: "invalid", occurredAt: "invalid", kind: "entry" },
  ], DEMO_NOW, 6);
  assert.deepEqual(slices[0].events.map((item) => item.id), ["start"]);
  assert.deepEqual(slices[1].events.map((item) => item.id), ["edge"]);
  assert.deepEqual(slices[11].events.map((item) => item.id), ["now"]);
  assert.deepEqual(totals(slices), { entry: 1, exit: 1, denied: 1 });
  assert.equal(slices.flatMap((slice) => slice.events).length, 3);
});

test("events in each slice sort newest first with stable ID ordering for equal times", () => {
  const events = freezeDeep([
    { id: "later-b", occurredAt: "2026-10-02T21:14:00Z", kind: "entry" },
    { id: "earlier", occurredAt: "2026-10-02T21:10:00Z", kind: "exit" },
    { id: "later-a", occurredAt: "2026-10-02T21:14:00Z", kind: "entry" },
  ]);
  assert.deepEqual(pulseSlices(events, DEMO_NOW, 6)[11].events.map((item) => item.id), ["later-a", "later-b", "earlier"]);
  assert.equal(events[0].id, "later-b");
});

test("arrival is immutable, deterministic and applied once until a fresh reset", () => {
  const initial = freezeDeep(createDemo());
  const before = structuredClone(initial);
  const arrived = simulateArrival(initial);
  assert.deepEqual(initial, before);
  assert.notEqual(arrived, initial);
  assert.notEqual(arrived.events, initial.events);
  assert.notEqual(arrived.inside, initial.inside);
  assert.notEqual(arrived.expected, initial.expected);
  assert.equal(formatTime(arrived.now), "22:16");
  assert.deepEqual(arrived.inside, ["jason", "steph", "sylvia", "jamie"]);
  assert.deepEqual(arrived.expected, ["alex", "morgan"]);
  assert.deepEqual(arrived.exited, initial.exited);
  assert.equal(arrived.events.length, 6);
  assert.deepEqual(arrived.events[0], {
    id: "jamie-entry", personId: "jamie", person: PEOPLE.jamie.name, plate: "JM26IAC",
    occurredAt: "2026-10-02T21:16:00.000Z", kind: "entry", snapshot: null,
  });
  assert.equal(arrived.hasArrival, true);
  assert.equal(simulateArrival(arrived), arrived);
  assert.deepEqual(totals(pulseSlices(arrived.events, arrived.now, 6)), { entry: 3, exit: 0, denied: 0 });
  const reset = createDemo();
  assert.deepEqual(reset, before);
  assert.notEqual(reset.events, initial.events);
  assert.notEqual(reset.events[0], initial.events[0]);
  assert.deepEqual(simulateArrival(reset), arrived);
});
