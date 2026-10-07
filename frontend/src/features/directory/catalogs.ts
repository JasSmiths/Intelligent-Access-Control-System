import React from "react";
import { api, isAbortError } from "../../api/client";
import type { Group } from "../../api/types";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { groupsEventMatches } from "./invalidation";

// This catalog retains the existing unpaged server contract. Directory collections use cursor pages.
export function useGroups() {
  const [items, setItems] = React.useState<Group[]>([]);
  const [ready, setReady] = React.useState(false);
  const [error, setError] = React.useState("");
  const read = React.useCallback(async (signal: AbortSignal) => {
    setError("");
    try {
      const result = await api.get<Group[]>("/api/v1/groups", { signal });
      if (!signal.aborted) { setItems(result); setReady(true); }
    } catch (failure) {
      if (!signal.aborted && !isAbortError(failure)) setError(failure instanceof Error ? failure.message : "Unable to load groups");
      throw failure;
    }
  }, []);
  const refresh = useCoordinatedRead(read, groupsEventMatches);
  return { items, ready, error, refresh };
}
