import { RefreshCw } from "lucide-react";
import React from "react";
import { incomingMessagesApi, type IncomingMessage, type IncomingMessageProvider } from "../../api/incomingMessages";
import type { UserAccount } from "../../api/types";
import { formatDate } from "../../lib/format";
import { Badge, type BadgeTone } from "../../ui/primitives";

const providerNames = { whatsapp: "WhatsApp", discord: "Discord" };
const deliveryLabels: Record<string, string> = {
  accepted: "Accepted by provider", rejected: "Rejected by provider", not_sent: "Not sent",
  attempting: "Attempt in progress", unknown: "Delivery unknown — review required",
};
const stateLabels: Record<string, { label: string; tone: BadgeTone }> = {
  received: { label: "Received — awaiting processing", tone: "gray" },
  processing: { label: "Processing", tone: "blue" },
  handled: { label: "Processing finished", tone: "blue" },
};
const originLabels: Record<string, string> = {
  admin: "Admin conversation", standard: "Standard conversation", visitor: "Visitor conversation", denied: "Denied",
};
const reasonLabels: Record<string, string> = {
  processing_interrupted: "Processing was interrupted.",
  incoming_snapshot_unavailable: "The recorded incoming message is unavailable.",
  reply_outcome_unknown: "A reply has an unknown delivery outcome.",
  sender_binding_changed: "The sender's authorization changed.",
  handler_interrupted: "Message handling was interrupted.",
  handler_failed: "Message handling failed.",
  reply_receipt_lost: "The reply receipt was lost.",
  interaction_reply_unavailable: "The original Discord interaction is no longer available; the action was not resumed.",
  channel_unavailable: "The Discord channel is no longer available.",
  discord_admission_changed: "The Discord conversation's authorization changed.",
  discord_membership_unavailable: "Current Discord membership could not be verified.",
};
const resultLabels: Record<keyof IncomingMessage["result_ids"], string> = {
  session_id: "Alfred session", confirmation_id: "Approval", visitor_pass_id: "Visitor pass",
  notification_run_id: "Notification delivery", feedback_id: "Feedback",
};
const uuid = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i;
const confirmation = /^confirm-[\da-f]{32}$/;
const MAX_PAGES = 100;

function messageStatus(message: IncomingMessage): { label: string; tone: BadgeTone } {
  if (message.requires_review || message.lease_expired || message.state === "review_required"
    || message.replies.some((reply) => reply.delivery === "unknown" || !Object.hasOwn(deliveryLabels, reply.delivery))) {
    return { label: "Review required", tone: "amber" };
  }
  return Object.hasOwn(stateLabels, message.state) ? stateLabels[message.state]
    : { label: "Status unavailable — review required", tone: "amber" };
}

export function IncomingMessageRecovery({ currentUser, provider }: { currentUser: UserAccount; provider: IncomingMessageProvider }) {
  return currentUser.role === "admin" && currentUser.is_active !== false
    ? <RecoverySession key={`${currentUser.id}:${currentUser.role}:${provider}`} provider={provider} /> : null;
}

function RecoverySession({ provider }: { provider: IncomingMessageProvider }) {
  const [open, setOpen] = React.useState(false);
  return <section className="discord-section" aria-label={`${providerNames[provider]} incoming message recovery`}>
    <div className="panel-header">
      <h3>Incoming message recovery</h3>
      <button className="secondary-button" type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {open ? "Close incoming history" : "Inspect incoming messages"}
      </button>
    </div>
    <p>Inspect recorded processing and reply outcomes. Uncertain replies need review and are not automatically resent.</p>
    {open ? <MessageHistory provider={provider} /> : null}
  </section>;
}

