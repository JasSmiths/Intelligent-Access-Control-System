import React from "react";
import { api, isAbortError } from "../../api/client";
import type { AccessEvent, ExpectedPresenceSummary, Presence, RealtimeMessage } from "../../api/types";
import { ReadSignalsContext } from "../../lib/readSignals";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { auditResource } from "../../lib/realtime";
import { accessEventFromRealtime } from "./realtime";
export function dashboardEventMatches(event: RealtimeMessage) {
  return event.type === "access_event.finalized" || event.type === "access_event.finalize_failed" || event.type === "vehicle_information.updated" || event.type.startsWith("visitor_pass.") || event.type.startsWith("schedule.") || ["person", "vehicle", "visitor_pass", "group", "schedule", "settings"].includes(auditResource(event));
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
export function useDashboardReads() {
  // Presence retains the current unpaged contract until the P7 bounded summary migration.
  const presence = useDashboardRead<Presence[]>("/api/v1/presence", []);
  const expected = useDashboardRead<ExpectedPresenceSummary | null>("/api/v1/presence/expected-today", null);
  const events = useDashboardRead<AccessEvent[]>("/api/v1/events?limit=40", []);
  const signals = React.useContext(ReadSignalsContext);
  React.useLayoutEffect(() => signals?.subscribe((signal) => {
    if (signal.type !== "realtime") return;
    const finalized = accessEventFromRealtime(signal.message);
    if (finalized) events.setValue((current) => [finalized, ...current.filter((item) => item.id !== finalized.id)].slice(0, 40));
  }), [signals, events.setValue]);
  const refresh = React.useCallback(async () => {
    const results = await Promise.allSettled([presence.refresh(), expected.refresh(), events.refresh()]);
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
  }, [presence.refresh, expected.refresh, events.refresh]);
  return { refresh, presence: presence.value, expectedPresence: expected.value, events: events.value, error: presence.error || expected.error || events.error };
}
