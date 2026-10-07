import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { UserAccount } from "../api/types";
import { createReadSignals } from "../lib/readSignals";
import { useOperationalReads } from "./operationalReads";
const admin = { id: "admin", role: "admin" } as UserAccount;
function setters() { return { anomalies: vi.fn(), integrationStatus: vi.fn(), maintenanceStatus: vi.fn() }; }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("loads only shared operational reads and does not withhold a feature behind an unrelated pending read", async () => {
  const blocked = deferred<unknown>(); const signals = createReadSignals("admin"); const updates = setters();
  const read = vi.spyOn(api, "get").mockImplementation((path) => path.includes("maintenance") ? blocked.promise : Promise.resolve([]));
  const { result } = renderHook(() => useOperationalReads("schedules", admin, signals, updates));
  await waitFor(() => expect(updates.anomalies).toHaveBeenCalledOnce());
  expect(read.mock.calls.map(([path]) => path)).toEqual(["/api/v1/alerts?status=open&limit=100", "/api/v1/maintenance/status"]);
  expect(result.current.loading).toBe(false);
  let next!: Promise<void>; act(() => { next = signals.publish({ type: "refresh", reason: "manual", resetHistory: false }); });
  await waitFor(() => expect(updates.anomalies).toHaveBeenCalledTimes(2));
  await act(async () => { blocked.resolve({}); await next; });
});
it("keeps a critical gate failure until that owner succeeds while independent alerts continue", async () => {
  let now = Date.now(); vi.spyOn(Date, "now").mockImplementation(() => now);
  let failed = true; const signals = createReadSignals("admin"); const updates = setters();
  vi.spyOn(api, "get").mockImplementation(async (path) => { if (path.includes("/gate/") && failed) throw new Error("Gate status unavailable"); return []; });
  const { result } = renderHook(() => useOperationalReads("people", admin, signals, updates));
  await waitFor(() => expect(result.current.readState).toBe("unavailable"));
  now += 6000;
  await act(async () => { await signals.publish({ type: "realtime", message: { type: "alerts.updated", payload: {} } }); });
  expect(result.current.failedKeys).toEqual(["integrationStatus"]); expect(result.current.readState).toBe("unavailable");
  failed = false;
  await act(async () => { await signals.publish({ type: "refresh", reason: "manual", resetHistory: false }); });
  expect(result.current.readState).toBe("ready");
});
it("discards rejected and successful completions plus queued work from a replaced account", async () => {
  const blocked = deferred<unknown>(); const updates = setters();
  const read = vi.spyOn(api, "get").mockReturnValue(blocked.promise);
  const firstSignals = createReadSignals("one");
  const { result, rerender } = renderHook(({ signals, user }) => useOperationalReads("people", user, signals, updates), { initialProps: { signals: firstSignals, user: admin } });
  let queued!: Promise<void>; act(() => { queued = firstSignals.publish({ type: "refresh", reason: "connection", resetHistory: false }); });
  const oldSignals = read.mock.calls.map(([, options]) => options?.signal);
  read.mockResolvedValue([]);
  rerender({ signals: createReadSignals("two"), user: { ...admin, id: "two" } });
  expect(oldSignals.every((signal) => signal?.aborted)).toBe(true);
  await act(async () => { blocked.reject(new Error("Old account failure")); await queued; });
  await waitFor(() => expect(updates.integrationStatus).toHaveBeenCalledOnce());
  expect(result.current.readState).toBe("ready"); expect(read).toHaveBeenCalledTimes(6);
});
it("remains functional through StrictMode owner activation and cleanup", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]); const updates = setters(); const signals = createReadSignals("admin");
  const { result } = renderHook(() => useOperationalReads("dashboard", admin, signals, updates), { wrapper: ({ children }) => <React.StrictMode>{children}</React.StrictMode> });
  await waitFor(() => expect(updates.integrationStatus).toHaveBeenCalledOnce());
  expect(result.current.readState).toBe("ready");
});
