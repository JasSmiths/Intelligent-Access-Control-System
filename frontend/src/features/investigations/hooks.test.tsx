import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as investigationApi from "../../api/investigations";
import { defaultOverview, filterCatalog, scheduleBlockedEpisode, SITE_TIMEZONE } from "./fixtures";
import { useInvestigationData } from "./hooks";
import { DEFAULT_INVESTIGATION_QUERY } from "./query";
import type { ActivityPage } from "./types";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function page(id: string, cursor: string | null = null): ActivityPage {
  return { items: [{ ...scheduleBlockedEpisode, episode_id: id }], next_cursor: cursor,
    site_timezone: SITE_TIMEZONE, resolved_range: {} };
}

beforeEach(() => {
  vi.spyOn(investigationApi, "getInvestigationFilters").mockResolvedValue(filterCatalog);
  vi.spyOn(investigationApi, "getInvestigationOverview").mockResolvedValue(defaultOverview);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each(["resolve", "reject"] as const)("resets canceled pagination for a new query and ignores the old %s", async (outcome) => {
  const oldMore = deferred<ActivityPage>();
  const newMore = deferred<ActivityPage>();
  const read = vi.spyOn(investigationApi, "getActivityPage")
    .mockResolvedValueOnce(page("old-first", "old-cursor"))
    .mockReturnValueOnce(oldMore.promise)
    .mockResolvedValueOnce(page("new-first", "new-cursor"))
    .mockReturnValueOnce(newMore.promise);
  const { result, rerender } = renderHook(({ query }) => useInvestigationData(query, 0),
    { initialProps: { query: DEFAULT_INVESTIGATION_QUERY } });
  await waitFor(() => expect(result.current.items[0]?.episode_id).toBe("old-first"));
  let oldRequest!: Promise<void>;
  act(() => { oldRequest = result.current.loadMore(); });
  const signal = read.mock.calls[1][4]?.signal;
  expect(result.current.loadingMore).toBe(true);
  rerender({ query: { ...DEFAULT_INVESTIGATION_QUERY, q: "new-query" } });
  expect(signal?.aborted).toBe(true);
  expect(result.current.loadingMore).toBe(false);
  await waitFor(() => expect(result.current.items[0]?.episode_id).toBe("new-first"));
  let newRequest!: Promise<void>;
  act(() => { newRequest = result.current.loadMore(); });
  expect(result.current.loadingMore).toBe(true);
  await act(async () => {
    if (outcome === "resolve") oldMore.resolve(page("stale-more", "stale-cursor"));
    else oldMore.reject(new Error("Stale request failed"));
    await oldRequest;
  });
  expect(result.current.items.map((item) => item.episode_id)).toEqual(["new-first"]);
  expect(result.current.page?.next_cursor).toBe("new-cursor");
  expect(result.current.error).toBe("");
  expect(result.current.loadingMore).toBe(true);
  await act(async () => { newMore.resolve(page("new-more")); await newRequest; });
  expect(result.current.items.map((item) => item.episode_id)).toEqual(["new-first", "new-more"]);
  expect(result.current.loadingMore).toBe(false);
  expect(read.mock.calls[3][2]).toBe("new-cursor");
});

it("does not use an old cursor while the new first page is still loading", async () => {
  const next = deferred<ActivityPage>();
  const read = vi.spyOn(investigationApi, "getActivityPage")
    .mockResolvedValueOnce(page("old", "old-cursor"))
    .mockReturnValueOnce(next.promise);
  const { result, rerender } = renderHook(({ query }) => useInvestigationData(query, 0),
    { initialProps: { query: DEFAULT_INVESTIGATION_QUERY } });
  await waitFor(() => expect(result.current.loading).toBe(false));
  rerender({ query: { ...DEFAULT_INVESTIGATION_QUERY, q: "new-query" } });
  expect(result.current.page).toBeNull();
  await act(async () => { await result.current.loadMore(); });
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => { next.resolve(page("new")); });
  expect(result.current.items[0].episode_id).toBe("new");
});

it("aborts pagination on unmount", async () => {
  const more = deferred<ActivityPage>();
  const read = vi.spyOn(investigationApi, "getActivityPage")
    .mockResolvedValueOnce(page("first", "cursor"))
    .mockReturnValueOnce(more.promise);
  const { result, unmount } = renderHook(() => useInvestigationData(DEFAULT_INVESTIGATION_QUERY, 0));
  await waitFor(() => expect(result.current.loading).toBe(false));
  let request!: Promise<void>;
  act(() => { request = result.current.loadMore(); });
  const signal = read.mock.calls[1][4]?.signal;
  unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => { more.resolve(page("late")); await request; });
});
