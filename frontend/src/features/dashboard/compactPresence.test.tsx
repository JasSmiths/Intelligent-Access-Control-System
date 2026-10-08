import React from "react";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { ExpectedPresenceOverview, Presence, PresenceSummary } from "../../api/types";
import { ExpectedPresenceTooltip, PresenceRosterTooltip } from "./OperationalWidgets";
import { insideNowRoster } from "./model";
import { useDashboardReads } from "./reads";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const names = ["Alpha", "Zed", "alpha", "Ána", "Éva", "😀 Person"];
const people: Presence[] = names.map((display_name, index) => ({ person_id: String(index), display_name, state: "present", last_changed_at: "2026-10-08T12:00:00Z" }));

it("keeps the bounded server roster order instead of sorting in the browser", () => {
  expect(insideNowRoster(people, new Map()).map((person) => person.display_name)).toEqual(names);
});

it("displays explicit omitted counts independently of the six received rows", () => {
  render(<PresenceRosterTooltip countLabel="19 people" emptyLabel="None" hidden={13} moreLabel={(hidden) => `+${hidden} more inside`} title="Inside Now" people={insideNowRoster(people, new Map())} />);
  expect(screen.getByText("19 people")).toBeTruthy();
  expect(screen.getByText("+13 more inside")).toBeTruthy();
  expect(screen.getAllByText(/^(Alpha|Zed|alpha|Ána|Éva|😀 Person)$/)).toHaveLength(6);
});

it("preserves expected global count and explicit hidden count", () => {
  const expected = people.map((person) => ({ person_id: person.person_id, display_name: person.display_name, confidence: .8, evidence_days: 12, observed_weekdays: 6, typical_arrival: "08:00", typical_departure: "17:00", profilePhotoDataUrl: null }));
  render(<ExpectedPresenceTooltip count={40} hidden={34} learning={false} people={expected} />);
  expect(screen.getByText("40 people")).toBeTruthy();
  expect(screen.getByText("+34 more expected")).toBeTruthy();
});

it("reads bounded presence and forecast summaries while retaining recent events", async () => {
  const presence: PresenceSummary = { counts: { total: 90, present: 19, exited: 60, unknown: 11 }, inside: { people, hidden: 13 } };
  const expected: ExpectedPresenceOverview = { date: "2026-10-08", timezone: "Europe/London", generated_at: "2026-10-08T12:00:00Z", count: 40, hidden: 34, learning: false, coverage: { regular_candidates: 40, learned_candidates: 40, learning_population: 40, ratio: 1 }, people: [] };
  const read = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/api/v1/presence/summary") return presence;
    if (path === "/api/v1/presence/expected-today/summary") return expected;
    if (path === "/api/v1/events?limit=40") return [];
    throw new Error(`Unexpected read ${path}`);
  });
  const { result } = renderHook(() => useDashboardReads());
  await act(async () => {});
  expect(result.current.presence).toEqual(people);
  expect(result.current.presenceCounts).toEqual(presence.counts);
  expect(result.current.insideHidden).toBe(13);
  expect(result.current.expectedPresence?.hidden).toBe(34);
  expect(read.mock.calls.map(([path]) => path)).toEqual(["/api/v1/presence/summary", "/api/v1/presence/expected-today/summary", "/api/v1/events?limit=40"]);
  expect(result.current.error).toBe("");
});
