import React from "react";
import { integrationsApi, isDeviceCommandReceipt, isGateCommandReceipt, type DeviceCommandReceipt, type GateCommandReceipt, type GateCommandHistoryRecord } from "../../api/integrations";
import type { UserAccount } from "../../api/types";
import { formatDate } from "../../lib/format";

type Receipt = GateCommandReceipt | DeviceCommandReceipt;
type Props = { currentUser: UserAccount; renderReceipt: (receipt: Receipt) => React.ReactNode };

/** Durable discovery is read-only and independent of the browser's advisory intent index. */
export function CommandReceiptHistory(props: Props) {
  return props.currentUser.role === "admin"
    ? <HistorySession key={`${props.currentUser.id}:${props.currentUser.role}`} {...props} /> : null;
}
function HistorySession({ renderReceipt }: Props) {
  const [kind, setKind] = React.useState<"gate" | "cover">("gate");
  return <section className="card" aria-label="Command history">
    <h2>Command history</h2>
    <p>Saved server receipts remain available after browser storage is cleared. Missing records do not prove that a command was not sent.</p>
    <label>Command history type <select value={kind} onChange={(event) => setKind(event.target.value as "gate" | "cover")}>
      <option value="gate">Gate commands</option><option value="cover">Cover commands</option>
    </select></label>
    <ReceiptList key={kind} kind={kind} renderReceipt={renderReceipt} />
  </section>;
}
function ReceiptList({ kind, renderReceipt }: { kind: "gate" | "cover"; renderReceipt: Props["renderReceipt"] }) {
  const [items, setItems] = React.useState<GateCommandHistoryRecord[]>([]);
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(null);
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
      const page = await (kind === "gate" ? integrationsApi.getGateCommands : integrationsApi.getCoverCommands)(beforeId, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (beforeId && (page.next_cursor === beforeId || (page.next_cursor && (seenCursors.current.has(page.next_cursor) || !page.items.length)))) {
        throw new Error("Command history did not advance. Refresh the history to continue.");
      }
      setItems((current) => beforeId ? [...current, ...page.items.filter((row) => !current.some((item) => item.command_id === row.command_id))] : page.items);
      if (page.next_cursor) seenCursors.current.add(page.next_cursor);
      setCursor(page.next_cursor);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Command history is unavailable.");
    } finally { if (!controller.signal.aborted) setLoading(false); }
  }, [kind]);
  React.useEffect(() => { void load(); return () => request.current?.abort(); }, [load]);
  return <div>
    <button type="button" className="secondary-button" onClick={() => void load()}>Refresh command history</button>
    {error ? <p role="alert">{error} {items.length ? "Displayed records may be older." : ""}</p> : null}
    {loading ? <p role="status">Loading command history…</p> : null}
    {!items.length && !loading && !error ? <p>No recorded commands are available. This is not evidence that an uncertain command can be repeated.</p> : null}
    <ul>{items.map((item) => <li key={item.command_id}>
      <button type="button" className="secondary-button" onClick={() => setSelected(item.command_id)}>Inspect command {item.command_id}</button>
      {item.started_at ? <time dateTime={item.started_at}>{formatDate(item.started_at)}</time> : null}
      <span>{item.requires_reconciliation ? "Review required" : "Recorded command"}</span>
    </li>)}</ul>
    {cursor ? <button type="button" className="secondary-button" disabled={loading} onClick={() => void load(cursor)}>Load older commands</button> : null}
    {selected ? <ReceiptInspection key={selected} commandId={selected} kind={kind} renderReceipt={renderReceipt} onClose={() => setSelected(null)} /> : null}
  </div>;
}
function ReceiptInspection({ commandId, kind, renderReceipt, onClose }: { commandId: string; kind: "gate" | "cover"; renderReceipt: Props["renderReceipt"]; onClose: () => void }) {
  const [receipt, setReceipt] = React.useState<Receipt | null>(null);
  const [error, setError] = React.useState("");
  const [revision, setRevision] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    setReceipt(null);
    setError("");
    const read = kind === "gate" ? integrationsApi.getGateCommand(commandId, { signal: controller.signal }) : integrationsApi.getCoverCommand(commandId, { signal: controller.signal });
    read.then((result) => {
      if (controller.signal.aborted) return;
      if ((kind === "gate" && isGateCommandReceipt(result)) || (kind === "cover" && isDeviceCommandReceipt(result))) setReceipt(result);
      else setError("The saved record has no complete command receipt. Its delivery and physical outcome are unavailable.");
    }).catch((failure) => {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "The command result is unavailable.");
    });
    return () => controller.abort();
  }, [commandId, kind, revision]);
  return <section aria-label="Selected command result">
    <h3>Command {commandId}</h3>
    {error ? <p role="alert">{error} Do not repeat an uncertain command.</p> : receipt ? renderReceipt(receipt) : <p role="status">Loading command result…</p>}
    <button type="button" className="secondary-button" onClick={() => setRevision((value) => value + 1)}>Refresh command result</button>
    <button type="button" className="secondary-button" onClick={onClose}>Close command result</button>
  </section>;
}
