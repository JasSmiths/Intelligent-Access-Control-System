import React from "react";
import { api, isAbortError } from "../../api/client";
import type { Schedule } from "../../api/types";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { schedulesEventMatches } from "../directory/invalidation";

// This catalog retains the existing unpaged server contract. Directory collections use cursor pages.
export function useSchedules() {
  const [items, setItems] = React.useState<Schedule[]>([]);
  const [ready, setReady] = React.useState(false);
  const [error, setError] = React.useState("");
  const read = React.useCallback(async (signal: AbortSignal) => {
    setError("");
    try {
      const result = await api.get<Schedule[]>("/api/v1/schedules", { signal });
      if (!signal.aborted) { setItems(result); setReady(true); }
    } catch (failure) {
      if (!signal.aborted && !isAbortError(failure)) setError(failure instanceof Error ? failure.message : "Unable to load schedules");
      throw failure;
    }
  }, []);
  const refresh = useCoordinatedRead(read, schedulesEventMatches);
  return { items, ready, error, refresh };
}
