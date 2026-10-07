import React from "react";
import { createReadSignals, ReadSignalsContext } from "../lib/readSignals";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useHistoryPage } from "./useHistoryPage";

const page = (items: number[], next_cursor: string | null, as_of = "2026-09-28T12:00:00Z") =>
  new Response(JSON.stringify({ items, next_cursor, as_of }), { status: 200 });

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("keeps the current page after a failed Next, caches Previous, and distinguishes realtime from reset", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(page([1, 2], "older"))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }))
    .mockResolvedValueOnce(page([3, 4], null))
    .mockResolvedValueOnce(page([5], null, "2026-09-28T12:01:00Z"));
  vi.stubGlobal("fetch", fetcher);
  const { signals, wrapper, advance } = signalHistory();
  const { result } = renderHook(() => useHistoryPage<number>("/api/v1/events/history", new URLSearchParams()), { wrapper });
  await waitFor(() => expect(result.current.items).toEqual([1, 2]));
  act(() => result.current.next());
  await waitFor(() => expect(result.current.error).toContain("unavailable"));
  expect(result.current.index).toBe(0);
  expect(result.current.items).toEqual([1, 2]);
  act(() => result.current.next());
  await waitFor(() => expect(result.current.items).toEqual([3, 4]));
  expect(result.current.index).toBe(1);
  act(() => result.current.previous());
  expect(result.current.items).toEqual([1, 2]);
  expect(fetcher).toHaveBeenCalledTimes(3);
  act(() => result.current.next());
  expect(result.current.items).toEqual([3, 4]);
  advance();
  await act(async () => { await signals.publish({ type: "realtime", message: { type: "access_event.finalized", payload: {} } }); });
  await waitFor(() => expect(result.current.newActivity).toBe(true));
  expect(fetcher).toHaveBeenCalledTimes(3);
  await act(async () => { await signals.publish({ type: "refresh", reason: "manual", resetHistory: true }); });
  await waitFor(() => expect(result.current.items).toEqual([5]));
  expect(result.current.index).toBe(0);
  expect(result.current.newActivity).toBe(false);
});

it("clears stale records when a changed filter cannot be read", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(page([1], null))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "search unavailable" }), { status: 503 }));
  vi.stubGlobal("fetch", fetcher);
  const { result, rerender } = renderHook(
    ({ q }) => useHistoryPage<number>("/api/v1/events/history", new URLSearchParams(q ? { q } : {})),
    { initialProps: { q: "" } },
  );
  await waitFor(() => expect(result.current.items).toEqual([1]));
  rerender({ q: "new plate" });
  await waitFor(() => expect(result.current.error).toContain("search unavailable"));
  expect(result.current.items).toEqual([]);
  expect(fetcher.mock.calls[1][0]).toContain("q=new+plate");
});

it("surfaces a changing alert history as a refreshable conflict", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(page([1], "older"))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Alert history changed during this read. Refresh the page." }), { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  const { result } = renderHook(() => useHistoryPage<number>("/api/v1/alerts/history", new URLSearchParams()));
  await waitFor(() => expect(result.current.items).toEqual([1]));
  act(() => result.current.next());
  await waitFor(() => expect(result.current.error).toContain("Refresh the page"));
  expect(result.current.index).toBe(0);
  expect(result.current.items).toEqual([1]);
});

function signalHistory() {
  const signals = createReadSignals("test");
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>;
  let now = 0; vi.spyOn(Date, "now").mockImplementation(() => now);
  return { signals, wrapper, advance: () => { now += 6000; } };
}
it.each(["previous", "failed-next"] as const)("refreshes the displayed first page after %s without a stale pending navigation target", async (navigation) => {
  const { signals, wrapper, advance } = signalHistory();
  const fetcher = vi.fn().mockResolvedValueOnce(page([1], "older"))
    .mockResolvedValueOnce(navigation === "previous" ? page([2], null) : new Response(JSON.stringify({ detail: "Offline" }), { status: 503 }))
    .mockResolvedValueOnce(page([3], null, "2026-09-28T12:02:00Z"));
  vi.stubGlobal("fetch", fetcher);
  const { result } = renderHook(() => useHistoryPage<number>("/api/v1/events/history", new URLSearchParams()), { wrapper });
  await waitFor(() => expect(result.current.items).toEqual([1]));
  act(() => result.current.next());
  if (navigation === "previous") {
    await waitFor(() => expect(result.current.items).toEqual([2]));
    act(() => result.current.previous()); expect(result.current.items).toEqual([1]);
  } else await waitFor(() => expect(result.current.error).toContain("Offline"));
  advance();
  await act(async () => { await signals.publish({ type: "realtime", message: { type: "access_event.finalized", payload: {} } }); });
  expect(result.current.items).toEqual([3]); expect(result.current.index).toBe(0); expect(result.current.newActivity).toBe(false);
  expect(fetcher).toHaveBeenCalledTimes(3); expect(fetcher.mock.calls[2][0]).not.toContain("cursor=");
});
it("retains a filtered historical snapshot on live activity and resets only on explicit manual refresh", async () => {
  const { signals, wrapper, advance } = signalHistory();
  const fetcher = vi.fn().mockResolvedValueOnce(page([1], "older")).mockResolvedValueOnce(page([2], null)).mockResolvedValueOnce(page([3], null, "2026-09-28T12:03:00Z"));
  vi.stubGlobal("fetch", fetcher);
  const { result } = renderHook(() => useHistoryPage<number>("/api/v1/events/history", new URLSearchParams({ q: "KEEP01" })), { wrapper });
  await waitFor(() => expect(result.current.items).toEqual([1])); act(() => result.current.next());
  await waitFor(() => expect(result.current.items).toEqual([2])); const asOf = result.current.asOf; advance();
  await act(async () => { await signals.publish({ type: "realtime", message: { type: "vehicle_information.updated", payload: {} } }); });
  expect(result.current.items).toEqual([2]); expect(result.current.asOf).toBe(asOf); expect(result.current.newActivity).toBe(true); expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => { await signals.publish({ type: "refresh", reason: "manual", resetHistory: true }); });
  expect(result.current.items).toEqual([3]); expect(result.current.index).toBe(0); expect(fetcher.mock.calls[2][0]).toContain("q=KEEP01");
});
