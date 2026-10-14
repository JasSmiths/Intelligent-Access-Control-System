import { Bell, Plus, Trash2 } from "lucide-react";
import React from "react";
import { Badge } from "../../../ui/primitives";
import { EditorDialog } from "../../../ui/EditorDialog";
import { useEditorLifetime } from "../../../ui/useEditorLifetime";
import { useCoordinatedRead } from "../../../lib/useCoordinatedRead";
import { type AppriseUrlSummary, addAppriseUrl, removeAppriseUrl, integrationsApi, isMutationNotStarted, sendAppriseTestNotification, testIntegrationSettings } from "../../../api/integrations";
import type { ProviderEditorProps } from "../editorTypes";
import type { IntegrationFeedback } from "../catalog";
import { IntegrationFeedbackPanel } from "../components";
import { integrationEventMatches } from "../invalidation";

export function AppriseEditor(props: ProviderEditorProps) {
  const lifetime = useEditorLifetime();
  const canManage = props.currentUser.role === "admin";
  const [urls, setUrls] = React.useState<AppriseUrlSummary[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [readError, setReadError] = React.useState("");
  const [feedback, setFeedback] = React.useState<IntegrationFeedback | null>(null);
  const [adding, setAdding] = React.useState(false);
  const [newUrl, setNewUrl] = React.useState("");
  const [operation, setOperation] = React.useState<"add" | "remove" | "send" | "test" | null>(null);
  const pending = React.useRef(false);
  const busy = operation !== null;
  const submitting = operation === "add";
  const discardDraft = () => !pending.current && (!newUrl || window.confirm("Discard unsaved Apprise URL?"));
  const read = React.useCallback(async (signal: AbortSignal) => {
    setLoading(true); setReadError("");
    try { const next = await integrationsApi.getAppriseUrls({ signal }); if (!signal.aborted) setUrls(next); }
    catch (error) { if (!signal.aborted) setReadError(error instanceof Error ? error.message : "Unable to load Apprise URLs."); throw error; }
    finally { if (!signal.aborted) setLoading(false); }
  }, []);
  const reload = useCoordinatedRead(read, integrationEventMatches);
  const refreshAccepted = () => { void reload().catch(() => undefined); void props.onChanged().catch(() => undefined); };
  const addUrl = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!newUrl.trim() || !canManage || pending.current || loading || readError || !lifetime.isCurrent()) return;
    pending.current = true; setOperation("add"); setFeedback(null);
    try {
      const result = await addAppriseUrl(newUrl.trim(), lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      setUrls(result); setNewUrl(""); setAdding(false);
      setFeedback({ tone: "success", title: "Apprise URL added", detail: "The notification URL was saved." });
      refreshAccepted();
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "Apprise URL update failed", detail: error instanceof Error ? error.message : "Unable to add Apprise URL." }); }
    finally { pending.current = false; if (lifetime.isCurrent()) setOperation(null); }
  };
  const removeUrl = async (url: AppriseUrlSummary) => {
    if (!canManage || pending.current || loading || readError || !lifetime.isCurrent()) return;
    pending.current = true; setOperation("remove"); setFeedback(null);
    try {
      const result = await removeAppriseUrl(url, lifetime.isCurrent);
      if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
      setUrls(result.urls);
      setFeedback({ tone: "success", title: "Apprise URL removed", detail: "The notification URL was removed." });
      refreshAccepted();
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: "Apprise URL update failed", detail: error instanceof Error ? error.message : "Unable to remove Apprise URL." }); }
    finally { pending.current = false; if (lifetime.isCurrent()) setOperation(null); }
  };
  const test = async (connection: boolean) => {
    if (!canManage || pending.current || loading || readError || !lifetime.isCurrent()) return;
    pending.current = true; setOperation(connection ? "test" : "send");
    setFeedback({ tone: "progress", title: connection ? "Testing connection" : "Sending test notification", detail: "Contacting Apprise.", activeStep: 1 });
    try {
      if (connection) {
        const result = await testIntegrationSettings({ integration: props.definition.key, values: {} }, props.definition.title, lifetime.isCurrent);
        if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
        if (!result.ok) throw new Error(result.message);
        setFeedback({ tone: "success", title: "Connection verified", detail: result.message });
      } else {
        const result = await sendAppriseTestNotification(lifetime.isCurrent);
        if (isMutationNotStarted(result) || !lifetime.isCurrent()) return;
        setFeedback({ tone: "success", title: "Test notification sent", detail: "Apprise accepted the notification request." });
      }
    } catch (error) { if (lifetime.isCurrent()) setFeedback({ tone: "error", title: connection ? "Connection failed" : "Notification failed", detail: error instanceof Error ? error.message : "Apprise test failed." }); }
    finally { pending.current = false; if (lifetime.isCurrent()) setOperation(null); }
  };
  return <EditorDialog title={props.definition.title} description={props.definition.description} dirty={Boolean(newUrl)} busy={busy} onClose={props.onClose} onGuardChange={props.onGuardChange}>
    {({ requestClose }) => <>
    {readError ? <div className="auth-error inline-error">{readError}<button className="secondary-button" onClick={() => void reload().catch(() => undefined)} type="button">Try again</button></div> : null}
    <div className="apprise-manager">
      <div className="apprise-manager-header">
        <div>
          <strong>Notification URLs</strong>
          <span>Add one destination per service. Secrets stay encrypted; only safe previews are shown here.</span>
        </div>
        <button className="primary-button" disabled={!canManage || busy || loading || Boolean(readError)} onClick={() => { if (!adding || discardDraft()) { if (adding) setNewUrl(""); setAdding((current) => !current); } }} type="button">
          <Plus size={15} /> Add New Apprise URL
        </button>
      </div>
      {adding ? (
        <form className="apprise-add-row" onSubmit={addUrl}>
          <label className="field">
            <span>Apprise URL</span>
            <div className="field-control">
              <Bell size={16} />
              <input
                autoFocus
                disabled={busy}
                value={newUrl}
                onChange={(event) => { if (!pending.current) setNewUrl(event.target.value); }}
                placeholder="pover://USER_KEY@APP_TOKEN"
              />
            </div>
            <small className="field-hint">For Pushover use `pover://USER_KEY@APP_TOKEN`. `pushover://USER_KEY/APP_TOKEN` is accepted too.</small>
          </label>
          <div className="apprise-add-actions">
            <button className="secondary-button" disabled={busy} onClick={() => { if (discardDraft()) { setNewUrl(""); setAdding(false); } }} type="button">Cancel</button>
            <button className="primary-button" disabled={!canManage || busy || loading || Boolean(readError) || !newUrl.trim()} type="submit">
              {submitting ? "Adding..." : "Add URL"}
            </button>
          </div>
        </form>
      ) : null}
      <div className="apprise-url-table">
        <div className="apprise-url-head">
          <span>Type</span>
          <span>API & Key Preview</span>
          <span />
        </div>
        {loading ? (
          <div className="apprise-empty">Loading saved URLs</div>
        ) : urls.length ? (
          urls.map((url) => (
            <div className="apprise-url-row" key={`${url.scheme}-${url.index}`}>
              <div>
                <Badge tone={url.type === "Pushover" ? "blue" : "gray"}>{url.type}</Badge>
              </div>
              <div>
                <strong>{url.preview}</strong>
                <span>{url.scheme}</span>
              </div>
              <button className="icon-button danger" onClick={() => void removeUrl(url)} disabled={!canManage || busy || loading || Boolean(readError)} type="button" aria-label={`Remove ${url.type} URL`}>
                <Trash2 size={15} />
              </button>
            </div>
          ))
        ) : (
          <div className="apprise-empty">No notification URLs configured</div>
        )}
      </div>
    </div>
    {feedback ? <IntegrationFeedbackPanel feedback={feedback} /> : null}
    <div className="modal-actions">
      <button className="secondary-button" disabled={!canManage || busy || loading || Boolean(readError)} onClick={() => void test(false)} type="button">{operation === "send" ? "Sending..." : "Send Test"}</button>
      <button className="secondary-button" disabled={!canManage || busy || loading || Boolean(readError)} onClick={() => void test(true)} type="button">{operation === "test" ? "Testing..." : "Test Connection"}</button>
      <button className="primary-button" onClick={requestClose} type="button">Done</button>
    </div>
    </>}
  </EditorDialog>;
}
