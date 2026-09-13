import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { workflowApi } from "../../api/workflows";
import { useNotificationCameras, useWorkflowData } from "./hooks";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
type Data = { rules: { id: string }[]; catalog: string };

it("ignores superseded responses, keeps current data during refresh, and aborts on unmount", async () => {
  const first = deferred<Data>(), second = deferred<Data>(), third = deferred<Data>();
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>()
    .mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockReturnValueOnce(third.promise);
  const { result, rerender, unmount } = renderHook(({ token }) => useWorkflowData(fetchData, token), { initialProps: { token: 0 } });
  rerender({ token: 1 });
  expect(fetchData.mock.calls[0][0].signal.aborted).toBe(true);
  await act(async () => second.resolve({ rules: [{ id: "new" }], catalog: "new" }));
  await act(async () => first.resolve({ rules: [{ id: "old" }], catalog: "old" }));
  expect(result.current.rules).toEqual([{ id: "new" }]);
  rerender({ token: 2 });
  expect(result.current.loading).toBe(false);
  expect(result.current.data?.catalog).toBe("new");
  unmount();
  expect(fetchData.mock.calls[2][0].signal.aborted).toBe(true);
});
it("does not replace a completed mutation with an earlier list read", async () => {
  const slow = deferred<Data>();
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>().mockResolvedValueOnce({ rules: [{ id: "initial" }], catalog: "test" }).mockReturnValueOnce(slow.promise);
  const { result, rerender } = renderHook(({ token }) => useWorkflowData(fetchData, token), { initialProps: { token: 0 } });
  await waitFor(() => expect(result.current.loading).toBe(false));
  rerender({ token: 1 });
  act(() => result.current.setRules([{ id: "saved" }]));
  await act(async () => slow.resolve({ rules: [{ id: "stale" }], catalog: "test" }));
  expect(result.current.rules).toEqual([{ id: "saved" }]);
});
it("surfaces load failures and recovers through the same owner", async () => {
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>().mockRejectedValueOnce(new Error("Offline")).mockResolvedValueOnce({ rules: [], catalog: "recovered" });
  const { result, rerender } = renderHook(({ token }) => useWorkflowData(fetchData, token), { initialProps: { token: 0 } });
  await waitFor(() => expect(result.current.error).toBe("Offline"));
  expect(result.current.loading).toBe(false);
  rerender({ token: 1 });
  await waitFor(() => expect(result.current.data?.catalog).toBe("recovered"));
  expect(result.current.error).toBe("");
});
it("loads cameras only while a media-capable editor is open and cancels on close", async () => {
  const read = vi.spyOn(workflowApi, "getNotificationCameras").mockImplementation(() => new Promise(() => {}));
  const { rerender } = renderHook(({ enabled, token }) => useNotificationCameras(enabled, token), { initialProps: { enabled: false, token: 0 } });
  rerender({ enabled: false, token: 1 });
  expect(read).not.toHaveBeenCalled();
  rerender({ enabled: true, token: 1 });
  expect(read).toHaveBeenCalledOnce();
  const signal = read.mock.calls[0][0]?.signal;
  rerender({ enabled: false, token: 1 });
  expect(signal?.aborted).toBe(true);
});
it("can load after the StrictMode cleanup/remount probe", async () => {
  const fetchData = vi.fn<(options: { signal: AbortSignal }) => Promise<Data>>().mockResolvedValue({ rules: [], catalog: "ready" });
  const { result } = renderHook(() => useWorkflowData(fetchData, 0), { wrapper: ({ children }) => <React.StrictMode>{children}</React.StrictMode> });
  await waitFor(() => expect(result.current.data?.catalog).toBe("ready"));
  expect(result.current.loading).toBe(false);
});
