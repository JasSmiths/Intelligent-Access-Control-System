import React from "react";
import { api, isAbortError } from "../../api/client";
import type { AccessEvent, ExpectedPresenceOverview, PresenceSummary, RealtimeMessage } from "../../api/types";
import { ReadSignalsContext } from "../../lib/readSignals";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { auditResource } from "../../lib/realtime";
import { accessEventFromRealtime } from "./realtime";
export function dashboardEventMatches(event: RealtimeMessage) {
  return event.type === "access_event.finalized" || event.type === "access_event.updated" || event.type === "access_event.finalize_failed" || event.type === "vehicle_information.updated" || event.type.startsWith("visitor_pass.") || event.type.startsWith("schedule.") || ["person", "vehicle", "visitor_pass", "group", "schedule", "settings"].includes(auditResource(event));
}
function useDashboardRead<T>(path: string, initial: T) {
  const [value, setValue] = React.useState(initial); const [error, setError] = React.useState("");
  const read = React.useCallback(async (signal: AbortSignal) => {
    setError("");
    try { const result = await api.get<T>(path, { signal }); if (!signal.aborted) setValue(result); }
    catch (failure) { if (!signal.aborted && !isAbortError(failure)) setError(failure instanceof Error ? failure.message : "Dashboard read unavailable"); throw failure; }
  }, [path]);
  const refresh = useCoordinatedRead(read, dashboardEventMatches);
  return { value, setValue, error, refresh };
}
// Revision knowledge stays in the existing bounded activity rows. A GET started
// before a realtime update cannot erase it or replace a newer revision.
function useDashboardEvents() {
  const signals = React.useContext(ReadSignalsContext);
  const [value, setValue] = React.useState<AccessEvent[]>([]);
  const [error, setError] = React.useState("");
  const rows = React.useRef<AccessEvent[]>([]);
  const replace = React.useCallback((change: (current: AccessEvent[]) => AccessEvent[]) => {
    setValue((current) => { const next = change(current); rows.current = next; return next; });
  }, []);
  React.useLayoutEffect(() => { rows.current = []; setValue([]); setError(""); }, [signals]);
  const read = React.useCallback(async (signal: AbortSignal) => {
    const started = new Map(rows.current.map((row) => [row.id, row.resource_revision]));
    setError("");
    try {
      const incoming = await api.get<AccessEvent[]>("/api/v1/events?limit=40", { signal });
      if (signal.aborted) return;
      replace((current) => {
        const known = new Map(current.map((row) => [row.id, row]));
        const merged = new Map(incoming.map((row) => {
          const latest = known.get(row.id);
          return [row.id, latest && latest.resource_revision > (row.resource_revision ?? 0) ? latest : row];
        }));
        for (const row of current) {
          if (!merged.has(row.id) && (!started.has(row.id) || row.resource_revision > started.get(row.id)!)) merged.set(row.id, row);
        }
        return newestActivity([...merged.values()]);
      });
    } catch (failure) {
      if (!signal.aborted && !isAbortError(failure)) setError(failure instanceof Error ? failure.message : "Dashboard read unavailable");
      throw failure;
    }
  }, [replace]);
  const refresh = useCoordinatedRead(read, dashboardEventMatches);
  React.useLayoutEffect(() => signals?.subscribe((signal) => {
    if (signal.type !== "realtime") return;
    const incoming = accessEventFromRealtime(signal.message);
    if (incoming) replace((current) => {
      const known = current.find((row) => row.id === incoming.id);
      if (known && known.resource_revision > incoming.resource_revision) return current;
      return newestActivity([incoming, ...current.filter((row) => row.id !== incoming.id)]);
    });
  }), [signals, replace]);
  return { value, error, refresh };
}
function newestActivity(rows: AccessEvent[]) {
  return rows.sort((left, right) => right.occurred_at.localeCompare(left.occurred_at) || right.id.localeCompare(left.id)).slice(0, 40);
}
export function useDashboardReads() {
  const presence = useDashboardRead<PresenceSummary>("/api/v1/presence/summary", { counts: { total: 0, present: 0, exited: 0, unknown: 0 }, inside: { people: [], hidden: 0 } });
  const expected = useDashboardRead<ExpectedPresenceOverview | null>("/api/v1/presence/expected-today/summary", null);
  const events = useDashboardEvents();
  const refresh = React.useCallback(async () => {
    const results = await Promise.allSettled([presence.refresh(), expected.refresh(), events.refresh()]);
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
  }, [presence.refresh, expected.refresh, events.refresh]);
  return { refresh, presence: presence.value.inside.people, presenceCounts: presence.value.counts, insideHidden: presence.value.inside.hidden, expectedPresence: expected.value, events: events.value, error: presence.error || expected.error || events.error };
}