function MessageHistory({ provider }: { provider: IncomingMessageProvider }) {
  const [items, setItems] = React.useState<IncomingMessage[]>([]);
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(null);
  const [revision, setRevision] = React.useState(0);
  const request = React.useRef<AbortController | null>(null);
  const visited = React.useRef(new Set<string>());
  const load = React.useCallback(async (beforeId?: string) => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError("");
    setSelected(null);
    if (!beforeId) visited.current.clear();
    try {
      const page = await incomingMessagesApi.getPage(provider, beforeId, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (page.items.length > 25 || page.items.some((item) => item.provider !== provider)) {
        throw new Error("Incoming history returned an unexpected page. Refresh to try reading it again.");
      }
      if (page.next_cursor && (page.next_cursor === beforeId || visited.current.has(page.next_cursor))) {
        throw new Error("Incoming history did not advance. Refresh the history to continue.");
      }
      if (beforeId) visited.current.add(beforeId);
      // Retain one bounded page, rather than accumulating incoming history in the browser.
      setItems(page.items);
      setCursor(visited.current.size + 1 < MAX_PAGES ? page.next_cursor : null);
      if (page.next_cursor && visited.current.size + 1 >= MAX_PAGES) {
        setError("The inspection limit has been reached. Refresh to view the latest incoming messages.");
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setCursor(null);
        setError(failure instanceof Error ? failure.message : "Unable to read incoming messages.");
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [provider]);
  React.useEffect(() => {
    void load();
    return () => request.current?.abort();
  }, [load, revision]);
  return <div>
    <button className="secondary-button" type="button" onClick={() => setRevision((value) => value + 1)}>
      <RefreshCw size={14} /> Refresh incoming history
    </button>
    {error ? <p role="alert">{error}{items.length ? " Displayed results may be older." : ""}</p> : null}
    {loading ? <p role="status">Loading incoming messages…</p> : null}
    {!items.length && !loading && !error ? <p>No incoming recovery records are available.</p> : null}
    <ul className="settings-list">
      {items.map((message) => {
        const status = messageStatus(message);
        return <li key={message.id}>
          <button className="secondary-button" type="button" disabled={loading} onClick={() => setSelected(message.id)}
            aria-label={`Inspect incoming message ${message.id}`}>Inspect message</button>
          <Badge tone={status.tone}>{status.label}</Badge>
          {message.received_at ? <time dateTime={message.received_at}>{formatDate(message.received_at)}</time> : null}
        </li>;
      })}
    </ul>
    {cursor ? <button className="secondary-button" type="button" disabled={loading} onClick={() => void load(cursor)}>Load older messages</button> : null}
    {selected ? <MessageInspection key={selected} provider={provider} id={selected} onClose={() => setSelected(null)} /> : null}
  </div>;
}

function MessageInspection({ provider, id, onClose }: { provider: IncomingMessageProvider; id: string; onClose: () => void }) {
  const [message, setMessage] = React.useState<IncomingMessage | null>(null);
  const [error, setError] = React.useState("");
  const [revision, setRevision] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    setMessage(null);
    setError("");
    incomingMessagesApi.getDetail(provider, id, { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      if (result.provider !== provider || result.id !== id) throw new Error("The incoming record did not match the selected message.");
      setMessage(result);
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to read the incoming record.");
    });
    return () => controller.abort();
  }, [provider, id, revision]);
  return <section aria-label="Selected incoming message" style={{ overflowWrap: "anywhere" }}>
    <div className="panel-header"><h4>Incoming message result</h4>
      <button className="secondary-button" type="button" onClick={onClose}>Close message result</button>
    </div>
    {error ? <p role="alert">{error} A failed read does not establish the delivery outcome.</p>
      : !message ? <p role="status">Loading incoming result…</p> : <IncomingMessageDetails message={message} />}
    <button className="secondary-button" type="button" onClick={() => setRevision((value) => value + 1)}>Refresh message result</button>
  </section>;
}

export function IncomingMessageDetails({ message }: { message: IncomingMessage }) {
  const status = messageStatus(message);
  return <div>
    <Badge tone={status.tone}>{status.label}</Badge>
    {message.lease_expired ? <p>Processing stopped before a final outcome was recorded. Review is required.</p> : null}
    {message.review_reason ? <p>{Object.hasOwn(reasonLabels, message.review_reason)
      ? reasonLabels[message.review_reason] : "The recorded outcome requires review."}</p> : null}
    <p>Record <code>{message.id}</code></p>
    <dl>
      <dt>Handling scope</dt><dd>{Object.hasOwn(originLabels, message.origin_kind) ? originLabels[message.origin_kind] : "Unavailable"}</dd>
      {([["Received", message.received_at], ["Available for processing", message.available_at], ["Processing started", message.claimed_at], ["Processing finished", message.handled_at]] as const)
        .map(([label, time]) => time ? <React.Fragment key={label}><dt>{label}</dt><dd><time dateTime={time}>{formatDate(time)}</time></dd></React.Fragment> : null)}
      {(Object.keys(resultLabels) as Array<keyof IncomingMessage["result_ids"]>).map((key) => {
        const id = message.result_ids[key];
        return id && (uuid.test(id) || (key === "confirmation_id" && confirmation.test(id)))
          ? <React.Fragment key={key}><dt>{resultLabels[key]}</dt><dd><code>{id}</code></dd></React.Fragment> : null;
      })}
    </dl>
    <h4>Reply outcomes</h4>
    <p>Provider acceptance does not confirm that a recipient read the reply.</p>
    {!message.replies.length ? <p>No reply attempt is recorded.</p> : null}
    <ol>
      {message.replies.map((reply, position) => <li key={position}>
        <strong>Reply {reply.index === null ? position + 1 : reply.index + 1}</strong>
        <p>{Object.hasOwn(deliveryLabels, reply.delivery) ? deliveryLabels[reply.delivery] : "Delivery unavailable — review required"}</p>
        {reply.operation_id && uuid.test(reply.operation_id) ? <p>Operation <code>{reply.operation_id}</code></p> : <p>Operation identity unavailable.</p>}
        {reply.attempted_at ? <p>Attempted <time dateTime={reply.attempted_at}>{formatDate(reply.attempted_at)}</time></p> : null}
        {reply.completed_at ? <p>Recorded <time dateTime={reply.completed_at}>{formatDate(reply.completed_at)}</time></p> : null}
      </li>)}
    </ol>
  </div>;
}
