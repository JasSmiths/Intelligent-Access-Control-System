import { CalendarDays, Key, Loader2, Lock, Plus, RefreshCcw, ShieldCheck, Trash2, UserRound } from "lucide-react";
import React from "react";
import { formatDate, titleCase } from "../../../lib/format";
import { Badge } from "../../../ui/primitives";
import type { BadgeTone } from "../../../ui/primitives";
import { ICloudCalendarAccount, ICloudCalendarPayload, ICloudCalendarSyncRun, integrationsApi } from "../../../api/integrations";
import { IntegrationFeedback } from "../catalog";
import { IntegrationFeedbackPanel } from "../components";

export function ICloudCalendarModal({
  payload,
  loading,
  error,
  onChanged
}: {
  payload: ICloudCalendarPayload;
  loading: boolean;
  error: string;
  onChanged: () => Promise<void>;
}) {
  const [adding, setAdding] = React.useState(false);
  const [step, setStep] = React.useState<"credentials" | "verify">("credentials");
  const [appleId, setAppleId] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [code, setCode] = React.useState("");
  const [handshakeId, setHandshakeId] = React.useState("");
  const [handshakeAppleId, setHandshakeAppleId] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [syncing, setSyncing] = React.useState(false);
  const [removingId, setRemovingId] = React.useState<string | null>(null);
  const [reconnectingAccountId, setReconnectingAccountId] = React.useState<string | null>(null);
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const activeAccounts = payload.accounts.filter((account) => account.is_active);
  const latestRun = payload.recent_sync_runs[0] ?? null;
  const hasAttention = activeAccounts.some((account) => ["error", "requires_reauth"].includes(account.status));
  const isReconnectFlow = Boolean(reconnectingAccountId);
  const resetAddFlow = () => {
    setAdding(false);
    setStep("credentials");
    setAppleId("");
    setPassword("");
    setCode("");
    setHandshakeId("");
    setHandshakeAppleId("");
    setReconnectingAccountId(null);
    setFeedback(null);
  };
  const startAddFlow = () => {
    if (adding && !isReconnectFlow) {
      resetAddFlow();
      return;
    }
    setAdding(true);
    setStep("credentials");
    setAppleId("");
    setPassword("");
    setCode("");
    setHandshakeId("");
    setHandshakeAppleId("");
    setReconnectingAccountId(null);
    setFeedback(null);
  };
  const startReconnectFlow = (account: ICloudCalendarAccount) => {
    setAdding(true);
    setStep("credentials");
    setAppleId(account.apple_id);
    setPassword("");
    setCode("");
    setHandshakeId("");
    setHandshakeAppleId("");
    setReconnectingAccountId(account.id);
    setFeedback({
      tone: "info",
      title: "Reconnect iCloud Calendar",
      detail: `Enter the Apple ID details for ${account.display_name} to refresh the trusted session.`
    });
  };
  const startAuth = async (event: React.FormEvent) => {
    event.preventDefault();
    const submittedAppleId = appleId.trim();
    const reconnecting = isReconnectFlow;
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await integrationsApi.startICloudAuth(submittedAppleId, password);
      setPassword("");
      if (result.status === "requires_2fa" && result.handshake_id) {
        setHandshakeId(result.handshake_id);
        setHandshakeAppleId(result.apple_id || submittedAppleId);
        setStep("verify");
        setFeedback({
          tone: "info",
          title: "Verification code required",
          detail: result.detail || "Enter the six-digit Apple verification code to finish connecting this account."
        });
      } else {
        resetAddFlow();
        await onChanged();
        setFeedback({
          tone: "success",
          title: reconnecting ? "iCloud Calendar reconnected" : "iCloud Calendar connected",
          detail: `${result.account?.display_name || submittedAppleId} is ready for calendar sync.`
        });
      }
    } catch (authError) {
      setFeedback({
        tone: "error",
        title: reconnecting ? "Unable to reconnect iCloud Calendar" : "Unable to connect iCloud Calendar",
        detail: authError instanceof Error ? authError.message : "Unable to connect iCloud Calendar."
      });
    } finally {
      setSubmitting(false);
    }
  };
  const verifyCode = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!handshakeId) return;
    const reconnecting = isReconnectFlow;
    setSubmitting(true);
    setFeedback(null);
    try {
      const result = await integrationsApi.verifyICloudAuth(handshakeId, code.trim());
      resetAddFlow();
      await onChanged();
      setFeedback({
        tone: "success",
        title: reconnecting ? "iCloud Calendar reconnected" : "iCloud Calendar connected",
        detail: `${result.account.display_name} is ready for calendar sync.`
      });
    } catch (verifyError) {
      setFeedback({
        tone: "error",
        title: "Verification failed",
        detail: verifyError instanceof Error ? verifyError.message : "Unable to verify that code."
      });
    } finally {
      setSubmitting(false);
    }
  };
  const syncNow = async () => {
    setSyncing(true);
    setFeedback({
      tone: "progress",
      title: "Syncing calendars",
      detail: "Scanning connected accounts for Open Gate events.",
      activeStep: 1
    });
    try {
      const run = await integrationsApi.syncICloudCalendar();
      await onChanged();
      setFeedback({
        tone: run.status === "ok" ? "success" : "info",
        title: run.status === "ok" ? "Calendar sync complete" : "Calendar sync complete with notes",
        detail: icloudSyncRunSummary(run)
      });
    } catch (syncError) {
      setFeedback({
        tone: "error",
        title: "Calendar sync failed",
        detail: syncError instanceof Error ? syncError.message : "Unable to sync iCloud Calendars."
      });
    } finally {
      setSyncing(false);
    }
  };
  const removeAccount = async (account: ICloudCalendarAccount) => {
    if (!window.confirm(`Remove iCloud Calendar account ${account.display_name}? Future unused calendar passes from this account will be cancelled.`)) return;
    setRemovingId(account.id);
    setFeedback(null);
    try {
      await integrationsApi.removeICloudAccount(account.id);
      await onChanged();
      setFeedback({
        tone: "success",
        title: "Account removed",
        detail: `${account.display_name} is no longer connected.`
      });
    } catch (removeError) {
      setFeedback({
        tone: "error",
        title: "Unable to remove account",
        detail: removeError instanceof Error ? removeError.message : "Unable to remove that iCloud Calendar account."
      });
    } finally {
      setRemovingId(null);
    }
  };
  return (
    <div className="icloud-calendar-panel">
      <section className="icloud-overview">
        <div className="icloud-overview-icon">
          <CalendarDays size={20} />
        </div>
        <div className="icloud-overview-copy">
          <strong>Automated Visitor Passes</strong>
          <span>Events with Open Gate in their notes create or update Visitor Passes for the next 14 days.</span>
        </div>
        <Badge tone={error ? "red" : hasAttention ? "amber" : activeAccounts.length ? "green" : "gray"}>
          {error ? "Error" : hasAttention ? "Needs Attention" : activeAccounts.length ? `${activeAccounts.length} Connected` : "Not Configured"}
        </Badge>
      </section>
      <div className="icloud-actions">
        <button className="primary-button" onClick={startAddFlow} disabled={submitting || syncing} type="button">
          <Plus size={15} /> {adding && !isReconnectFlow ? "Close Add Account" : "Add Account"}
        </button>
        <button className="secondary-button" onClick={syncNow} disabled={loading || syncing || !activeAccounts.length} type="button">
          {syncing ? <Loader2 className="spin" size={15} /> : <RefreshCcw size={15} />}
          {syncing ? "Syncing..." : "Sync Calendars Now"}
        </button>
      </div>
      {error ? <div className="auth-error inline-error">{error}</div> : null}
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
                    autoComplete="username"
                    autoFocus
                    inputMode="email"
                    onChange={(event) => setAppleId(event.target.value)}
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
                    autoComplete="current-password"
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="App-specific or account password"
                    type="password"
                    value={password}
                  />
                </div>
              </label>
            </div>
            <div className="icloud-form-actions">
              <button className="secondary-button" onClick={resetAddFlow} disabled={submitting} type="button">Cancel</button>
              <button className="primary-button" disabled={submitting || !appleId.trim() || !password} type="submit">
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
                  autoComplete="one-time-code"
                  autoFocus
                  inputMode="numeric"
                  maxLength={6}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  pattern="[0-9]{6}"
                  placeholder="123456"
                  type="text"
                  value={code}
                />
              </div>
            </label>
            <div className="icloud-form-actions">
              <button className="secondary-button" onClick={resetAddFlow} disabled={submitting} type="button">Cancel</button>
              <button className="primary-button" disabled={submitting || code.length !== 6} type="submit">
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
                      disabled={submitting || syncing || removingId === account.id}
                      onClick={() => startReconnectFlow(account)}
                      type="button"
                    >
                      <RefreshCcw size={14} /> Reconnect
                    </button>
                  ) : null}
                  <button
                    aria-label={`Remove ${account.display_name}`}
                    className="icon-button danger"
                    disabled={removingId === account.id}
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
  );
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
