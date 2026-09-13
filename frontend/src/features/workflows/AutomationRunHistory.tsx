import React from "react";
import { gateCommandReceiptUrl } from "../../api/integrations";
import type { UserAccount } from "../../api/types";
import { notificationRunReceiptUrl, workflowApi, type AutomationRule, type AutomationRun } from "../../api/workflows";
import { formatDate, titleCase } from "../../lib/format";
import { Badge, type BadgeTone } from "../../ui/primitives";

const actionLabels: Record<string, string> = {
  pending: "Pending", attempting: "Attempt in progress", succeeded: "Completed",
  failed: "Failed", skipped: "Skipped", unknown: "Outcome unknown — review required",
};
const uuid = /^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i;
const text = (value: unknown): string | null => typeof value === "string" && value.trim() ? value : null;
const identity = (value: unknown): string | null => typeof value === "string" && uuid.test(value) ? value : null;

function runStatus(run: AutomationRun): { label: string; tone: BadgeTone } {
  const unclearAction = run.action_states.some((action) => !text(action.state)
    || !Object.hasOwn(actionLabels, String(action.state)) || action.state === "unknown");
  if (run.requires_review || run.status === "review_required" || unclearAction) return { label: "Review required", tone: "amber" };
  if (run.status === "success" && run.action_results.some((result) => result?.status === "queued")) return { label: "Delivery queued", tone: "blue" };
  const known: Record<string, { label: string; tone: BadgeTone }> = {
    queued: { label: "Queued", tone: "gray" }, processing: { label: "Processing", tone: "blue" },
    success: { label: "Completed", tone: "green" }, failed: { label: "Failed", tone: "red" },
    skipped: { label: "Skipped", tone: "gray" },
  };
  return known[run.status] ?? { label: "Status unavailable — review required", tone: "amber" };
}

export function AutomationRunHistory(props: { currentUser: UserAccount; refreshToken: number; rules: AutomationRule[] }) {
  return props.currentUser.role === "admin"
    ? <RunHistorySession key={`${props.currentUser.id}:${props.currentUser.role}`} {...props} /> : null;
}

function RunHistorySession({ refreshToken, rules }: { refreshToken: number; rules: AutomationRule[] }) {
  const [runs, setRuns] = React.useState<AutomationRun[]>([]);
  const [cursor, setCursor] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [revision, setRevision] = React.useState(0);
  const [selected, setSelected] = React.useState<string | null>(null);
  const request = React.useRef<AbortController | null>(null);
  const load = React.useCallback(async (beforeId?: string) => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError("");
    try {
      const page = await workflowApi.getAutomationRuns(beforeId, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (beforeId && page.next_cursor === beforeId) throw new Error("Run history did not advance. Refresh the history to continue.");
      setRuns((current) => beforeId ? [...current, ...page.items.filter((run) => !current.some((item) => item.id === run.id))] : page.items);
      setCursor(page.next_cursor);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to read automation runs.");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);
  React.useEffect(() => {
    void load();
    return () => request.current?.abort();
  }, [load, refreshToken, revision]);

  return <section className="card" aria-label="Automation run history">
    <div className="panel-header"><h2>Run history</h2><button className="secondary-button" type="button" onClick={() => setRevision((value) => value + 1)}>Refresh run history</button></div>
    <p>Recorded attempts and outcomes. Opening a result does not run its actions again.</p>
    {error ? <p role="alert">{error}{runs.length ? " Displayed results may be older." : ""}</p> : null}
    {loading ? <p role="status">Loading run history…</p> : null}
    {!runs.length && !loading && !error ? <p>No automation runs are available.</p> : null}
    <ul className="settings-list">
      {runs.map((run) => {
        const status = runStatus(run);
        const label = rules.find((rule) => rule.id === run.rule_id)?.name || "Automation run";
        return <li key={run.id}>
          <button className="secondary-button" type="button" onClick={() => setSelected(run.id)} aria-label={`Inspect ${label} ${run.id}`}>{label}</button>
          <Badge tone={status.tone}>{status.label}</Badge>
          {run.started_at ? <time dateTime={run.started_at}>{formatDate(run.started_at)}</time> : null}
        </li>;
      })}
    </ul>
    {cursor ? <button className="secondary-button" type="button" disabled={loading} onClick={() => void load(cursor)}>Load older runs</button> : null}
    {selected ? <RunInspection key={selected} runId={selected} refreshToken={`${refreshToken}:${revision}`} onClose={() => setSelected(null)} /> : null}
  </section>;
}

function RunInspection({ runId, refreshToken, onClose }: { runId: string; refreshToken: string; onClose: () => void }) {
  const [run, setRun] = React.useState<AutomationRun | null>(null);
  const [error, setError] = React.useState("");
  const [revision, setRevision] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    setRun(null);
    setError("");
    workflowApi.getAutomationRun(runId, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setRun(result); })
      .catch((failure) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to read the run result."); });
    return () => controller.abort();
  }, [runId, refreshToken, revision]);
  return <section aria-label="Selected automation run">
    <div className="panel-header"><h3>Run result</h3><button className="secondary-button" type="button" onClick={onClose}>Close run result</button></div>
    {error ? <p role="alert">{error} This does not establish whether any action was sent. Do not repeat an uncertain action.</p> : !run ? <p role="status">Loading run result…</p> : <AutomationRunDetails run={run} />}
    <button className="secondary-button" type="button" onClick={() => setRevision((value) => value + 1)}>Refresh run result</button>
  </section>;
}

export function AutomationRunDetails({ run }: { run: AutomationRun }) {
  const status = runStatus(run);
  return <div>
    <Badge tone={status.tone}>{status.label}</Badge>
    {run.review_reason ? <p>{titleCase(run.review_reason.replaceAll("_", " "))}</p> : null}
    <p>{run.actor} · {run.source} · {run.trigger_key}</p>
    {run.error && run.error !== run.review_reason ? <p>{run.error}</p> : null}
    {!run.action_states.length ? <p>Checkpoint details are unavailable for this run. No action outcome is inferred.</p> : null}
    <ol>
      {run.action_states.map((action, position) => {
        const id = text(action.id);
        const result = id ? run.action_results.find((item) => item?.id === id) : undefined;
        const state = text(action.state);
        const operationId = identity(action.operation_id);
        const notificationId = identity(result?.notification_run_id);
        const queued = result?.status === "queued";
        const label = queued ? "Queued for notification delivery — delivery is not confirmed"
          : state && Object.hasOwn(actionLabels, state) ? actionLabels[state] : "State unavailable — review required";
        return <li key={typeof action.index === "number" ? `${action.index}:${position}` : position}>
          <strong>{id || `Action ${position + 1} (details unavailable)`}</strong>
          <p>{label}</p>
          {text(result?.reason) ? <p>{titleCase(String(result?.reason).replaceAll("_", " "))}</p> : null}
          {operationId ? <p>Operation <code>{operationId}</code></p> : <p>Operation identity unavailable.</p>}
          {operationId && result?.type === "gate.open" ? <p><a href={gateCommandReceiptUrl(operationId)} target="_blank" rel="noreferrer">Inspect gate receipt</a></p> : null}
          {notificationId ? <p><a href={notificationRunReceiptUrl(notificationId)} target="_blank" rel="noreferrer">Inspect notification delivery</a></p> : null}
        </li>;
      })}
    </ol>
  </div>;
}
