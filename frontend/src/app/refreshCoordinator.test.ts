import { afterEach, expect, it, vi } from "vitest";
import { createRefreshCoordinator, type RefreshSelection } from "./refreshCoordinator";
import type { ShellDataKey } from "./navigation";

function deferred() { let resolve!: () => void; let reject!: (error: unknown) => void; const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function selection(...keys: ShellDataKey[]): RefreshSelection { return { keys: new Set(keys), route: false }; }
afterEach(() => vi.useRealTimers());

it("merges burst invalidations and retains those received during a slow read", async () => {
  vi.useFakeTimers();
  const slow = deferred();
  const execute = vi.fn().mockReturnValueOnce(slow.promise).mockResolvedValue(undefined);
  const queue = createRefreshCoordinator(execute);
  const initial = queue.request(selection("people"));
  const second = queue.request(selection("schedules"), 5000);
  const third = queue.request({ ...selection("vehicles"), route: true }, 5000);
  await vi.advanceTimersByTimeAsync(6000);
  expect(execute).toHaveBeenCalledTimes(1);
  slow.resolve();
  await Promise.all([initial, second, third]);
  expect(execute).toHaveBeenCalledTimes(2);
  expect(execute.mock.calls[1][0]).toEqual({ keys: new Set(["schedules", "vehicles"]), route: true });
  queue.dispose();
});
it("throttles stream updates, while an explicit refresh can advance a pending batch", async () => {
  vi.useFakeTimers();
  const execute = vi.fn().mockResolvedValue(undefined);
  const queue = createRefreshCoordinator(execute);
  await queue.request(selection("people"));
  const pending = queue.request(selection("people"), 5000);
  await vi.advanceTimersByTimeAsync(4999);
  expect(execute).toHaveBeenCalledOnce();
  const manual = queue.request(selection("schedules"));
  await Promise.all([manual, pending]);
  expect(execute).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(10000);
  expect(execute).toHaveBeenCalledTimes(2);
  queue.dispose();
});
it("does not strand the next batch after failure", async () => {
  const slow = deferred();
  const execute = vi.fn().mockReturnValueOnce(slow.promise).mockResolvedValue(undefined);
  const queue = createRefreshCoordinator(execute);
  const initial = queue.request(selection("people"));
  const rejection = expect(initial).rejects.toThrow("Offline");
  const next = queue.request(selection("schedules"));
  slow.reject(new Error("Offline"));
  await rejection;
  await next;
  expect(execute).toHaveBeenCalledTimes(2);
  queue.dispose();
});
it("cleans pending timers and waiters when its route owner is disposed", async () => {
  vi.useFakeTimers();
  const execute = vi.fn().mockResolvedValue(undefined);
  const queue = createRefreshCoordinator(execute);
  await queue.request(selection("people"));
  const pending = queue.request(selection("schedules"), 5000);
  queue.dispose();
  await pending;
  await vi.advanceTimersByTimeAsync(10000);
  expect(execute).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});
