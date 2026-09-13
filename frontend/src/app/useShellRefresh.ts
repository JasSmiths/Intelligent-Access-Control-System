import React from "react";
import { api } from "../api/client";
import type { AccessEvent, Anomaly, ExpectedPresenceSummary, Group, IntegrationStatus, MaintenanceStatus, Person, Presence, RealtimeMessage, Schedule, UserAccount, Vehicle, ViewKey } from "../api/types";
import { shellDataKeysForView, type ShellDataKey } from "./navigation";
import { REALTIME_REFRESH_MIN_INTERVAL_MS } from "./realtimeEvents";
import { refreshSelectionForEvent } from "./realtimeRefresh";
import { createRefreshCoordinator, type RefreshSelection } from "./refreshCoordinator";

type ShellData = {
  presence: Presence[]; expectedPresence: ExpectedPresenceSummary | null;
  events: AccessEvent[]; anomalies: Anomaly[]; people: Person[]; vehicles: Vehicle[];
  groups: Group[]; schedules: Schedule[]; integrationStatus: IntegrationStatus | null;
  maintenanceStatus: MaintenanceStatus | null;
};
type Setters = { [Key in ShellDataKey]: (value: ShellData[Key]) => void };
const paths: Record<ShellDataKey, string> = {
  presence: "/api/v1/presence", expectedPresence: "/api/v1/presence/expected-today",
  events: "/api/v1/events?limit=40", anomalies: "/api/v1/alerts?status=open&limit=100",
  people: "/api/v1/people?include_media=false", vehicles: "/api/v1/vehicles?include_media=false",
  groups: "/api/v1/groups", schedules: "/api/v1/schedules",
  integrationStatus: "/api/v1/integrations/gate/status", maintenanceStatus: "/api/v1/maintenance/status"
};

export function useShellRefresh(view: ViewKey, user: UserAccount | null, setters: Setters) {
  const [loading, setLoading] = React.useState(true);
  const [dataRefreshToken, setDataRefreshToken] = React.useState(0);
  const settersRef = React.useRef(setters);
  settersRef.current = setters;
  const owner = React.useMemo(() => {
    const required = shellDataKeysForView(view, user);
    function createSession() {
      const controller = new AbortController();
      const loaded = new Set<ShellDataKey>();
      const coordinator = createRefreshCoordinator(async ({ keys, route }) => {
        if ([...keys].some((key) => !loaded.has(key))) setLoading(true);
        async function fetchKey<Key extends ShellDataKey>(key: Key) {
          const value = await api.get<ShellData[Key]>(paths[key], { signal: controller.signal });
          if (controller.signal.aborted) return;
          settersRef.current[key](value);
          loaded.add(key);
        }
        // Wait for every read even if one fails; never overlap with the next batch.
        const results = await Promise.allSettled([...keys].map(fetchKey));
        if (controller.signal.aborted) return;
        setLoading(false);
        const failure = results.find((result) => result.status === "rejected");
        if (failure?.status === "rejected") throw failure.reason;
        if (route) setDataRefreshToken((token) => token + 1);
      });
      return { coordinator, controller };
    }
    let session: ReturnType<typeof createSession> | null = null;
    return {
      activate() { session = createSession(); setLoading(true); },
      selection(event: RealtimeMessage) { return refreshSelectionForEvent(event, view, required); },
      refresh: () => session?.coordinator.request({ keys: required, route: true }) ?? Promise.resolve(),
      initial: () => session?.coordinator.request({ keys: required, route: false }) ?? Promise.resolve(),
      realtime: (selection?: RefreshSelection) => session?.coordinator.request(selection ?? { keys: required, route: true }, REALTIME_REFRESH_MIN_INTERVAL_MS) ?? Promise.resolve(),
      dispose() { session?.controller.abort(); session?.coordinator.dispose(); session = null; }
    };
  }, [view, user?.id, user?.role]);
  React.useLayoutEffect(() => {
    owner.activate();
    return () => owner.dispose();
  }, [owner]);
  return { loading, dataRefreshToken, refresh: owner.refresh, initialRefresh: owner.initial, refreshRealtime: owner.realtime, selectionForEvent: owner.selection, resetRefresh: owner.dispose };
}
