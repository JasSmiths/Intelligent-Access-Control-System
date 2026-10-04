import React from "react";
import { ChevronRight, DoorOpen, History, RefreshCw, Warehouse, X } from "lucide-react";
import { integrationsApi, isDeviceCommandReceipt, isGateCommandReceipt, type DeviceCommandReceipt, type GateCommandReceipt, type GateCommandHistoryRecord } from "../../api/integrations";
import type { UserAccount } from "../../api/types";
import { formatDate } from "../../lib/format";
import { Badge, EmptyState, ErrorState, LoadingState } from "../../ui/primitives";

type Receipt = GateCommandReceipt | DeviceCommandReceipt;
type Props = { targetId?: string | null; currentUser: UserAccount; renderReceipt: (receipt: Receipt) => React.ReactNode };

/** Durable discovery is read-only and independent of the browser's advisory intent index. */
export function CommandReceiptHistory(props: Props) {
  return props.currentUser.role === "admin"
    ? <HistorySession key={`${props.currentUser.id}:${props.currentUser.role}`} {...props} /> : null;
}
function HistorySession({ renderReceipt, targetId }: Props) {
  const [kind, setKind] = React.useState<"gate" | "cover">("gate");
  return <section className="card command-history-card" aria-label="Command history">
    <div className="command-history-heading">
      <div><h2>Saved command receipts</h2><p>Inspect delivery and verification from the durable command record.</p></div>
      <label>Command history type <select value={kind} onChange={(event) => setKind(event.target.value as "gate" | "cover")}>
        <option value="gate">Gate commands</option><option value="cover">Cover commands</option>
      </select></label>
    </div>
    <p className="command-history-note">Saved server receipts remain available after browser storage is cleared. Missing records do not prove that a command was not sent.</p>
    <ReceiptList key={`${kind}:${targetId}`} targetId={kind === "gate" ? targetId : null} kind={kind} renderReceipt={renderReceipt} />
  </section>;
}
function ReceiptList({ kind, renderReceipt, targetId }: { targetId?: string | null; kind: "gate" | "cover"; renderReceipt: Props["renderReceipt"] }) {
  const [items, setItems] = React.useState<GateCommandHistoryRecord[]>([]);
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(targetId ?? null);
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
  const Icon = kind === "gate" ? DoorOpen : Warehouse;
  return <div className="command-history-content">
    <div className="command-history-tools">
      <span>{items.length > 0 ? `${items.length} receipt${items.length === 1 ? "" : "s"} loaded` : "Command archive"}</span>
      <button type="button" className="secondary-button" aria-label="Refresh command history" onClick={() => void load()}><RefreshCw size={14} aria-hidden="true" /> Refresh</button>
    </div>
    {error ? <ErrorState title="Command history unavailable" description={`${error}${items.length ? " Displayed records may be older." : ""}`} onRetry={() => void load()} retrying={loading} /> : null}
    {loading ? <LoadingState label="Loading command history…" compact={items.length > 0} /> : null}
    {!items.length && !loading && !error ? <EmptyState icon={History} label="No recorded commands are available." description="This is not evidence that an uncertain command can be repeated." /> : null}
    {items.length > 0 ? <ul className="command-history-list">{items.map((item) => <li key={item.command_id}>
      <button type="button" className={`command-history-row${selected === item.command_id ? " selected" : ""}`} aria-label={`Inspect command ${item.command_id}`} aria-expanded={selected === item.command_id} aria-controls={selected === item.command_id ? "command-history-selected-result" : undefined} title={item.command_id} onClick={() => setSelected(item.command_id)}>
        <span className="command-history-icon"><Icon size={18} aria-hidden="true" /></span>
        <span className="command-history-identity"><strong>{kind === "gate" ? "Gate command" : "Cover command"}</strong><code>{shortCommandId(item.command_id)}</code></span>
        {item.started_at ? <time dateTime={item.started_at}>{formatDate(item.started_at)}</time> : <span className="command-history-time-unknown">Time unavailable</span>}
        <Badge tone={item.requires_reconciliation ? "amber" : "gray"}>{item.requires_reconciliation ? "Review required" : "Recorded command"}</Badge>
        <ChevronRight className="command-history-chevron" size={16} aria-hidden="true" />
      </button>
    </li>)}</ul> : null}
    {cursor ? <div className="command-history-pagination"><button type="button" className="secondary-button" disabled={loading} onClick={() => void load(cursor)}>Load older commands</button></div> : null}
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
  return <section className="command-history-inspection" id="command-history-selected-result" aria-label="Selected command result">
    <div className="command-history-inspection-heading"><div><span>Selected receipt</span><h3 title={commandId}>Command <code>{shortCommandId(commandId)}</code></h3></div><button type="button" className="icon-button" aria-label="Close command result" onClick={onClose}><X size={17} aria-hidden="true" /></button></div>
    {error ? <ErrorState title="Command result unavailable" description={`${error} Do not repeat an uncertain command.`} /> : receipt ? renderReceipt(receipt) : <LoadingState label="Loading command result…" compact />}
    <div className="command-history-inspection-actions"><button type="button" className="secondary-button" onClick={() => setRevision((value) => value + 1)}><RefreshCw size={14} aria-hidden="true" /> Refresh command result</button></div>
  </section>;
}

function shortCommandId(commandId: string) {
  return commandId.length > 16 ? `${commandId.slice(0, 8)}…${commandId.slice(-4)}` : commandId;
}
