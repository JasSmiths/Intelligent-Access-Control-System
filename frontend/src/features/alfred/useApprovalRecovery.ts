import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { inspectChatApproval, type ChatApprovalInspection } from "../../api/chat";

export type RetainedApproval = { sessionId: string; confirmationId: string; decision?: "confirm" | "cancel" };
export function approvalStorageKey(requesterKey: string) { return `iacs-alfred-approvals:${requesterKey}`; }
function readRetainedApprovals(key: string): RetainedApproval[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) || "[]");
    if (!Array.isArray(value)) return [];
    return value.flatMap((item: unknown) => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      if (typeof record.sessionId !== "string" || !record.sessionId || record.sessionId.length > 128 ||
          typeof record.confirmationId !== "string" || !record.confirmationId || record.confirmationId.length > 256 ||
          (record.decision !== undefined && record.decision !== "confirm" && record.decision !== "cancel")) return [];
      return [{ sessionId: record.sessionId, confirmationId: record.confirmationId, decision: record.decision }];
    });
  } catch { return []; }
}
function retain(key: string, approvals: RetainedApproval[]) {
  try {
    // Only opaque identifiers/decision: no tool arguments, preview text or credentials.
    if (approvals.length) sessionStorage.setItem(key, JSON.stringify(approvals));
    else sessionStorage.removeItem(key);
  } catch { /* In-memory recovery still works when browser storage is unavailable. */ }
}

/** Owns inspection lifetime and requester-scoped receipt metadata; it cannot confirm an action. */
export function useApprovalRecovery(requesterKey: string, enabled: boolean, connectionNonce: number) {
  const key = approvalStorageKey(requesterKey);
  const [saved, setSaved] = useState(() => ({ key, approvals: readRetainedApprovals(key) }));
  const savedRef = useRef(saved);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const approvals = saved.key === key ? saved.approvals : [];
  const approval = approvals.find((item) => item.confirmationId === selectedId) ?? approvals.at(-1) ?? null;
  const approvalRef = useRef(approval);
  useLayoutEffect(() => { approvalRef.current = approval; }, [approval]);
  const [inspectionState, setInspection] = useState<{ approval: RetainedApproval; result: ChatApprovalInspection } | null>(null);
  const inspection = inspectionState?.approval === approval ? inspectionState.result : null;
  const [errorState, setError] = useState<{ approval: RetainedApproval; message: string } | null>(null);
  const error = errorState?.approval === approval ? errorState.message : null;
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (savedRef.current.key === key) return;
    const next = { key, approvals: readRetainedApprovals(key) };
    savedRef.current = next;
    setSaved(next);
    setSelectedId(null);
  }, [key]);
  const save = useCallback((next: RetainedApproval[]) => {
    savedRef.current = { key, approvals: next };
    retain(key, next);
    setSaved(savedRef.current);
  }, [key]);
  const remember = useCallback((next: RetainedApproval) => {
    const current = savedRef.current.key === key ? savedRef.current.approvals : [];
    const existing = current.find((item) => item.confirmationId === next.confirmationId);
    if (existing?.sessionId === next.sessionId && existing?.decision === next.decision) {
      setSelectedId(next.confirmationId);
      return;
    }
    save([...current.filter((item) => item.confirmationId !== next.confirmationId), next]);
    setSelectedId(next.confirmationId);
  }, [key, save]);
  const forget = useCallback((confirmationId: string) => {
    if (savedRef.current.key !== key) return;
    save(savedRef.current.approvals.filter((item) => item.confirmationId !== confirmationId));
  }, [key, save]);
  const select = useCallback((confirmationId: string) => {
    setSelectedId(confirmationId);
  }, []);
  const recheck = useCallback(() => setRevision((current) => current + 1), []);
  useEffect(() => {
    if (!enabled || !approval) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    inspectChatApproval(approval.sessionId, approval.confirmationId, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setInspection({ approval, result }); })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError({ approval, message: reason instanceof Error ? reason.message : "The saved action result is unavailable." });
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [approval, enabled, connectionNonce, revision]);
  return { approvals, approval, approvalRef, inspection, error, loading, remember, forget, select, recheck };
}
