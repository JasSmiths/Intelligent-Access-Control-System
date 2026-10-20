import React from "react";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createReadSignals, ReadSignalsContext, type ReadSignals } from "../../lib/readSignals";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { settingsEventMatches } from "./invalidation";
import { useZoneShadowRefresh } from "./useZoneShadowRefresh";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((yes) => { resolve = yes; });
  return { promise, resolve };
}
function wrapper(signals: ReadSignals) {
  return ({ children }: { children: React.ReactNode }) => <ReadSignalsContext.Provider value={signals}>{children}</ReadSignalsContext.Provider>;
}
function visibility(value: DocumentVisibilityState) {
  vi.spyOn(document, "visibilityState", "get").mockReturnValue(value);
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it("coalesces committed observation hints with empty projected payloads without invalidating settings reads", async () => {
  const signals = createReadSignals("account");
  const read = vi.fn().mockResolvedValue(undefined);
  const settingsRead = vi.fn().mockResolvedValue(undefined);
  renderHook(() => {
    useZoneShadowRefresh(read);
    useCoordinatedRead(settingsRead, settingsEventMatches);
  }, { wrapper: wrapper(signals) });
  await act(async () => {});
  act(() => {
    signals.publish({ type: "realtime", message: { type: "notification.sent", payload: {} } });
    for (let index = 0; index < 100; index++) {
      signals.publish({ type: "realtime", message: { type: "lpr_zone_shadow.observed", payload: {} } });
    }
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(4999); });
  expect(read).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(read).toHaveBeenCalledTimes(2);
  expect(settingsRead).toHaveBeenCalledOnce();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
    signals.publish({ type: "realtime", message: { type: "audit.log.created", payload: { action: "settings.update" } } });
  });
  expect(read).toHaveBeenCalledTimes(3);
  expect(settingsRead).toHaveBeenCalledTimes(2);
  act(() => { signals.publish({ type: "realtime", message: { type: "notification.sent", payload: {} } }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(read).toHaveBeenCalledTimes(3);
});

it("repairs a lost hint only after sixty visible seconds and refreshes immediately on resume", async () => {
  const read = vi.fn().mockResolvedValue(undefined);
  renderHook(() => useZoneShadowRefresh(read));
  await act(async () => { await vi.advanceTimersByTimeAsync(59_999); });
  expect(read).toHaveBeenCalledOnce();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(read).toHaveBeenCalledTimes(2);
  visibility("hidden");
  await act(async () => { await vi.advanceTimersByTimeAsync(180_000); });
  expect(read).toHaveBeenCalledTimes(2);
  await act(async () => { visibility("visible"); });
  expect(read).toHaveBeenCalledTimes(3);
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(read).toHaveBeenCalledTimes(4);
});

it("does not start fallback reads while mounted hidden, and removes timers and listeners on unmount", async () => {
  visibility("hidden");
  const blocked = deferred();
  const read = vi.fn().mockReturnValue(blocked.promise);
  const { unmount } = renderHook(() => useZoneShadowRefresh(read));
  const signal = read.mock.calls[0][0] as AbortSignal;
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(read).toHaveBeenCalledOnce(); // Initial read remains owned by useCoordinatedRead.
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => { blocked.resolve(); });
  visibility("visible");
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(read).toHaveBeenCalledOnce();
});

it("aborts old-account reads and drops their queued hints while the replacement account repairs independently", async () => {
  const first = createReadSignals("first");
  const second = createReadSignals("second");
  const blocked = deferred();
  const read = vi.fn().mockReturnValueOnce(blocked.promise).mockResolvedValue(undefined);
  const Host = () => {
    useZoneShadowRefresh(read);
    return null;
  };
  // A provider replacement models the account-owned signals lifetime used by the app.
  const view = render(<ReadSignalsContext.Provider value={first}><Host /></ReadSignalsContext.Provider>);
  const oldSignal = read.mock.calls[0][0] as AbortSignal;
  act(() => { first.publish({ type: "realtime", message: { type: "lpr_zone_shadow.observed", payload: {} } }); });
  view.rerender(<ReadSignalsContext.Provider value={second}><Host /></ReadSignalsContext.Provider>);
  expect(oldSignal.aborted).toBe(true);
  await act(async () => { blocked.resolve(); });
  expect(read).toHaveBeenCalledTimes(2);
  act(() => { first.publish({ type: "realtime", message: { type: "lpr_zone_shadow.observed", payload: {} } }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(read).toHaveBeenCalledTimes(3);
  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(read).toHaveBeenCalledTimes(3);
});
