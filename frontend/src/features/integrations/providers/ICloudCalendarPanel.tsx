import { CalendarDays, Key, Loader2, Lock, Plus, RefreshCcw, ShieldCheck, Trash2, UserRound } from "lucide-react";
import React from "react";
import { formatDate, titleCase } from "../../../lib/format";
import { Badge } from "../../../ui/primitives";
import type { BadgeTone } from "../../../ui/primitives";
import { ICloudCalendarAccount, ICloudCalendarPayload, ICloudCalendarSyncRun, integrationsApi } from "../../../api/integrations";
import { IntegrationFeedback } from "../catalog";
import { IntegrationFeedbackPanel } from "../components";

import { EditorDialog } from "../../../ui/EditorDialog";
import { useEditorLifetime } from "../../../ui/useEditorLifetime";
import { useCoordinatedRead } from "../../../lib/useCoordinatedRead";
import type { ProviderEditorProps } from "../editorTypes";
import { integrationEventMatches } from "../invalidation";
import { useCalendarRunRecovery } from "./useCalendarRunRecovery";
import { isMutationNotStarted } from "../../../api/integrations";

export function ICloudCalendarEditor(props: ProviderEditorProps) {
  const lifetime = useEditorLifetime();
  const canManage = props.currentUser.role === "admin";
  const recovery = useCalendarRunRecovery(props.currentUser.id, props.currentUser.role);
  const [payload, setPayload] = React.useState<ICloudCalendarPayload>({ accounts: [], recent_sync_runs: [] });
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [adding, setAdding] = React.useState(false);
  const [step, setStep] = React.useState<"credentials" | "verify">("credentials");
  const [appleId, setAppleId] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [code, setCode] = React.useState("");
  const [handshakeId, setHandshakeId] = React.useState("");
  const [handshakeAppleId, setHandshakeAppleId] = React.useState("");
  const [operation, setOperation] = React.useState<"auth" | "verify" | "sync" | "remove" | null>(null);
  const [removingId, setRemovingId] = React.useState<string | null>(null);
  const [reconnectingAccountId, setReconnectingAccountId] = React.useState<string | null>(null);
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const pending = React.useRef(false);
  const busy = operation !== null;
  const submitting = operation === "auth" || operation === "verify";
  const syncing = operation === "sync";
  // An outstanding Apple handshake remains a draft after its password is cleared.
  const dirty = Boolean(appleId || password || code || handshakeId || reconnectingAccountId);
  const activeAccounts = payload.accounts.filter((account) => account.is_active);
  const latestRun = payload.recent_sync_runs[0] ?? null;
  const hasAttention = activeAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));
  const isReconnectFlow = Boolean(reconnectingAccountId);
  const read = React.useCallback(async (signal: AbortSignal) => {
    setLoading(true); setError("");
    try { const next = await integrationsApi.getICloudCalendar({ signal }); if (!signal.aborted) setPayload(next); }
    catch (error) { if (!signal.aborted) setError(error instanceof Error ? error.message : "Unable to load iCloud Calendar accounts."); throw error; }
    finally { if (!signal.aborted) setLoading(false); }
  }, []);
  const reload = useCoordinatedRead(read, integrationEventMatches);
  const refreshAccepted = () => { void reload().catch(() => undefined); void props.onChanged().catch(() => undefined); };
  const resetAddFlow = () => {
    setAdding(false); setStep("credentials"); setAppleId(""); setPassword(""); setCode(""); setHandshakeId(""); setHandshakeAppleId(""); setReconnectingAccountId(null); setFeedback(null);
  };
  const discardDraft = () => !pending.current && (!dirty || window.confirm("Discard unsaved iCloud Calendar connection?"));
  const cancelFlow = () => { if (discardDraft()) resetAddFlow(); };
  const startAddFlow = () => {
    if (!canManage || !lifetime.isCurrent() || !discardDraft()) return;
    const close = adding && !isReconnectFlow;
    resetAddFlow(); if (!close) setAdding(true);
  };
  const startReconnectFlow = (account: ICloudCalendarAccount) => {
    if (!canManage || !lifetime.isCurrent() || !discardDraft()) return;
    resetAddFlow(); setAdding(true); setAppleId(account.apple_id); setReconnectingAccountId(account.id);
    setFeedback({ tone: "info", title: "Reconnect iCloud Calendar", detail: `Enter the Apple ID details for ${account.display_name} to refresh the trusted session.` });
  };
  const begin = (next: NonNullable<typeof operation>) => {
    if (!canManage || pending.current || loading || error || !lifetime.isCurrent()) return false;
    pending.current = true; setOperation(next); setFeedback(null); return true;
  };
  const finish = () => { pending.current = false; if (lifetime.isCurrent()) { setOperation(null); setRemovingId(null); } };
  const acceptedAccount = (account: ICloudCalendarAccount | undefined) => {
    if (account) setPayload((current) => ({ ...current, accounts: [...current.accounts.filter((item) => item.id !== account.id), account] }));
  };
  const startAuth = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!appleId.trim() || !password || !begin("auth")) return;
    const submittedAppleId = appleId.trim(); const reconnecting = isReconnectFlow;
    try {
      const result = await integrationsApi.startICloudAuth(submittedAppleId, password);
      if (!lifetime.isCurrent()) return;
      setPassword("");
      if (result.status === "requires_2fa" && result.handshake_id) {
        setHandshakeId(result.handshake_id); setHandshakeAppleId(result.apple_id || submittedAppleId); setStep("verify");
        setFeedback({ tone: "info", title: "Verification code required", detail: result.detail || "Enter the six-digit Apple verification code to finish connecting this account." });
      } else {
        resetAddFlow(); acceptedAccount(result.account);
        setFeedback({ tone: "success", title: reconnecting ? "iCloud Calendar reconnected" : "iCloud Calendar connected", detail: `${result.account?.display_name || submittedAppleId} is ready for calendar sync.` });
        refreshAccepted();
      }
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: reconnecting ? "Unable to reconnect iCloud Calendar" : "Unable to connect iCloud Calendar", detail: error instanceof Error ? error.message : "Unable to connect iCloud Calendar." }); }
    finally { finish(); }
  };
  const verifyCode = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!handshakeId || code.length !== 6 || !begin("verify")) return;
    const reconnecting = isReconnectFlow;
    try {
      const result = await integrationsApi.verifyICloudAuth(handshakeId, code.trim());
      if (!lifetime.isCurrent()) return;
      resetAddFlow(); acceptedAccount(result.account);
      setFeedback({ tone: "success", title: reconnecting ? "iCloud Calendar reconnected" : "iCloud Calendar connected", detail: `${result.account.display_name} is ready for calendar sync.` });
      refreshAccepted();
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "Verification failed", detail: error instanceof Error ? error.message : "Unable to verify that code." }); }
    finally { finish(); }
  };
  const syncNow = async (fresh = false) => {
    if (!begin("sync")) return;
    setFeedback({ tone: "progress", title: "Syncing calendars", detail: "Scanning the fixed account manifest for Open Gate events.", activeStep: 1 });
    try {
      const identity = fresh || !recovery.originalId ? recovery.prepareNew() : recovery.originalId;
      if (!fresh && recovery.originalId) recovery.select(identity);
      const run = await integrationsApi.syncICloudCalendar(identity); if (!lifetime.isCurrent()) return;
      recovery.accept(run);
      setPayload((current) => ({ ...current, recent_sync_runs: [run, ...current.recent_sync_runs.filter((item) => item.id !== run.id)].slice(0, 5) }));
      setFeedback({ tone: run.status === "ok" ? "success" : "info", title: run.status === "ok" ? "Calendar sync complete" : "Calendar sync receipt retained", detail: icloudSyncRunSummary(run) });
      refreshAccepted();
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "Calendar sync response unavailable", detail: `${error instanceof Error ? error.message : "Unable to read the response."} Read the saved run before recovery; no new run will be started automatically.` }); }
    finally { finish(); }
  };
  const resumeRun = async () => {
    const run = recovery.run, context = run?.recovery;
    if (!run || !context || !context.can_resume || recovery.error || pending.current || !lifetime.isCurrent()) return;
    if (!window.confirm(`Resume calendar run ${run.id}? Original origin: ${context.original_origin}. ${context.completed_account_ids.length} completed accounts will be skipped; ${context.remaining_account_ids.length} remaining accounts use the original ${context.coverage.start_date} to ${context.coverage.end_date} range (${context.coverage.vendor_timezone}).${context.original_origin === "workflow" ? " This Admin recovery leaves the original workflow result unchanged." : ""}`) || !begin("sync")) return;
    try {
      const result = await integrationsApi.resumeICloudCalendar(run.id, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      recovery.accept(result);
      setFeedback({ tone: result.status === "ok" ? "success" : "info", title: "Calendar recovery receipt retained", detail: icloudSyncRunSummary(result) });
      refreshAccepted();
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "Calendar recovery response unavailable", detail: `${error instanceof Error ? error.message : "Unable to read recovery response."} The same run ID is retained. Read its checkpoints before retrying.` }); }
    finally { finish(); }
  };
  const removeAccount = async (account: ICloudCalendarAccount) => {
    if (pending.current || !canManage || !lifetime.isCurrent() || !window.confirm(`Remove iCloud Calendar account ${account.display_name}? Future unused calendar passes from this account will be cancelled.`) || !begin("remove")) return;
    setRemovingId(account.id);
    try {
      const removed = await integrationsApi.removeICloudAccount(account.id, lifetime.isCurrent); if (isMutationNotStarted(removed) || !lifetime.isCurrent()) return;
      setPayload((current) => ({ ...current, accounts: current.accounts.filter((item) => item.id !== account.id) }));
      setFeedback({ tone: "success", title: "Account removed", detail: `${account.display_name} is no longer connected.` });
      refreshAccepted();
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "Unable to remove account", detail: error instanceof Error ? error.message : "Unable to remove that iCloud Calendar account." }); }
    finally { finish(); }
  };
  return <EditorDialog className="icloud-calendar-modal" title={props.definition.title} description={props.definition.description} dirty={dirty} busy={busy} onClose={props.onClose} onGuardChange={props.onGuardChange}>
    {({ requestClose }) => <>
    <div className="icloud-calendar-panel">
      <section className="icloud-overview">
        <div className="icloud-overview-icon">
          <CalendarDays size={20} />
        </div>
        <div className="icloud-overview-copy">
          <strong>Automated Visitor Passes</strong>
          <span>Events with Open Gate in their notes create or update Visitor Passes within the captured calendar date range.</span>
        </div>
        <Badge tone={error ? "red" : hasAttention ? "amber" : activeAccounts.length ? "green" : "gray"}>
          {error ? "Error" : hasAttention ? "Needs Attention" : activeAccounts.length ? `${activeAccounts.length} Connected` : "Not Configured"}
        </Badge>
      </section>
      <div className="icloud-actions">
        <button className="primary-button" onClick={startAddFlow} disabled={!canManage || busy || loading || Boolean(error)} type="button">
          <Plus size={15} /> {adding && !isReconnectFlow ? "Close Add Account" : "Add Account"}
        </button>
        <button className="secondary-button" onClick={() => void syncNow()} disabled={!canManage || loading || busy || Boolean(error) || !activeAccounts.length} type="button">
          {syncing ? <Loader2 className="spin" size={15} /> : <RefreshCcw size={15} />}
          {syncing ? "Syncing..." : recovery.originalId ? "Check Original Sync Request" : "Sync Calendars Now"}
        </button>
      </div>
      {recovery.originalId ? <button className="secondary-button" type="button" disabled={!canManage || busy || loading || Boolean(error) || !activeAccounts.length} onClick={() => {
        const complete = recovery.knownId === recovery.originalId && recovery.run?.recovery?.remaining_account_ids.length === 0;
        if (complete || window.confirm("Start a separate calendar sync with a new request ID? The previous request will not be resumed. Read its retained checkpoints before starting separate work.")) void syncNow(true);
      }}>Start New Calendar Sync</button> : null}
      {payload.recent_sync_runs.length ? <section className="icloud-section" aria-label="Recent calendar runs">
        <strong>Read a Recent Run</strong>{payload.recent_sync_runs.map((run) => <button className="secondary-button icloud-run-reference" type="button" key={run.id} disabled={busy} onClick={() => recovery.select(run.id)}>Read run {run.id}</button>)}
      </section> : null}
      {recovery.originalId && recovery.originalId !== recovery.knownId ? <button className="secondary-button" type="button" disabled={busy} onClick={() => recovery.select(recovery.originalId)}>Read Original Request</button> : null}
      {recovery.knownId ? <section className="icloud-section" aria-label="Saved calendar run">
        <strong>Saved Calendar Run</strong><p className="icloud-run-reference">{recovery.knownId}</p>
        {recovery.run ? <p>Original origin: {recovery.run.recovery?.original_origin || recovery.run.trigger_source}. {recovery.run.recovery?.completed_account_ids.length ?? 0} completed; {recovery.run.recovery?.remaining_account_ids.length ?? 0} remaining. {icloudSyncRunSummary(recovery.run)}</p> : null}
        {recovery.run?.recovery ? <p>Original coverage: {recovery.run.recovery.coverage.start_date} to {recovery.run.recovery.coverage.end_date}, inclusive ({recovery.run.recovery.coverage.vendor_timezone}).{recovery.run.recovery.original_origin === "workflow" ? " Admin recovery preserves the original workflow outcome." : ""}</p> : null}
        {recovery.error ? <p role="alert">{recovery.error} The saved ID and accepted receipts are retained.</p> : null}
        <button className="secondary-button" type="button" disabled={busy || recovery.reading} onClick={() => void recovery.refresh().catch(() => undefined)}>Read Saved Sync</button>
        {recovery.run?.recovery?.can_resume ? <button className="secondary-button" type="button" disabled={!canManage || busy || recovery.reading || Boolean(recovery.error)} onClick={() => void resumeRun()}>Resume This Sync</button> : null}
      </section> : null}
      {error ? <div className="auth-error inline-error">{error}<button className="secondary-button" onClick={() => void reload().catch(() => undefined)} type="button">Try again</button></div> : null}
      {feedback ? <IntegrationFeedbackPanel feedback={feedback} /> : null}
      {adding ? (
        step === "credentials" ? (
          <form className="icloud-auth-panel" onSubmit={startAuth}>
            <div className="icloud-auth-heading">
              {isReconnectFlow ? <RefreshCcw size={17} /> : <Key size={17} />}
              <div>
                <strong>{isReconnectFlow ? "Reconnect iCloud account" : "Add iCloud account"}</strong>
                <span>
                  {isReconnectFlow
                    ? "Refresh the trusted iCloud session without removing existing calendar passes."
                    : "Enter the Apple ID details once; only the trusted session is stored."}
                </span>
              </div>
            </div>
            <div className="icloud-auth-grid">
              <label className="field">
                <span>Apple ID</span>
                <div className="field-control">
                  <UserRound size={15} />
                  <input
                    disabled={!canManage || busy}
                    autoComplete="username"
                    autoFocus
                    inputMode="email"
                    onChange={(event) => { if (!pending.current) setAppleId(event.target.value); }}
                    placeholder="name@example.com"
                    type="email"
                    value={appleId}
                  />
                </div>
              </label>
              <label className="field">
                <span>Password</span>
                <div className="field-control">
                  <Lock size={15} />
                  <input
                    disabled={!canManage || busy}
                    autoComplete="current-password"
                    onChange={(event) => { if (!pending.current) setPassword(event.target.value); }}
                    placeholder="App-specific or account password"
                    type="password"
                    value={password}
                  />
                </div>
              </label>
            </div>
            <div className="icloud-form-actions">
              <button className="secondary-button" onClick={cancelFlow} disabled={!canManage || busy} type="button">Cancel</button>
              <button className="primary-button" disabled={!canManage || busy || !appleId.trim() || !password} type="submit">
                {submitting
                  ? isReconnectFlow
                    ? "Reconnecting..."
                    : "Connecting..."
                  : isReconnectFlow
                    ? "Reconnect"
                    : "Connect"}
              </button>
            </div>
          </form>
        ) : (
          <form className="icloud-auth-panel" onSubmit={verifyCode}>
            <div className="icloud-auth-heading">
              <ShieldCheck size={17} />
              <div>
                <strong>Enter verification code</strong>
                <span>{handshakeAppleId || "Apple"} is waiting for the six-digit code.</span>
              </div>
            </div>
            <label className="field icloud-code-field">
              <span>Verification code</span>
              <div className="field-control">
                <ShieldCheck size={15} />
                <input
                    disabled={!canManage || busy}
                  autoComplete="one-time-code"
                  autoFocus
                  inputMode="numeric"
                  maxLength={6}
                  onChange={(event) => { if (!pending.current) setCode(event.target.value.replace(/\D/g, "").slice(0, 6)); }}
                  pattern="[0-9]{6}"
                  placeholder="123456"
                  type="text"
                  value={code}
                />
              </div>
            </label>
            <div className="icloud-form-actions">
              <button className="secondary-button" onClick={cancelFlow} disabled={!canManage || busy} type="button">Cancel</button>
              <button className="primary-button" disabled={!canManage || busy || code.length !== 6} type="submit">
                {submitting ? "Verifying..." : "Verify and Connect"}
              </button>
            </div>
          </form>
        )
      ) : null}
      <section className="icloud-section">
        <div className="icloud-section-heading">
          <strong>Connected Accounts</strong>
          <span>{loading ? "Refreshing accounts" : `${activeAccounts.length} active`}</span>
        </div>
        <div className="icloud-account-list">
          {activeAccounts.length ? (
            activeAccounts.map((account) => (
              <article className="icloud-account-card" key={account.id}>
                <div className="icloud-account-main">
                  <span className="icloud-account-icon"><CalendarDays size={16} /></span>
                  <div>
                    <strong>{account.display_name}</strong>
                    <span>{account.apple_id}</span>
                  </div>
                </div>
                <div className="icloud-account-status">
                  <Badge tone={icloudAccountStatusTone(account.status)}>{icloudAccountStatusLabel(account.status)}</Badge>
                  <span>{account.last_sync_at ? `Last sync ${formatDate(account.last_sync_at)}` : "Not synced yet"}</span>
                  {account.last_error ? <small>{account.last_error}</small> : null}
                </div>
                <div className="icloud-account-actions">
                  {icloudAccountNeedsReconnect(account) ? (
                    <button
                      className="secondary-button compact"
                      disabled={!canManage || busy || loading || Boolean(error)}
                      onClick={() => startReconnectFlow(account)}
                      type="button"
                    >
                      <RefreshCcw size={14} /> Reconnect
                    </button>
                  ) : null}
                  <button
                    aria-label={`Remove ${account.display_name}`}
                    className="icon-button danger"
                    disabled={!canManage || busy || loading || Boolean(error)}
                    onClick={() => removeAccount(account)}
                    type="button"
                  >
                    {removingId === account.id ? <Loader2 className="spin" size={15} /> : <Trash2 size={15} />}
                  </button>
                </div>
              </article>
            ))
          ) : (
            <div className="icloud-empty">No iCloud Calendar accounts connected</div>
          )}
        </div>
      </section>
      <section className="icloud-section">
        <div className="icloud-section-heading">
          <strong>Recent Sync</strong>
          <span>{latestRun ? formatOptionalDate(latestRun.started_at || latestRun.finished_at) : "No syncs yet"}</span>
        </div>
        {latestRun ? (
          <div className="icloud-sync-summary">
            <div>
              <Badge tone={latestRun.status === "ok" ? "green" : latestRun.status === "error" ? "red" : "amber"}>{titleCase(latestRun.status)}</Badge>
              <span>{icloudSyncRunSummary(latestRun)}</span>
            </div>
            {latestRun.error ? <small>{latestRun.error}</small> : null}
          </div>
        ) : (
          <div className="icloud-empty">Run a manual sync after connecting an account</div>
        )}
      </section>
    </div>
    <div className="modal-actions"><button className="primary-button" onClick={requestClose} type="button">Done</button></div>
    </>}
  </EditorDialog>;
}

function icloudAccountStatusLabel(status: string) {
  if (status === "requires_reauth") return "Reconnect";
  if (status === "connected") return "Connected";
  if (status === "error") return "Error";
  if (status === "removed") return "Removed";
  return titleCase(status || "unknown");
}

function icloudAccountNeedsReconnect(account: ICloudCalendarAccount): boolean {
  return ["error", "requires_reauth"].includes(account.status);
}

function icloudAccountStatusTone(status: string): BadgeTone {
  if (status === "connected") return "green";
  if (status === "requires_reauth") return "amber";
  if (status === "error") return "red";
  return "gray";
}

function icloudSyncRunSummary(run: ICloudCalendarSyncRun) {
  const changes = [
    `${run.events_matched} matched`,
    `${run.passes_created} created`,
    `${run.passes_updated} updated`,
    `${run.passes_cancelled} cancelled`,
    `${run.passes_skipped} skipped`
  ];
  return `${run.account_count} account${run.account_count === 1 ? "" : "s"} scanned, ${run.events_scanned} event${run.events_scanned === 1 ? "" : "s"} read, ${changes.join(", ")}.`;
}

export function formatOptionalDate(value: string | null | undefined) {
  return value ? formatDate(value) : "Pending";
}
