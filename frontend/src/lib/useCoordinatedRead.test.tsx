import React from "react";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { RealtimeMessage } from "../api/types";
import { createReadSignals, ReadSignalsContext } from "./readSignals";
import { useCoordinatedRead } from "./useCoordinatedRead";
function deferred() { let resolve!: () => void; const promise = new Promise<void>((yes) => { resolve = yes; }); return { promise, resolve }; }
const event = { type: "vehicle_information.updated", payload: {} } as RealtimeMessage;
afterEach(() => { cleanup(); vi.useRealTimers(); });
it("retains a blocked HTTP read across predicate identity changes and coalesces 100 signals into one trailing read", async () => {
  vi.useFakeTimers();
  const bus = createReadSignals("account"); const blocked = deferred();
  const read = vi.fn().mockReturnValueOnce(blocked.promise).mockResolvedValue(undefined);
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={bus}>{children}</ReadSignalsContext.Provider>;
  const { rerender } = renderHook(({ matches }) => useCoordinatedRead(read, matches), { wrapper, initialProps: { matches: () => false } });
  expect(read).toHaveBeenCalledOnce();
  const originalSignal = read.mock.calls[0][0] as AbortSignal;
  rerender({ matches: () => true });
  expect(originalSignal.aborted).toBe(false);
  let pending!: Promise<void>[];
  act(() => { pending = Array.from({ length: 100 }, () => bus.publish({ type: "realtime", message: event })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(read).toHaveBeenCalledOnce();
  await act(async () => { blocked.resolve(); await Promise.all(pending); });
  expect(read).toHaveBeenCalledTimes(2);
});
it("keeps independent owners progressing when a different HTTP owner stalls or fails", async () => {
  const bus = createReadSignals("account"); const blocked = deferred(); const failure = new Error("Offline");
  const slow = vi.fn().mockReturnValueOnce(blocked.promise).mockRejectedValue(failure);
  const fast = vi.fn().mockResolvedValue(undefined); const matches = () => true;
  const wrapper = ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={bus}>{children}</ReadSignalsContext.Provider>;
  renderHook(() => { useCoordinatedRead(slow, matches); useCoordinatedRead(fast, matches); }, { wrapper });
  let pending!: Promise<void>; act(() => { pending = bus.publish({ type: "refresh", reason: "manual", resetHistory: false }); });
  const assertion = expect(pending).rejects.toThrow("Offline");
  await act(async () => { await Promise.resolve(); });
  expect(fast).toHaveBeenCalledTimes(2);
  await act(async () => { blocked.resolve(); await assertion; });
  expect(slow).toHaveBeenCalledTimes(2);
});
it("aborts old account work and discards pending invalidations after replacement", async () => {
  vi.useFakeTimers();
  const old = createReadSignals("old"); const next = createReadSignals("next"); const blocked = deferred();
  const read = vi.fn().mockReturnValueOnce(blocked.promise).mockResolvedValue(undefined); const matches = () => true;
  function Probe() { useCoordinatedRead(read, matches); return null; }
  const { rerender } = render(<ReadSignalsContext.Provider value={old}><Probe /></ReadSignalsContext.Provider>);
  const oldSignal = read.mock.calls[0][0] as AbortSignal;
  let pending!: Promise<void>; act(() => { pending = old.publish({ type: "realtime", message: event }); });
  rerender(<ReadSignalsContext.Provider value={next}><Probe /></ReadSignalsContext.Provider>);
  expect(oldSignal.aborted).toBe(true);
  await act(async () => { blocked.resolve(); await pending; await vi.advanceTimersByTimeAsync(10000); });
  expect(read).toHaveBeenCalledTimes(2);
});
