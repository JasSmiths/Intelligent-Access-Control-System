import React from "react";
import { ApiError, isAbortError } from "../../../api/client";
import { integrationsApi, type ICloudCalendarSyncRun } from "../../../api/integrations";
import { useCoordinatedRead } from "../../../lib/useCoordinatedRead";
import { ReadSignalsContext } from "../../../lib/readSignals";
import { integrationEventMatches } from "../invalidation";

type SavedIds = { original_request_id: string; selected_run_id: string };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function restored(key: string): SavedIds {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? "null") as SavedIds | null;
    if (value && Object.keys(value).every((field) => field === "original_request_id" || field === "selected_run_id")
      && [value.original_request_id, value.selected_run_id].every((id) => id === "" || typeof id === "string" && uuid.test(id))) return value;
  } catch { /* Unavailable/invalid storage cannot select a mutation target. */ }
  return { original_request_id: "", selected_run_id: "" };
}

// Two UUIDs only: the original UI request survives inspection of another run.
// Credentials, codes, bearer tokens, provider payloads and checkpoints never persist.
export function useCalendarRunRecovery(accountId: string, role: string) {
  const signals = React.useContext(ReadSignalsContext);
  const storageKey = `iacs-calendar-run:${accountId}:${role}`;
  const owner = React.useMemo(() => ({ storageKey, ids: restored(storageKey), generation: 0, active: true }), [storageKey, signals]);
  const blank = () => ({ owner, ids: owner.ids, run: null as ICloudCalendarSyncRun | null, error: "", missing: false, reading: false });
  const [state, setState] = React.useState(blank);
  const value = state.owner === owner ? state : blank();
  React.useLayoutEffect(() => { owner.active = true; return () => { owner.active = false; }; }, [owner]);
  const knownId = value.ids.selected_run_id;
  const read = React.useCallback(async (signal: AbortSignal) => {
    if (!knownId) return;
    const generation = owner.generation;
    const current = () => owner.active && !signal.aborted && generation === owner.generation && knownId === owner.ids.selected_run_id;
    setState((previous) => ({ ...(previous.owner === owner ? previous : { owner, ids: owner.ids, run: null, missing: false }), error: "", reading: true }));
    try {
      const result = await integrationsApi.getICloudCalendarRun(knownId, { signal });
      if (result.id !== knownId) throw new Error("The response does not match the selected calendar run.");
      if (current()) setState((previous) => ({ ...previous, run: result, error: "", missing: false }));
    } catch (failure) {
      if (current() && !isAbortError(failure)) setState((previous) => ({ ...previous,
        error: failure instanceof Error ? failure.message : "Unable to read this saved calendar run.",
        missing: failure instanceof ApiError && failure.status === 404 }));
      if (current()) throw failure;
    } finally { if (current()) setState((previous) => ({ ...previous, reading: false })); }
  }, [knownId, owner]);
  const refresh = useCoordinatedRead(read, integrationEventMatches, Boolean(knownId));
  const remember = (ids: SavedIds) => {
    sessionStorage.setItem(storageKey, JSON.stringify(ids));
    owner.generation += 1; owner.ids = ids;
    setState({ owner, ids, run: null, error: "", missing: false, reading: false });
  };
  const prepareNew = () => {
    const identity = crypto.randomUUID();
    remember({ original_request_id: identity, selected_run_id: identity });
    return identity;
  };
  const select = (identity: string) => {
    if (!uuid.test(identity)) throw new Error("This calendar run has an invalid request ID.");
    if (identity !== owner.ids.selected_run_id) remember({ ...owner.ids, selected_run_id: identity });
  };
  const accept = (receipt: ICloudCalendarSyncRun) => {
    if (!owner.active || receipt.id !== owner.ids.selected_run_id) return;
    owner.generation += 1;
    setState((previous) => ({ ...(previous.owner === owner ? previous : blank()), run: receipt, error: "", missing: false, reading: false }));
  };
  return { knownId, originalId: value.ids.original_request_id, run: value.run, error: value.error,
    missing: value.missing, reading: value.reading, refresh, prepareNew, select, accept };
}
