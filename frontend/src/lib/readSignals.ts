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
  return {
    accountKey,
    subscribe(listener: Listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    async publish(signal: ReadSignal) {
      const pending = [...listeners].map((listener) => {
        try { return Promise.resolve(listener(signal)); }
        catch (error) { return Promise.reject(error); }
      });
      const results = await Promise.allSettled(pending);
      const failed = results.find((result) => result.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
    },
    dispose() { listeners.clear(); }
  };
}
export type ReadSignals = ReturnType<typeof createReadSignals>;
export const ReadSignalsContext = React.createContext<ReadSignals | null>(null);
