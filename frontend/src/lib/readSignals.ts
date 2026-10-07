import React from "react";
import type { RealtimeMessage } from "../api/types";

export const REALTIME_REFRESH_MIN_INTERVAL_MS = 5000;

export type ReadSignal =
  | { type: "realtime"; message: RealtimeMessage }
  | { type: "refresh"; reason: "manual" | "connection"; resetHistory: boolean };
type Listener = (signal: ReadSignal) => Promise<void> | void;

// Transport delivers every signal. Feature owners select policy and await their own reads.
export function createReadSignals(accountKey: string) {
  const listeners = new Set<Listener>();
  const observed = new WeakSet<Promise<void>>();
  function publish(signal: Extract<ReadSignal, { type: "realtime" }>): void;
  function publish(signal: Extract<ReadSignal, { type: "refresh" }>): Promise<void>;
  function publish(signal: ReadSignal): Promise<void> | void {
    if (signal.type === "realtime") {
      // Scheduling is synchronous. A blocked batch gets one rejection observer,
      // regardless of how many stream messages coalesce into that same batch.
      for (const listener of listeners) {
        try {
          const completion = listener(signal);
          if (completion && !observed.has(completion)) {
            observed.add(completion);
            void completion.catch(() => undefined); // Errors remain with each read owner.
          }
        } catch { /* Keep independent realtime owners schedulable. */ }
      }
      return;
    }
    const pending = [...listeners].map((listener) => {
      try { return Promise.resolve(listener(signal)); }
      catch (error) { return Promise.reject(error); }
    });
    return Promise.allSettled(pending).then((results) => {
      const failed = results.find((result) => result.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
    });
  }
  return { accountKey, publish,
    subscribe(listener: Listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    dispose() { listeners.clear(); }
  };
}
export type ReadSignals = ReturnType<typeof createReadSignals>;
export const ReadSignalsContext = React.createContext<ReadSignals | null>(null);
