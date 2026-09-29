import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useHistoryPage } from "./useHistoryPage";

const page = (items: number[], next_cursor: string | null, as_of = "2026-09-28T12:00:00Z") =>
  new Response(JSON.stringify({ items, next_cursor, as_of }), { status: 200 });

afterEach(() => vi.unstubAllGlobals());

it("keeps the current page after a failed Next, caches Previous, and distinguishes realtime from reset", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(page([1, 2], "older"))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 }))
    .mockResolvedValueOnce(page([3, 4], null))
    .mockResolvedValueOnce(page([5], null, "2026-09-28T12:01:00Z"));
  vi.stubGlobal("fetch", fetcher);
  const { result, rerender } = renderHook(
    ({ refresh, reset }) => useHistoryPage<number>("/api/v1/events/history", new URLSearchParams(), refresh, reset),
    { initialProps: { refresh: 0, reset: 0 } },
  );
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
  rerender({ refresh: 1, reset: 0 });
  await waitFor(() => expect(result.current.newActivity).toBe(true));
  expect(fetcher).toHaveBeenCalledTimes(3);
  rerender({ refresh: 1, reset: 1 });
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
