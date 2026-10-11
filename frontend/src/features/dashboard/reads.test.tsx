import React from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AccessEvent, RealtimeMessage } from "../../api/types";
import { createReadSignals, ReadSignalsContext } from "../../lib/readSignals";
import { useDashboardReads } from "./reads";

function event(revision: number, id = "event"): AccessEvent {
  return { id, resource_revision: revision, registration_number: "AB12CDE", direction: "entry",
    decision: "granted", confidence: .9, source: "synthetic", occurred_at: "2026-10-07T12:00:00Z",
    timing_classification: "normal", anomaly_count: revision, visitor_pass_id: null,
    visitor_name: null, visitor_pass_mode: null, external_admission_mode: null,
    external_admission_source: null, snapshot_url: null, snapshot_captured_at: null,
    snapshot_bytes: null, snapshot_width: null, snapshot_height: null, snapshot_camera: null,
    movement_saga: null };
}
function realtime(row: AccessEvent, type = "access_event.updated"): RealtimeMessage {
  return { type, payload: { ...row, event_id: row.id }, created_at: "2026-10-07T12:00:00Z" };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
function fetches(read: (signal: AbortSignal) => Promise<AccessEvent[]>) {
  return vi.fn(async (path: string, options: RequestInit) => {
    if (path.startsWith("/api/v1/events?")) return new Response(JSON.stringify(await read(options.signal as AbortSignal)));
    if (path === "/api/v1/presence/summary") return new Response(JSON.stringify({ counts: { total: 0, present: 0, exited: 0, unknown: 0 }, inside: { people: [], hidden: 0 } }));
    if (path === "/api/v1/presence/expected-today/summary") return new Response("null");
    throw new Error(`Unexpected URL: ${path}`);
  });
}
function expectDashboardRequests(request: ReturnType<typeof fetches>) {
  expect([...new Set(request.mock.calls.map(([path]) => path.split("?")[0]))].sort()).toEqual([
    "/api/v1/events", "/api/v1/presence/expected-today/summary", "/api/v1/presence/summary"
  ]);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("keeps newer realtime rows through a blocked actual GET, legacy delivery and one trailing refetch", async () => {
  vi.useFakeTimers();
  const first = deferred<AccessEvent[]>();
  const read = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue([event(1)]);
  const request = fetches(read); vi.stubGlobal("fetch", request);
  const signals = createReadSignals("account");
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>;
  const { result } = renderHook(() => useDashboardReads(), { wrapper });
  await act(async () => {});
  await act(async () => {
    signals.publish({ type: "realtime", message: realtime(event(2)) });
    signals.publish({ type: "realtime", message: realtime(event(1), "access_event.finalized") });
    signals.publish({ type: "realtime", message: realtime(event(0)) });
    signals.publish({ type: "realtime", message: realtime(event(1, "new-event"), "access_event.finalized") });
  });
  expect(result.current.events.find((row) => row.id === "event")?.resource_revision).toBe(2);
  await act(async () => { first.resolve([event(1)]); });
  expect(result.current.events).toHaveLength(2);
  expect(result.current.events.find((row) => row.id === "event")?.anomaly_count).toBe(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(read).toHaveBeenCalledTimes(2);
  expect(result.current.events.find((row) => row.id === "event")?.resource_revision).toBe(2);
  expect(result.current.error).toBe(""); expectDashboardRequests(request);
});

it("cancels the old account GET and drops its bounded revision knowledge on account change", async () => {
  const first = deferred<AccessEvent[]>();
  const read = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue([event(0)]);
  const request = fetches(read); vi.stubGlobal("fetch", request);
  let signals = createReadSignals("first-account");
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>;
  const { result, rerender } = renderHook(() => useDashboardReads(), { wrapper });
  await act(async () => {});
  const oldSignal = read.mock.calls[0][0] as AbortSignal;
  await act(async () => { signals.publish({ type: "realtime", message: realtime(event(5)) }); });
  expect(result.current.events[0].resource_revision).toBe(5);
  signals = createReadSignals("second-account");
  rerender();
  await act(async () => {});
  expect(oldSignal.aborted).toBe(true);
  expect(result.current.events[0].resource_revision).toBe(0);
  await act(async () => { first.resolve([event(10)]); });
  expect(result.current.events[0].resource_revision).toBe(0);
  expect(result.current.error).toBe(""); expectDashboardRequests(request);
});

it("retains at most40 rows and ignores unrelated realtime types", async () => {
  vi.useFakeTimers();
  const read = vi.fn().mockResolvedValue([]);
  const request = fetches(read); vi.stubGlobal("fetch", request);
  const signals = createReadSignals("account");
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>;
  const { result } = renderHook(() => useDashboardReads(), { wrapper });
  await act(async () => {});
  await act(async () => { signals.publish({ type: "realtime", message: { type: "unrelated.updated", payload: {}, created_at: "now" } }); });
  expect(read).toHaveBeenCalledTimes(1);
  await act(async () => {
    for (let index = 0; index < 50; index += 1) signals.publish({ type: "realtime", message: realtime(event(1, `event-${index}`)) });
  });
  expect(result.current.events).toHaveLength(40);
  expect(result.current.error).toBe(""); expectDashboardRequests(request);
});
