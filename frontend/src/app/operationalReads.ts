import React from "react";
import { api } from "../api/client";
import type { Anomaly, IntegrationStatus, MaintenanceStatus, RealtimeMessage, UserAccount, ViewKey } from "../api/types";
import type { ReadSignals } from "../lib/readSignals";
import { auditResource } from "../lib/realtime";
import { useCoordinatedRead } from "../lib/useCoordinatedRead";

// These are shared console controls and indicators. Route collections belong to features.
type OperationalData = { anomalies: Anomaly[]; integrationStatus: IntegrationStatus | null; maintenanceStatus: MaintenanceStatus | null };
export type OperationalDataKey = keyof OperationalData;
type Setters = { [Key in OperationalDataKey]: (value: OperationalData[Key]) => void };
const paths: Record<OperationalDataKey, string> = {
  anomalies: "/api/v1/alerts?status=open&limit=100",
  integrationStatus: "/api/v1/integrations/gate/status",
  maintenanceStatus: "/api/v1/maintenance/status"
};
function eventMatches(key: OperationalDataKey, event: RealtimeMessage) {
  if (key === "anomalies") return ["alerts.updated", "access_event.finalized", "access_event.updated", "access_event.finalize_failed"].includes(event.type);
  const resource = auditResource(event);
  if (key === "maintenanceStatus") return resource === "settings" || event.type === "maintenance_mode.changed";
  return ["home_assistant.status", "access_device.status"].includes(event.type) || event.type.startsWith("schedule.") || ["access_device", "esphome", "home_assistant", "schedule", "settings"].includes(resource);
}
function useOperationalRead<Key extends OperationalDataKey>(key: Key, enabled: boolean, signals: ReadSignals, setter: Setters[Key]) {
  const identity = `${signals.accountKey}:${key}`;
  const [state, setState] = React.useState<{ identity: string; pending: boolean; loaded: boolean; failed: boolean } | null>(null);
  const setterRef = React.useRef(setter);
  React.useLayoutEffect(() => { setterRef.current = setter; }, [setter]);
  const read = React.useCallback(async (signal: AbortSignal) => {
    setState((current) => ({ identity, pending: true, loaded: current?.identity === identity && current.loaded, failed: current?.identity === identity && current.failed }));
    try {
      const value = await api.get<OperationalData[Key]>(paths[key], { signal });
      if (signal.aborted) return;
      setterRef.current(value);
      setState({ identity, pending: false, loaded: true, failed: false });
    } catch (error) {
      if (!signal.aborted) setState((current) => ({ identity, pending: false, loaded: current?.identity === identity && current.loaded, failed: true }));
      throw error;
    }
  }, [identity, key]);
  const matches = React.useCallback((event: RealtimeMessage) => eventMatches(key, event), [key]);
  const refresh = useCoordinatedRead(read, matches, enabled, 0, signals);
  return { ...(enabled ? state?.identity === identity ? state : { pending: true, loaded: false, failed: false } : { pending: false, loaded: false, failed: false }), refresh };
}
export function useOperationalReads(view: ViewKey, user: UserAccount | null, signals: ReadSignals, setters: Setters) {
  const authenticated = Boolean(user);
  const alerts = useOperationalRead("anomalies", authenticated, signals, setters.anomalies);
  const maintenance = useOperationalRead("maintenanceStatus", authenticated, signals, setters.maintenanceStatus);
  const gate = useOperationalRead("integrationStatus", authenticated && (view === "people" || view === "dashboard"), signals, setters.integrationStatus);
  const failedKeys: OperationalDataKey[] = [];
  if (alerts.failed) failedKeys.push("anomalies");
  if (maintenance.failed) failedKeys.push("maintenanceStatus");
  if (gate.failed) failedKeys.push("integrationStatus");
  const criticalGate = view === "people";
  return {
    refreshAlerts: alerts.refresh,
    refreshOverview: async () => {
      const results = await Promise.allSettled([alerts.refresh(), maintenance.refresh(), gate.refresh()]);
      const failure = results.find((result) => result.status === "rejected");
      if (failure?.status === "rejected") throw failure.reason;
    },
    loading: criticalGate && gate.pending && !gate.loaded,
    readState: criticalGate && gate.failed && !gate.loaded ? "unavailable" as const : failedKeys.length ? "stale" as const : "ready" as const,
    failedKeys
  };
}
