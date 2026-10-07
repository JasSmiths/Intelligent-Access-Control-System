import React from "react";
import { getHistory, type HistoryPage } from "../api/history";
import { isAbortError } from "../api/client";
import type { RealtimeMessage } from "../api/types";
import { auditResource } from "../lib/realtime";
import { ReadSignalsContext, REALTIME_REFRESH_MIN_INTERVAL_MS } from "../lib/readSignals";
import { createRefreshCoordinator } from "../lib/refreshCoordinator";
const EMPTY_ITEMS: never[] = [];
function historyEventMatches(path: string, event: RealtimeMessage) {
  if (event.type === "access_event.finalized" || event.type === "access_event.finalize_failed") return true;
  if (path.includes("alerts")) return event.type === "alerts.updated";
  return event.type === "vehicle_information.updated" || event.type.startsWith("visitor_pass.") || ["vehicle", "visitor_pass"].includes(auditResource(event));
}

export function useHistoryPage<T>(path: string, filters: URLSearchParams) {
  const filterKey = filters.toString();
  const signals = React.useContext(ReadSignalsContext);
  const [pages, setPages] = React.useState<Array<HistoryPage<T>>>([]);
  const [index, setIndex] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [newActivity, setNewActivity] = React.useState(false);
  const state = React.useRef({ pages, index }); state.current = { pages, index };
  const owner = React.useMemo(() => {
    const noRead = Promise.resolve();
    let active: AbortController | null = null;
    let generation = 0;
    let navigationPending = false;
    let destination = { cursor: null as string | null, index: 0 };
    let queue: ReturnType<typeof createRefreshCoordinator<"read">> | null = null;
    return {
      activate() {
        setPages([]); setIndex(0); setNewActivity(false);
        queue = createRefreshCoordinator<"read">(async () => {
          const controller = new AbortController(); active = controller;
          const request = destination; const currentGeneration = generation;
          setLoading(true); setError("");
          try {
            const result = await getHistory<T>(path, new URLSearchParams(filterKey), request.cursor, controller.signal);
            if (controller.signal.aborted || currentGeneration !== generation) return;
            setPages((current) => request.index === 0 ? [result] : [...current.slice(0, request.index), result]);
            setIndex(request.index);
          } catch (failure) {
            if (controller.signal.aborted || currentGeneration !== generation || isAbortError(failure)) return;
            setError(failure instanceof Error ? failure.message : "History is unavailable."); throw failure;
          } finally { if (currentGeneration === generation) { navigationPending = false; if (!controller.signal.aborted) setLoading(false); } }
        });
      },
      latest(reset: boolean, realtime = false) {
        if (!reset && (state.current.index > 0 || navigationPending && destination.index > 0)) { setNewActivity(true); return noRead; }
        if (reset) { navigationPending = false; active?.abort(); generation += 1; setPages([]); setIndex(0); setNewActivity(false); }
        destination = { cursor: null, index: 0 };
        return queue?.request(new Set(["read"]), realtime ? REALTIME_REFRESH_MIN_INTERVAL_MS : 0) ?? Promise.resolve();
      },
      next(cursor: string, target: number) {
        active?.abort(); generation += 1; navigationPending = true; destination = { cursor, index: target };
        return queue?.request(new Set(["read"])) ?? Promise.resolve();
      },
      dispose() { generation += 1; active?.abort(); queue?.dispose(); queue = null; }
    };
  }, [path, filterKey, signals]);
  React.useLayoutEffect(() => { owner.activate(); return () => owner.dispose(); }, [owner]);
  React.useEffect(() => { void owner.latest(true).catch(() => undefined); }, [owner]);
  React.useLayoutEffect(() => signals?.subscribe((signal) => {
    if (signal.type === "realtime") return historyEventMatches(path, signal.message) ? owner.latest(false, true) : undefined;
    return owner.latest(signal.resetHistory, signal.reason === "connection");
  }), [owner, signals, path]);
  const page = pages[index] ?? null;
  return { items: page?.items ?? EMPTY_ITEMS, asOf: page?.as_of ?? null, nextCursor: page?.next_cursor ?? null,
    index, loading, error, newActivity,
    next: () => {
      if (!page?.next_cursor || loading) return;
      if (pages[index + 1]) setIndex(index + 1);
      else void owner.next(page.next_cursor, index + 1).catch(() => undefined);
    },
    previous: () => { if (index > 0 && !loading) setIndex(index - 1); },
    reload: () => owner.latest(true),
    refresh: () => { void owner.latest(true).catch(() => undefined); }
  };
}
