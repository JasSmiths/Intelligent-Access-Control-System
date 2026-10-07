type Batch<Key extends string> = { selection: Set<Key>; due: number; completion: Promise<void>; resolve: () => void; reject: (error: unknown) => void };

// Serialize reads and retain invalidations received during a read. Each batch
// owns one completion promise, so a failed batch cannot strand the next one.
export function createRefreshCoordinator<Key extends string>(execute: (selection: Set<Key>) => Promise<void>) {
  let pending: Batch<Key> | null = null;
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
      batch.resolve();
    } catch (error) {
      batch.reject(error);
    } finally {
      running = false;
      schedule();
    }
  }
  return {
    request(selection: Set<Key>, minInterval = 0, notBefore = 0): Promise<void> {
      if (disposed || !selection.size) return Promise.resolve();
      const due = Math.max(Date.now(), lastStarted + minInterval, notBefore);
      if (!pending) {
        let resolve!: () => void;
        let reject!: (error: unknown) => void;
        const completion = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
        pending = { selection: new Set(), due, completion, resolve, reject };
      }
      selection.forEach((key) => pending!.selection.add(key));
      pending.due = Math.min(pending.due, due);
      const completion = pending.completion;
      schedule();
      return completion;
    },
    dispose() {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
      pending?.resolve();
      pending = null;
    }
  };
}
