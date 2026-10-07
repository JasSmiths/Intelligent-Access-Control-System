import * as React from "react";
import { readMotHistory, type MotHistoryPage } from "../../api/vehicleInformation";
import { Badge } from "../../ui/primitives";

export function MotHistory({ vehicleId, revision }: { vehicleId: string; revision: string | null }) {
  const [open, setOpen] = React.useState(false);
  const [page, setPage] = React.useState<MotHistoryPage | null>(null);
  const [cursors, setCursors] = React.useState<(string | null)[]>([null]);
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [reload, setReload] = React.useState(0);
  const cursor = cursors[cursors.length - 1];
  React.useEffect(() => { setCursors([null]); setPage(null); }, [vehicleId, revision]);
  React.useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true); setError("");
    readMotHistory(vehicleId, cursor, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setPage(result); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Unable to load MOT history"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [open, vehicleId, revision, cursor, reload]);
  return <details className="mot-history" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>MOT test history</summary>
    {open && <div aria-live="polite">
      {loading && <p>Loading MOT history…</p>}
      {error && <p role="alert">{error} <button type="button" onClick={() => { setCursors([null]); setReload((value) => value + 1); }}>Retry</button></p>}
      {page && <>
        <p className="field-hint">{page.checked_at ? `DVSA checked ${new Date(page.checked_at).toLocaleString()}` : "MOT history has not been checked."}{page.freshness === "stale" ? " · Saved information is stale." : ""}</p>
        {!page.items.length && <p>{page.outcome === "not_found" ? "DVSA could not find this registration." : page.outcome === "no_history" ? "No MOT tests are recorded for this vehicle." : "Refresh vehicle information to check for MOT history."}</p>}
        <ol className="mot-history-tests">
          {page.items.map((test, index) => <li key={`${test.source}:${test.number}:${test.completed_at}:${index}`}>
            <div className="mot-history-result"><strong>{test.completed_at ? new Date(test.completed_at).toLocaleDateString() : "Test date unavailable"}</strong><Badge tone={test.result === "PASSED" ? "green" : "red"}>{test.result === "PASSED" ? "Passed" : "Failed"}</Badge></div>
            <p>{test.mileage && test.mileage_read !== "NO_ODOMETER" ? `${test.mileage} ${test.mileage_unit ?? "(unit unavailable)"}` : "Mileage unavailable"}{test.expiry ? ` · Expires ${test.expiry}` : ""}</p>
            <details><summary>Test details{test.defects ? ` (${test.defects.length} defects or advisories)` : ""}</summary>
              <p className="field-hint">Test {test.number ?? "number unavailable"}{test.source ? ` · ${test.source}` : ""}</p>
              {test.defects === null ? <p>Defect details unavailable.</p> : test.defects.length ? <ul>{test.defects.map((defect, index) => <li key={index}><strong>{defect.dangerous ? "Dangerous" : defect.type ?? "Defect"}: </strong>{defect.text ?? "Description unavailable"}</li>)}</ul> : <p>No defects or advisories recorded.</p>}
            </details>
          </li>)}
        </ol>
        <div className="mot-history-pagination"><button type="button" disabled={loading || cursors.length === 1} onClick={() => setCursors((values) => values.slice(0, -1))}>Previous</button><span>{page.total} tests</span><button type="button" disabled={loading || !page.next_cursor} onClick={() => setCursors((values) => [...values, page.next_cursor])}>Next</button></div>
      </>}
    </div>}
  </details>;
}
