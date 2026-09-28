import React from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { UserAccount, ViewKey } from "../api/types";
import { useShellRefresh } from "./useShellRefresh";
import type { ShellDataKey } from "./navigation";

function setters() { return { presence: vi.fn(), expectedPresence: vi.fn(), events: vi.fn(), anomalies: vi.fn(), people: vi.fn(), vehicles: vi.fn(), groups: vi.fn(), schedules: vi.fn(), integrationStatus: vi.fn(), maintenanceStatus: vi.fn() }; }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const admin = { id: "admin-one", role: "admin" } as UserAccount;

it("requests only selected resources and does not reload an independently mounted route on initial load", async () => {
  const read = vi.spyOn(api, "get").mockResolvedValue([]);
  const updates = setters();
  const { result } = renderHook(() => useShellRefresh("schedules", admin, updates));
  await act(async () => { await result.current.initialRefresh(); });
  expect(read.mock.calls.map(([path]) => path)).toEqual(["/api/v1/alerts?status=open&limit=100", "/api/v1/maintenance/status", "/api/v1/schedules"]);
  expect(result.current.dataRefreshToken).toBe(0);
  read.mockClear();
  await act(async () => { await result.current.refresh(); });
  expect(result.current.dataRefreshToken).toBe(1);
});
it("prevents slow responses from the previous route or account from updating state", async () => {
  const old = deferred<unknown>();
  const read = vi.spyOn(api, "get").mockReturnValue(old.promise);
  const updates = setters();
  const { result, rerender } = renderHook(({ view, user }) => useShellRefresh(view, user, updates), { initialProps: { view: "people" as ViewKey, user: admin as UserAccount | null } });
  let initial!: Promise<void>;
  act(() => { initial = result.current.initialRefresh(); });
  const oldSignal = read.mock.calls[0][1]?.signal;
  rerender({ view: "schedules", user: admin });
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => { old.resolve([{ id: "stale" }]); await initial; });
  expect(updates.people).not.toHaveBeenCalled();
  read.mockResolvedValue([]);
  await act(async () => { await result.current.initialRefresh(); });
  expect(updates.schedules).toHaveBeenCalledOnce();
  const currentSignal = read.mock.calls.at(-1)?.[1]?.signal;
  rerender({ view: "schedules", user: null });
  expect(currentSignal?.aborted).toBe(true);
});
it("waits for all failed-batch reads before starting another refresh", async () => {
  const slow = deferred<unknown>();
  const read = vi.spyOn(api, "get").mockImplementation((path) => path.includes("/alerts?") ? Promise.reject(new Error("Offline")) : slow.promise);
  const { result } = renderHook(() => useShellRefresh("schedules", admin, setters()));
  let first!: Promise<void>;
  let next!: Promise<void>;
  act(() => { first = result.current.refresh(); next = result.current.refresh(); });
  const failed = expect(first).rejects.toThrow("Offline");
  expect(read).toHaveBeenCalledTimes(3);
  read.mockResolvedValue([]);
  await act(async () => { slow.resolve([]); await failed; await next; });
  expect(read).toHaveBeenCalledTimes(6);
  expect(result.current.loading).toBe(false);
});
it("remains functional after the StrictMode setup/cleanup probe", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]);
  const updates = setters();
  const { result } = renderHook(() => useShellRefresh("schedules", admin, updates), { wrapper: ({ children }) => <React.StrictMode>{children}</React.StrictMode> });
  await act(async () => { await result.current.initialRefresh(); });
  expect(updates.schedules).toHaveBeenCalledOnce();
  expect(result.current.loading).toBe(false);
});
it("retains an unavailable critical read across an unrelated successful invalidation", async () => {
  let now = Date.now();
  vi.spyOn(Date, "now").mockImplementation(() => now);
  let schedulesAvailable = false;
  vi.spyOn(api, "get").mockImplementation((path) => path === "/api/v1/schedules" && !schedulesAvailable
    ? Promise.reject(new Error("Synthetic schedules outage")) : Promise.resolve([]));
  const { result } = renderHook(() => useShellRefresh("schedules", admin, setters()));
  await act(async () => { await expect(result.current.initialRefresh()).rejects.toThrow("Synthetic schedules outage"); });
  expect(result.current.readState).toBe("unavailable");
  expect(result.current.failedKeys).toContain("schedules");
  now += 6000;
  await act(async () => { await result.current.refreshRealtime({ keys: new Set<ShellDataKey>(["anomalies"]), route: false }); });
  expect(result.current.readState).toBe("unavailable");
  expect(result.current.failedKeys).toContain("schedules");
  schedulesAvailable = true;
  await act(async () => { await result.current.refresh(); });
  expect(result.current.readState).toBe("ready");
  expect(result.current.failedKeys).toEqual([]);
});
