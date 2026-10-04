import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AccessEvent } from "../../api/types";
import { AccessPulse, pulseSlices } from "./AccessPulse";

afterEach(cleanup);
const now = new Date("2026-09-28T12:00:00Z");
const event = (id: string, minutesAgo: number, overrides: Partial<AccessEvent> = {}) => ({
  id, occurred_at: new Date(now.getTime() - minutesAgo * 60_000).toISOString(),
  registration_number: id, direction: "entry", decision: "granted", ...overrides
} as AccessEvent);

it("uses bounded, deduplicated recent events and treats denials separately from entry direction", () => {
  const slices = pulseSlices([
    event("start", 60), event("boundary", 55), event("denied", 1, { decision: "denied" }),
    event("now", 0, { direction: "exit" }), event("now", 0),
    event("old", 61), event("future", -1), event("bad-date", 1, { occurred_at: "invalid" }),
    event("unknown", 2, { direction: "unrecognised" as AccessEvent["direction"] })
  ], now.getTime(), 60);
  expect(slices.flatMap((slice) => slice.events)).toHaveLength(5);
  expect(slices[0].events.map((item) => item.id)).toEqual(["start"]);
  expect(slices[1].events.map((item) => item.id)).toEqual(["boundary"]);
  expect(slices[11].counts).toEqual({ entry: 0, exit: 1, denied: 1, unknown: 1 });
});

it("supports keyboard inspection, reset, changing time windows, and opening events", () => {
  const onOpenEvents = vi.fn();
  render(<AccessPulse now={now} events={[event("RECENT", 2), event("EARLIER", 120), event("OLDER", 600), event("OUTSIDE", 721)]} onOpenEvents={onOpenEvents} />);
  expect(screen.getByRole("button", { name: "6h" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByRole("button", { name: "1h" })).toBeNull();
  expect(screen.getByLabelText("Time window event counts").textContent).toContain("2Entry");
  expect(screen.getByText("30-min slices")).toBeTruthy();
  const timeline = screen.getByRole("group", { name: /activity timeline/ });
  const slices = within(timeline).getAllByRole("button");
  slices[0].focus();
  fireEvent.keyDown(slices[0], { key: "End" });
  expect(document.activeElement).toBe(slices[11]);
  fireEvent.keyDown(slices[11], { key: "Enter" });
  expect(slices[11].getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByLabelText("Selected slice event counts").textContent).toContain("1Entry");
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  expect(slices[11].getAttribute("aria-pressed")).toBe("false");
  fireEvent.keyDown(slices[11], { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "12h" }));
  expect(slices[11].getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByRole("button", { name: "12h" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("group", { name: "12-hour activity timeline; select a time slice" })).toBeTruthy();
  expect(screen.getByLabelText("Time window event counts").textContent).toContain("3Entry");
  expect(screen.getByText("60-min slices")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "6h" }));
  expect(screen.getByLabelText("Time window event counts").textContent).toContain("2Entry");
  fireEvent.click(screen.getByRole("button", { name: "Events" }));
  expect(onOpenEvents).toHaveBeenCalledOnce();
});

it("describes an empty feed without claiming a healthy site or complete history", () => {
  render(<AccessPulse now={now} events={[]} onOpenEvents={() => {}} />);
  expect(screen.getByText("Waiting for activity")).toBeTruthy();
  expect(screen.getByText("No events in this time window of the loaded feed.")).toBeTruthy();
  expect(screen.getByText(/Events do not confirm physical passage/)).toBeTruthy();
});
