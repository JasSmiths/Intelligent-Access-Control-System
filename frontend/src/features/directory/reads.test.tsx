import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as directory from "../../api/directory";
import type { Person } from "../../api/types";
import { DirectoryRefreshContext, useDirectoryOptions, useDirectoryPage } from "./reads";

const ash = { id: "ash", display_name: "Ash Smith" } as Person;
const zoe = { id: "zoe", display_name: "Zoe Smith" } as Person;
const page = (items: Person[], cursor: string | null = null) => ({ items, total: 75, next_cursor: cursor });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
beforeEach(() => { vi.spyOn(directory, "lookupDirectory").mockResolvedValue([]); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("uses bounded cursor pages and resets pagination when search changes", async () => {
  const read = vi.spyOn(directory, "readDirectory").mockResolvedValueOnce(page([ash], "page-two")).mockResolvedValueOnce(page([zoe])).mockResolvedValue(page([zoe]));
  const { result, rerender } = renderHook(({ query }) => useDirectoryPage("people", [], { query }), { initialProps: { query: "" } });
  await waitFor(() => expect(result.current.items).toEqual([ash]));
  act(() => result.current.next());
  await waitFor(() => expect(result.current.items).toEqual([zoe]));
  expect(read.mock.calls[1][1]?.cursor).toBe("page-two");
  rerender({ query: "Zoe" });
  await waitFor(() => expect(read).toHaveBeenCalledTimes(3));
  expect(read.mock.calls[2][1]).toMatchObject({ q: "Zoe", cursor: null });
  expect(result.current.canPrevious).toBe(false);
});

it("ignores an old search response after the user selects a different query", async () => {
  const pending = deferred<directory.DirectoryPage<Person>>();
  const read = vi.spyOn(directory, "readDirectory").mockReturnValueOnce(pending.promise).mockResolvedValue(page([zoe]));
  const { result, rerender } = renderHook(({ query }) => useDirectoryPage("people", [], { query }), { initialProps: { query: "" } });
  await waitFor(() => expect(read).toHaveBeenCalledOnce());
  const signal = read.mock.calls[0][2]?.signal;
  rerender({ query: "Zoe" });
  expect(signal?.aborted).toBe(true);
  await waitFor(() => expect(result.current.items).toEqual([zoe]));
  await act(async () => pending.resolve(page([ash])));
  expect(result.current.items).toEqual([zoe]);
});

it("retains selected identities outside the result page and reports refresh failure with retained rows", async () => {
  vi.mocked(directory.lookupDirectory).mockResolvedValue([zoe]);
  vi.spyOn(directory, "readDirectory").mockResolvedValueOnce(page([ash])).mockRejectedValue(new Error("Synthetic offline"));
  const { result } = renderHook(() => useDirectoryOptions("people", [], ["zoe"]));
  await waitFor(() => expect(result.current.items.map((item) => item.id).sort()).toEqual(["ash", "zoe"]));
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.error).toBe("Synthetic offline"));
  expect(result.current.items.map((item) => item.id).sort()).toEqual(["ash", "zoe"]);
});


it("refreshes outside-page selected metadata and gives the selected read precedence over an old page", async () => {
  const current = { ...zoe, is_active: false };
  vi.spyOn(directory, "readDirectory").mockResolvedValue(page([{ ...zoe, is_active: true }]));
  vi.mocked(directory.lookupDirectory).mockResolvedValueOnce([{ ...zoe, is_active: true }]).mockResolvedValue([current]);
  const { result, rerender } = renderHook(({ refreshToken }) => useDirectoryOptions("people", [], ["zoe"], true, refreshToken), { initialProps: { refreshToken: 0 } });
  await waitFor(() => expect(result.current.items[0]?.is_active).toBe(true));
  rerender({ refreshToken: 1 });
  await waitFor(() => expect(result.current.items[0]?.is_active).toBe(false));
  expect(directory.lookupDirectory).toHaveBeenCalledTimes(2);
});


it("cancels selected identity reads when disabled and rejects a late result", async () => {
  const pending = deferred<Person[]>();
  vi.mocked(directory.lookupDirectory).mockReturnValueOnce(pending.promise);
  vi.spyOn(directory, "readDirectory").mockResolvedValue(page([ash]));
  const { result, rerender } = renderHook(({ enabled }) => useDirectoryOptions("people", [], ["zoe"], enabled), { initialProps: { enabled: true } });
  await waitFor(() => expect(directory.lookupDirectory).toHaveBeenCalledOnce());
  const signal = vi.mocked(directory.lookupDirectory).mock.calls[0][2]?.signal;
  rerender({ enabled: false });
  expect(signal?.aborted).toBe(true);
  await act(async () => pending.resolve([zoe]));
  expect(result.current.items.some((item) => item.id === zoe.id)).toBe(false);
});

it("refreshes a nested selector from route invalidation while retaining its query and selected IDs", async () => {
  const changed = { ...zoe, display_name: "Zoe Updated", is_active: false };
  const read = vi.spyOn(directory, "readDirectory").mockResolvedValue(page([ash]));
  vi.mocked(directory.lookupDirectory).mockResolvedValueOnce([{ ...zoe, is_active: true }]).mockResolvedValue([changed]);
  let routeToken = 0;
  const wrapper = ({ children }: { children: React.ReactNode }) => <DirectoryRefreshContext.Provider value={routeToken}>{children}</DirectoryRefreshContext.Provider>;
  const seed = [ash];
  const { result, rerender } = renderHook(() => useDirectoryOptions("people", seed, ["zoe"]), { wrapper });
  await waitFor(() => expect(result.current.items.some((item) => item.display_name === "Zoe Smith")).toBe(true));
  act(() => result.current.setQuery("synthetic plate"));
  await waitFor(() => expect(read.mock.calls.at(-1)?.[1]?.q).toBe("synthetic plate"));
  routeToken = 1;
  rerender();
  await waitFor(() => expect(result.current.items.some((item) => item.display_name === "Zoe Updated")).toBe(true));
  expect(result.current.query).toBe("synthetic plate");
  expect(result.current.items.find((item) => item.id === zoe.id)?.is_active).toBe(false);
  expect(result.current.pageItems).toEqual([ash]);
  expect(directory.lookupDirectory).toHaveBeenCalledTimes(2);
});
