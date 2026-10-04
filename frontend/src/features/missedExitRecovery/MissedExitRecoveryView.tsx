import { History, RefreshCw, X } from "lucide-react";
import React from "react";
import { isAbortError } from "../../api/client";
import { missedExitRecoveryApi, type RecoveryAttempt, type RecoveryAttemptFilters, type RecoveryAttemptPage } from "../../api/missedExitRecovery";
import type { Person, UserAccount } from "../../api/types";
import { formatDate } from "../../lib/format";
import { Badge, Toolbar } from "../../ui/primitives";
import { RecoveryConfiguration } from "./RecoveryConfiguration";
import { filterDate, recoveryLabel, recoveryMethods, safeDiagnosticRows } from "./model";

type Props = { targetId?: string | null; currentUser: UserAccount; people: Person[]; refreshToken: number; refresh: () => Promise<void> };
export function MissedExitRecoveryView(props: Props) {
  if (props.currentUser.role !== "admin") return <section className="view-stack"><div className="permission-state" role="alert">Administrator access required for Missed Exit Recovery.</div></section>;
  return <RecoveryAdminView {...props} />;
}
function RecoveryAdminView({ people, refreshToken, refresh, targetId }: Props) {
  const [draft, setDraft] = React.useState({ owner_id: "", registration_number: "", outcome: "", method: "", from_at: "", to_at: "" });
  const [filters, setFilters] = React.useState<RecoveryAttemptFilters>({ offset: 0, limit: 25 });
  const [page, setPage] = React.useState<RecoveryAttemptPage | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [reload, setReload] = React.useState(0);
  const [selectedId, setSelectedId] = React.useState<string | null>(targetId ?? null);
  const [detail, setDetail] = React.useState<RecoveryAttempt | null>(null);
  const [detailError, setDetailError] = React.useState("");
  React.useEffect(() => { setSelectedId(targetId ?? null); }, [targetId]);
  React.useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setPage(null);
    missedExitRecoveryApi.attempts(filters, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setPage(result);
    }).catch((cause) => {
      if (!controller.signal.aborted && !isAbortError(cause)) setError(cause instanceof Error ? cause.message : "Unable to load recovery attempts");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters, refreshToken, reload]);
  React.useEffect(() => {
    setDetail(null); setDetailError("");
    if (!selectedId) return;
    const controller = new AbortController();
    missedExitRecoveryApi.attempt(selectedId, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setDetail(result);
    }).catch((cause) => {
      if (!controller.signal.aborted && !isAbortError(cause)) setDetailError(cause instanceof Error ? cause.message : "Unable to load attempt details");
    });
    return () => controller.abort();
  }, [selectedId, refreshToken, reload]);
  function applyFilters(event: React.FormEvent) {
    event.preventDefault();
    const from = filterDate(draft.from_at), to = filterDate(draft.to_at);
    if (from && to && from > to) { setError("From must be earlier than To."); return; }
    setSelectedId(null); setFilters({ ...draft, from_at: from, to_at: to, offset: 0, limit: filters.limit });
  }
  function movePage(offset: number) { setSelectedId(null); setFilters({ ...filters, offset }); }
  const offset = page?.offset ?? filters.offset ?? 0, limit = page?.limit ?? filters.limit ?? 25;
  return <section className="view-stack settings-page recovery-page">
    <Toolbar title="Missed Exit Recovery" icon={History} />
    <p className="muted">Testing / debug · Recovery requires global enablement and an owner opt-in. Decisions and verified physical commands are recorded separately.</p>
    <RecoveryConfiguration people={people} refreshToken={refreshToken} refresh={refresh} />
    <section className="card"><div className="toolbar"><h2>Recovery attempts</h2><button className="secondary-button" type="button" disabled={loading} onClick={() => setReload((value) => value + 1)}><RefreshCw size={15} /> Refresh attempts</button></div>
      <form onSubmit={applyFilters}><div className="recovery-fields">
        <label className="field"><span>Owner filter</span><select value={draft.owner_id} onChange={(event) => setDraft({ ...draft, owner_id: event.target.value })}><option value="">All owners</option>{people.map((person) => <option key={person.id} value={person.id}>{person.display_name}</option>)}</select></label>
        <label className="field"><span>Plate filter</span><input value={draft.registration_number} onChange={(event) => setDraft({ ...draft, registration_number: event.target.value })} /></label>
        <label className="field"><span>Outcome filter</span><input value={draft.outcome} list="recovery-outcomes" placeholder="Exact outcome" onChange={(event) => setDraft({ ...draft, outcome: event.target.value })} /></label>
        <datalist id="recovery-outcomes">{["denied", "authorized", "awaiting_resident", "coalesced", "departure", "expired", "verified", "command_pending", "rejected"].map((outcome) => <option key={outcome} value={outcome}>{recoveryLabel(outcome)}</option>)}</datalist>
        <label className="field"><span>Method filter</span><select value={draft.method} onChange={(event) => setDraft({ ...draft, method: event.target.value })}><option value="">All methods</option>{recoveryMethods.map((method) => <option key={method} value={method}>{recoveryLabel(method)}</option>)}</select></label>
        <label className="field"><span>From</span><input type="datetime-local" value={draft.from_at} onChange={(event) => setDraft({ ...draft, from_at: event.target.value })} /></label>
        <label className="field"><span>To</span><input type="datetime-local" value={draft.to_at} onChange={(event) => setDraft({ ...draft, to_at: event.target.value })} /></label>
        <label className="field"><span>Attempts per page</span><select value={filters.limit ?? 25} onChange={(event) => { setSelectedId(null); setFilters({ ...filters, limit: Number(event.target.value), offset: 0 }); }}>{[25, 50, 100].map((size) => <option key={size} value={size}>{size}</option>)}</select></label>
        <button className="primary-button" type="submit">Apply filters</button>
      </div></form>
      {error ? <div className="auth-error" role="alert">{error}</div> : null}
      {loading ? <p role="status">Loading recovery attempts…</p> : page ? <>
        <div className="table-scroll"><table className="data-table"><thead><tr><th>Occurred</th><th>Owner / plate</th><th>Method</th><th>Outcome</th><th>Reason</th><th>Details</th></tr></thead><tbody>{page.items.map((attempt) => <tr key={attempt.id}><td>{formatDate(attempt.occurred_at)}</td><td>{attempt.owner_name || "Unresolved owner"}<br /><strong>{attempt.registration_number}</strong></td><td>{recoveryLabel(attempt.method)}</td><td><Badge tone="gray">{recoveryLabel(attempt.outcome)}</Badge></td><td>{attempt.reason}</td><td><button className="secondary-button" type="button" onClick={() => setSelectedId(attempt.id)} aria-label={`View recovery attempt ${attempt.registration_number} ${attempt.id}`}>View</button></td></tr>)}</tbody></table></div>
        {!page.items.length ? <p className="empty-state">No recovery attempts match these filters.</p> : null}
        <div className="toolbar"><span role="status">{page.total ? `${offset + 1}–${Math.min(offset + page.items.length, page.total)} of ${page.total}` : "0 attempts"}</span><div className="recovery-pagination"><button className="secondary-button" type="button" disabled={offset === 0} onClick={() => movePage(Math.max(0, offset - limit))}>Previous</button><button className="secondary-button" type="button" disabled={offset + limit >= page.total} onClick={() => movePage(offset + limit)}>Next</button></div></div>
      </> : null}
    </section>
    {selectedId ? <section className="card" aria-label="Recovery attempt details"><div className="toolbar"><h2>Attempt details</h2><button className="icon-button" type="button" onClick={() => setSelectedId(null)} aria-label="Close attempt details"><X size={18} /></button></div>{detailError ? <div className="auth-error" role="alert">{detailError}</div> : detail ? <RecoveryAttemptDetails attempt={detail} /> : <p role="status">Loading attempt details…</p>}</section> : null}
  </section>;
}
export function RecoveryAttemptDetails({ attempt }: { attempt: RecoveryAttempt }) {
  return <div className="recovery-details">
    <dl className="recovery-summary"><dt>Outcome</dt><dd>{recoveryLabel(attempt.outcome)}</dd><dt>Reason</dt><dd>{attempt.reason}</dd><dt>Method</dt><dd>{recoveryLabel(attempt.method)}</dd><dt>Attempt</dt><dd>{attempt.id}</dd><dt>Original conflicting event</dt><dd>{attempt.event_id ? <a href={`/events?event=${encodeURIComponent(attempt.event_id)}`}>{attempt.event_id}</a> : "None"}</dd><dt>Recovery event</dt><dd>{attempt.recovery_event_id ? <a href={`/events?event=${encodeURIComponent(attempt.recovery_event_id)}`}>{attempt.recovery_event_id}</a> : "None"}</dd><dt>Saga</dt><dd>{attempt.saga_id || "None"}</dd><dt>Physical command evidence</dt><dd>{attempt.command_id ? <a href={`/settings/command-history?command=${encodeURIComponent(attempt.command_id)}`}>{attempt.command_id}</a> : "No command recorded"}</dd><dt>Duration</dt><dd>{attempt.duration_ms === null ? "Not recorded" : `${attempt.duration_ms} ms`}</dd><dt>Policy version</dt><dd>{attempt.policy_version}</dd></dl>
    <h3>Checks</h3><DiagnosticRows value={attempt.checks} />
    <h3>Notification lifecycle</h3><dl className="recovery-summary"><dt>Status</dt><dd>{recoveryLabel(attempt.notification.status || "not_requested")}</dd><dt>Expires</dt><dd>{attempt.notification.expires_at ? formatDate(attempt.notification.expires_at) : "None"}</dd><dt>Action received</dt><dd>{attempt.notification.action_at ? formatDate(attempt.notification.action_at) : "None"}</dd><dt>Delivery</dt><dd>{attempt.notification.delivery_id || "None"}</dd></dl>
    {attempt.notification.canonical_attempt_id ? <p>Coalesced with <a href={`/settings/missed-exit-recovery?attempt=${encodeURIComponent(attempt.notification.canonical_attempt_id)}`}>canonical attempt {attempt.notification.canonical_attempt_id}</a>{attempt.notification.canonical_outcome ? ` · ${recoveryLabel(attempt.notification.canonical_outcome)}` : ""}{attempt.notification.canonical_reason ? ` · ${attempt.notification.canonical_reason}` : ""}</p> : null}
    <h4>Approval and apology delivery</h4><p className="field-hint">Delivered counts describe provider acceptance. They do not prove the resident saw the notification. Action received records the separate iOS action.</p>
    <DiagnosticRows value={Object.fromEntries(Object.entries(attempt.notification).filter(([key]) => /^(approval|apology)_/.test(key) && !key.endsWith("_id")))} />
    {attempt.notification.apology_delivery_id ? <p>Apology delivery: {attempt.notification.apology_delivery_id}</p> : null}
    <h3>Decision and command timeline</h3><p className="field-hint">A recovery decision or provider acceptance alone does not verify physical gate movement. Review the command verification stage.</p>
    {attempt.timeline.length ? <ol className="recovery-timeline">{attempt.timeline.map((entry, index) => <li key={`${entry.at}:${entry.stage}:${index}`}><div><time>{formatDate(entry.at)}</time> · <strong>{recoveryLabel(entry.stage)}</strong> · {recoveryLabel(entry.status)}</div><p>{entry.reason}</p>{entry.details ? <DiagnosticRows value={entry.details} /> : null}</li>)}</ol> : <p>No timeline entries recorded.</p>}
  </div>;
}
function DiagnosticRows({ value }: { value: Record<string, unknown> }) {
  const rows = safeDiagnosticRows(value);
  return rows.length ? <dl className="recovery-summary">{rows.map((row) => <React.Fragment key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></React.Fragment>)}</dl> : <p className="muted">No safe diagnostic values recorded.</p>;
}
