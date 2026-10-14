import React from "react";
import { workflowApi, type NotificationRunReceipt } from "../../api/workflows";
import { titleCase } from "../../lib/format";
import { useCoordinatedRead } from "../../lib/useCoordinatedRead";
import { notificationRunEventMatches } from "./invalidation";

export function NotificationRunHistory({ accountKey }: { accountKey: string }) {
  const [open, setOpen] = React.useState(false);
  return <details onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>Recent notification delivery history</summary>
    {open ? <RecentDeliveries key={accountKey} /> : null}
  </details>;
}

function RecentDeliveries() {
  const [runs, setRuns] = React.useState<NotificationRunReceipt[]>([]);
  const [error, setError] = React.useState("");
  const read = React.useCallback(async (signal: AbortSignal) => {
    try {
      const next = await workflowApi.getNotificationRuns({ signal });
      if (!signal.aborted) { setRuns(next); setError(""); }
    } catch (failure) {
      if (!signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to read delivery history.");
      throw failure;
    }
  }, []);
  const refresh = useCoordinatedRead(read, notificationRunEventMatches);
  return <section aria-label="Recent notification deliveries">
    <p>Latest 20 recorded runs. Provider acceptance does not prove audible playback or that a person received the alert.</p>
    <button type="button" className="secondary-button" onClick={() => void refresh().catch(() => undefined)}>Refresh notification history</button>
    {error ? <p role="alert">{error} Delivery is not established by a failed history read.</p> : null}
    <ol>{runs.map((run) => <li key={run.id}>
      <strong>{titleCase(run.status.replaceAll("_", " "))}</strong> · {run.trigger_event}
      {run.review_reason ? <p>{titleCase(run.review_reason.replaceAll("_", " "))}. Inspect uncertain work before any new send.</p> : null}
      {run.actions.map((action) => <div key={action.index}>
        <p>Action {action.index + 1}: {action.accepted_any ? "At least one provider accepted" : "No recorded provider acceptance"}{action.partial_failure ? "; partial failure" : ""}{action.review_required ? "; review required" : ""}.</p>
        <ul>{action.destination_outcomes.map((target, index) => <li key={`${target.target}:${index}`}>{target.target}: {titleCase(target.delivery.replaceAll("_", " "))}</li>)}</ul>
      </div>)}
    </li>)}</ol>
  </section>;
}
