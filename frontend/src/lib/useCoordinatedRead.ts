import React from "react";
import type { RealtimeMessage } from "../api/types";
import { ReadSignalsContext, REALTIME_REFRESH_MIN_INTERVAL_MS } from "./readSignals";
import { createRefreshCoordinator } from "./refreshCoordinator";

// Own request lifetime and serialization; data, errors and event policy stay with the feature.
export function useCoordinatedRead(
  read: (signal: AbortSignal) => Promise<void>,
  eventMatches: (message: RealtimeMessage) => boolean,
  enabled = true,
  debounceMs = 0
) {
  const signals = React.useContext(ReadSignalsContext);
  const matchesRef = React.useRef(eventMatches);
  React.useLayoutEffect(() => { matchesRef.current = eventMatches; }, [eventMatches]);
  const owner = React.useMemo(() => {
    let active: AbortController | null = null;
    let coordinator: ReturnType<typeof createRefreshCoordinator<"read">> | null = null;
    let firstDue = 0;
    return {
      activate() {
        firstDue = Date.now() + debounceMs;
        coordinator = createRefreshCoordinator<"read">(async () => {
          firstDue = 0;
          const controller = new AbortController();
          active = controller;
          try { await read(controller.signal); }
          catch (error) { if (!controller.signal.aborted) throw error; }
          finally { if (active === controller) active = null; }
        });
      },
      request(realtime = false, urgent = false) {
        if (!enabled) return Promise.resolve();
        if (urgent) active?.abort();
        return coordinator?.request(new Set(["read"]), realtime ? REALTIME_REFRESH_MIN_INTERVAL_MS : 0, urgent ? 0 : firstDue) ?? Promise.resolve();
      },
      dispose() { active?.abort(); coordinator?.dispose(); coordinator = null; }
    };
  }, [read, enabled, debounceMs, signals]);
  React.useLayoutEffect(() => {
    owner.activate();
    return () => owner.dispose();
  }, [owner]);
  React.useLayoutEffect(() => {
    const unsubscribe = signals?.subscribe((signal) => {
      if (signal.type === "realtime") return matchesRef.current(signal.message) ? owner.request(true) : undefined;
      return owner.request(signal.reason === "connection", signal.reason === "manual");
    });
    return () => unsubscribe?.();
  }, [owner, signals]);
  React.useEffect(() => { void owner.request().catch(() => undefined); }, [owner]);
  return React.useCallback(() => owner.request(false, true), [owner]);
}
