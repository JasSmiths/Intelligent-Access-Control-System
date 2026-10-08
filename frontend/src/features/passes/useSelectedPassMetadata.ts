import React from "react";
import { ApiError, isAbortError } from "../../api/client";
import { visitorPassesApi } from "../../api/visitorPasses";
import type { RealtimeMessage } from "../../api/types";
import { ReadSignalsContext } from "../../lib/readSignals";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { auditResource } from "../../lib/realtime";
import { isVisitorPassRealtimeEvent, visitorPassFromRealtime } from "./model";
import type { VisitorPass } from "./types";

// At most the open detail and editor identities are hydrated. Updating their
// metadata never resets the editor's local draft or reconstructs a full list.
export function useSelectedPassMetadata(
  detailId: string | undefined,
  editorId: string | undefined,
  setDetail: React.Dispatch<React.SetStateAction<VisitorPass | null>>,
  setEditor: React.Dispatch<React.SetStateAction<VisitorPass | null>>,
) {
  const signals = React.useContext(ReadSignalsContext);
  const revisions = React.useMemo(() => new Map<string, number>(), [signals]);
  const [errors, setErrors] = React.useState<Record<string, string>>({});
  React.useLayoutEffect(() => {
    for (const id of revisions.keys()) if (id !== detailId && id !== editorId) revisions.delete(id);
    return signals?.subscribe((signal) => {
    if (signal.type !== "realtime" || !isVisitorPassRealtimeEvent(signal.message)) return;
    const row = visitorPassFromRealtime(signal.message);
    if (row && (row.id === detailId || row.id === editorId)) {
      revisions.set(row.id, (revisions.get(row.id) ?? 0) + 1);
      setErrors((current) => {
        if (!current[row.id]) return current;
        const next = { ...current }; delete next[row.id]; return next;
      });
    }
    });
  }, [signals, revisions, detailId, editorId]);
  const read = React.useCallback(async (signal: AbortSignal) => {
    const identities = [...new Set([detailId, editorId].filter((id): id is string => Boolean(id)))];
    setErrors({});
    const outcomes = await Promise.allSettled(identities.map(async (id) => {
      const revision = revisions.get(id) ?? 0;
      const current = () => !signal.aborted && revision === (revisions.get(id) ?? 0);
      try {
        const row = await visitorPassesApi.get(id, { signal });
        if (!current()) return;
        setDetail((current) => current?.id === id ? row : current);
        setEditor((current) => current?.id === id ? row : current);
      } catch (failure) {
        if (!current() || isAbortError(failure)) return;
        const message = failure instanceof ApiError && failure.status === 404
          ? "This Visitor Pass no longer exists. Your draft is retained; close it when ready."
          : failure instanceof Error ? failure.message : "Unable to read selected Visitor Pass metadata.";
        setErrors((current) => ({ ...current, [id]: message }));
        throw failure;
      }
    }));
    const failed = outcomes.find((item) => item.status === "rejected");
    if (failed?.status === "rejected") throw failed.reason;
  }, [detailId, editorId, setDetail, setEditor, revisions]);
  const matches = React.useCallback((event: RealtimeMessage) =>
    event.type === "access_event.finalized" || auditResource(event) === "person"
    || isVisitorPassRealtimeEvent(event) && !visitorPassFromRealtime(event), []);
  const refresh = useCoordinatedRead(read, matches, Boolean(detailId || editorId));
  return { refresh, detailError: detailId ? errors[detailId] ?? "" : "",
    editorError: editorId ? errors[editorId] ?? "" : "" };
}
