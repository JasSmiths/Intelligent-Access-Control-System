import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { workflowApi } from "../../api/workflows";
import { createReadSignals, ReadSignalsContext } from "../../lib/readSignals";
import { useNotificationCameras, useWorkflowData } from "./hooks";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
type Data = { rules: { id: string }[]; catalog: string };

it("ignores superseded responses, keeps current data during refresh, and aborts on unmount", async () => {
  const first = deferred<Data>(), second = deferred<Data>(), third = deferred<Data>();
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>()
    .mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockReturnValueOnce(third.promise);
  const { result, unmount } = renderHook(() => useWorkflowData(fetchData));
  let refreshed!: Promise<void>;
  act(() => { refreshed = result.current.load(); });
  expect(fetchData.mock.calls[0][0].signal.aborted).toBe(true);
  await act(async () => first.resolve({ rules: [{ id: "old" }], catalog: "old" }));
  await waitFor(() => expect(fetchData).toHaveBeenCalledTimes(2));
  await act(async () => { second.resolve({ rules: [{ id: "new" }], catalog: "new" }); await refreshed; });
  expect(result.current.rules).toEqual([{ id: "new" }]);
  act(() => { void result.current.load(); });
  expect(result.current.loading).toBe(false);
  expect(result.current.data?.catalog).toBe("new");
  unmount();
  expect(fetchData.mock.calls[2][0].signal.aborted).toBe(true);
  await act(async () => third.resolve({ rules: [], catalog: "disposed" }));
});
it("does not replace a completed mutation with an earlier list read", async () => {
  const slow = deferred<Data>();
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>().mockResolvedValueOnce({ rules: [{ id: "initial" }], catalog: "test" }).mockReturnValueOnce(slow.promise);
  const { result } = renderHook(() => useWorkflowData(fetchData));
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => { void result.current.load(); });
  act(() => result.current.setRules([{ id: "saved" }]));
  await act(async () => slow.resolve({ rules: [{ id: "stale" }], catalog: "test" }));
  expect(result.current.rules).toEqual([{ id: "saved" }]);
});
it("surfaces load failures and recovers through the same owner", async () => {
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>().mockRejectedValueOnce(new Error("Offline")).mockResolvedValueOnce({ rules: [], catalog: "recovered" });
  const { result } = renderHook(() => useWorkflowData(fetchData));
  await waitFor(() => expect(result.current.error).toBe("Offline"));
  expect(result.current.loading).toBe(false);
  await act(async () => { await result.current.load(); });
  expect(result.current.data?.catalog).toBe("recovered");
  expect(result.current.error).toBe("");
});
it("loads cameras only while a media-capable editor is open and cancels on close", async () => {
  const read = vi.spyOn(workflowApi, "getNotificationCameras").mockImplementation(() => new Promise(() => {}));
  const { rerender } = renderHook(({ enabled }) => useNotificationCameras(enabled), { initialProps: { enabled: false } });
  expect(read).not.toHaveBeenCalled();
  rerender({ enabled: true });
  expect(read).toHaveBeenCalledOnce();
  const signal = read.mock.calls[0][0]?.signal;
  rerender({ enabled: false });
  expect(signal?.aborted).toBe(true);
});
it("coalesces a realtime burst around actual HTTP", async () => {
  const first = deferred<Data>();
  let now = Date.now(); vi.spyOn(Date, "now").mockImplementation(() => now);
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>().mockReturnValueOnce(first.promise).mockResolvedValue({ rules: [{ id: "current" }], catalog: "test" });
  const one = createReadSignals("one");
  const { result } = renderHook(() => useWorkflowData(fetchData), { wrapper: ({ children }) => <ReadSignalsContext.Provider value={one}>{children}</ReadSignalsContext.Provider> });
  act(() => { for (let i = 0; i < 100; i++) one.publish({ type: "realtime", message: { type: "automation.run.updated", payload: {} } }); });
  expect(fetchData).toHaveBeenCalledOnce();
  now += 6000;
  await act(async () => { first.resolve({ rules: [{ id: "initial" }], catalog: "test" }); });
  expect(fetchData).toHaveBeenCalledTimes(2);
  expect(result.current.rules).toEqual([{ id: "current" }]);
});
it("can load after the StrictMode cleanup/remount probe", async () => {
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>().mockResolvedValue({ rules: [], catalog: "strict" });
  const { result } = renderHook(() => useWorkflowData(fetchData), { wrapper: ({ children }) => <React.StrictMode>{children}</React.StrictMode> });
  await waitFor(() => expect(result.current.data?.catalog).toBe("strict"));
});
