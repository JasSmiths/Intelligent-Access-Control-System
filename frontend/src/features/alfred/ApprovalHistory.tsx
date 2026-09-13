import React from "react";
import { listChatApprovals, type ChatApprovalReference } from "../../api/chat";
import type { UserAccount } from "../../api/types";
import { formatDate } from "../../lib/format";
import type { RetainedApproval } from "./useApprovalRecovery";

type Props = { currentUser: UserAccount; busy: boolean; onInspect: (approval: RetainedApproval) => void };
const statusLabels: Record<ChatApprovalReference["status"], string> = {
  pending: "Awaiting confirmation", in_progress: "In progress", completed: "Completed — inspect result",
  unknown: "Outcome unknown — review required", cancelled: "Cancelled", expired: "Expired",
};

/** Lists requester-bound identifiers; only an explicit selection opens the existing GET inspector. */
export function ApprovalHistory(props: Props) {
  return props.currentUser.role === "admin"
    ? <ApprovalHistorySession key={`${props.currentUser.id}:${props.currentUser.role}`} {...props} /> : null;
}
function ApprovalHistorySession({ busy, onInspect }: Props) {
  const [items, setItems] = React.useState<ChatApprovalReference[]>([]);
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const request = React.useRef<AbortController | null>(null);
  const seenCursors = React.useRef(new Set<string>());
  const load = React.useCallback(async (beforeId?: string) => {
    request.current?.abort();
    if (!beforeId) seenCursors.current.clear();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError("");
    try {
      const page = await listChatApprovals(beforeId, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (beforeId && (page.next_cursor === beforeId || (page.next_cursor && (seenCursors.current.has(page.next_cursor) || !page.items.length)))) {
        throw new Error("Approval history did not advance. Refresh the history to continue.");
      }
      setItems((current) => beforeId ? [...current, ...page.items.filter((row) => !current.some((item) => item.confirmation_id === row.confirmation_id))] : page.items);
      if (page.next_cursor) seenCursors.current.add(page.next_cursor);
      setCursor(page.next_cursor);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Approval history is unavailable.");
    } finally { if (!controller.signal.aborted) setLoading(false); }
  }, []);
  React.useEffect(() => { void load(); return () => request.current?.abort(); }, [load]);
  return <section aria-label="Approval history" className="chat-llm-feedback">
    <h3>Your saved actions</h3>
    <p>Server history for your current account. Inspecting an action does not confirm or repeat it.</p>
    <button type="button" className="chat-confirm-button secondary" onClick={() => void load()}>Refresh approval history</button>
    {error ? <p role="alert">{error} {items.length ? "Displayed records may be older." : ""}</p> : null}
    {loading ? <p role="status">Loading approval history…</p> : null}
    {!items.length && !loading && !error ? <p>No saved actions are available for this account. Absence does not establish whether an action was sent.</p> : null}
    <ul>{items.map((item) => <li key={item.confirmation_id}>
      <p>{statusLabels[item.status] ?? "Status unavailable — review required"} · <time dateTime={item.created_at}>{formatDate(item.created_at)}</time></p>
      <p>Operation <code>{item.operation_id}</code></p>
      <button type="button" className="chat-confirm-button secondary" disabled={busy || !item.session_id}
        onClick={() => { if (item.session_id) onInspect({ sessionId: item.session_id, confirmationId: item.confirmation_id }); }}>
        Inspect action {item.confirmation_id}
      </button>
      {!item.session_id ? <p>The original conversation is unavailable. This record cannot be inspected here.</p> : null}
    </li>)}</ul>
    {cursor ? <button type="button" className="chat-confirm-button secondary" disabled={loading} onClick={() => void load(cursor)}>Load older actions</button> : null}
  </section>;
}
