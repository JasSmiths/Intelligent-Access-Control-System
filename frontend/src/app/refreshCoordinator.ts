import type { ShellDataKey } from "./navigation";

export type RefreshSelection = { keys: Set<ShellDataKey>; route: boolean };
type Waiter = { resolve: () => void; reject: (error: unknown) => void };
type Batch = { selection: RefreshSelection; due: number; waiters: Waiter[] };

// Serialize reads and retain invalidations received during a read. Each batch
// owns its waiters, so a failed batch cannot strand the next one.
export function createRefreshCoordinator(execute: (selection: RefreshSelection) => Promise<void>) {
  let pending: Batch | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running = false;
  let disposed = false;
  let lastStarted = -Infinity;

  function schedule() {
    if (disposed || running || !pending) return;
    if (timer !== null) clearTimeout(timer);
    const delay = Math.max(0, pending.due - Date.now());
    if (delay) timer = setTimeout(() => { timer = null; void flush(); }, delay);
    else void flush();
  }
  async function flush() {
    if (disposed || running || !pending) return;
    const batch = pending;
    pending = null;
    running = true;
    lastStarted = Date.now();
    try {
      await execute(batch.selection);
      batch.waiters.forEach(({ resolve }) => resolve());
    } catch (error) {
      batch.waiters.forEach(({ reject }) => reject(error));
    } finally {
      running = false;
      schedule();
    }
  }
  return {
    request(selection: RefreshSelection, minInterval = 0): Promise<void> {
      if (disposed || (!selection.keys.size && !selection.route)) return Promise.resolve();
      return new Promise((resolve, reject) => {
        const due = Math.max(Date.now(), lastStarted + minInterval);
        if (!pending) pending = { selection: { keys: new Set(), route: false }, due, waiters: [] };
        selection.keys.forEach((key) => pending!.selection.keys.add(key));
        pending.selection.route ||= selection.route;
        pending.due = Math.min(pending.due, due);
        pending.waiters.push({ resolve, reject });
        schedule();
      });
    },
    dispose() {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
      pending?.waiters.forEach(({ resolve }) => resolve());
      pending = null;
    }
  };
}
