import { useModalClose } from "../../ui/useModalClose";
import { useModalFocus } from "../../ui/useModalFocus";
import { useEditorDismiss } from "../../ui/useEditorDismiss";
import { Car, Clock3, Save, UserRound, Warehouse, X } from "lucide-react";
import React from "react";
import type { ScheduleDependencies } from "../../api/schedules";
import { schedulesApi } from "../../api/schedules";
import type { Schedule } from "../../api/types";
import type { BadgeTone } from "../../ui/primitives";
import { Badge } from "../../ui/primitives";
import { emptyScheduleBlocks, normalizeScheduleBlocks, scheduleSummary } from "./model";
import { WeeklyScheduleGrid } from "./WeeklyScheduleGrid";

export function ScheduleEditor({
  mode,
  onClose: finishClose,
  onSaved,
  schedule,
  setPageError
}: {
  mode: "create" | "edit";
  onClose: () => void;
  onSaved: () => Promise<void>;
  schedule: Schedule | null;
  setPageError: (message: string) => void;
}) {
  const modalRef = React.useRef<HTMLFormElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const [form, setForm] = React.useState({
    name: schedule?.name ?? "",
    description: schedule?.description ?? "",
    time_blocks: normalizeScheduleBlocks(schedule?.time_blocks ?? emptyScheduleBlocks())
  });
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const submittingRef = React.useRef(false);
  const originalForm = React.useRef(JSON.stringify(form));
  const requestClose = useEditorDismiss(onClose, JSON.stringify(form) !== originalForm.current, submitting, "schedule changes");
  useModalFocus(modalRef, true, requestClose);

  const update = <K extends keyof typeof form>(field: K, value: (typeof form)[K]) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setError("");
    setPageError("");
    setSubmitting(true);
    const payload = {
      name: form.name,
      description: form.description || null,
      time_blocks: normalizeScheduleBlocks(form.time_blocks)
    };
    try {
      await schedulesApi.save(payload, mode === "edit" ? schedule : null);
      await onClose();
      try { await onSaved(); } catch { setPageError("Schedule saved, but the list could not be refreshed. Refresh to see the latest data."); }
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "Unable to save schedule";
      setError(message);
      setPageError(message);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose(); }}>
      <form ref={modalRef} role="dialog" aria-modal="true" aria-label="Schedule" className="modal-card schedule-modal" onSubmit={submit}>
        <div className="modal-header">
          <div>
            <h2>{mode === "edit" ? "Edit Schedule" : "New Schedule"}</h2>
            <p>{scheduleSummary(form.time_blocks)}</p>
          </div>
          <button className="icon-button" onClick={requestClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}

        <div className="schedule-modal-grid">
          <div className="schedule-details-panel">
            <label className="field">
              <span>Schedule name</span>
              <div className="field-control">
                <Clock3 size={17} />
                <input value={form.name} onChange={(event) => update("name", event.target.value)} required />
              </div>
            </label>
            <label className="field">
              <span>Description</span>
              <textarea value={form.description} onChange={(event) => update("description", event.target.value)} />
            </label>
            <ScheduleDependencyPanel key={schedule?.id} scheduleId={schedule?.id} />
          </div>

          <WeeklyScheduleGrid
            value={form.time_blocks}
            onChange={(timeBlocks) => update("time_blocks", timeBlocks)}
          />
        </div>

        <div className="modal-actions">
          <button className="secondary-button" onClick={requestClose} type="button">Cancel</button>
          <button className="primary-button" disabled={submitting} type="submit">
            <Save size={16} />
            {submitting ? "Saving..." : mode === "edit" ? "Save Schedule" : "Create Schedule"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ScheduleDependencyPanel({ scheduleId }: { scheduleId?: string }) {
  const [dependencies, setDependencies] = React.useState<ScheduleDependencies | null>(null);
  const [trail, setTrail] = React.useState<string[]>([]);
  const [loading, setLoading] = React.useState(Boolean(scheduleId));
  const [failedPage, setFailedPage] = React.useState<string[] | null>(null);
  const active = React.useRef<AbortController | null>(null);

  const load = React.useCallback(async (next: string[]) => {
    if (!scheduleId) return;
    active.current?.abort();
    const request = new AbortController();
    active.current = request;
    setLoading(true);
    setFailedPage(null);
    try {
      const page = await schedulesApi.dependencies(scheduleId, { signal: request.signal }, next[next.length - 1]);
      if (!request.signal.aborted) {
        setDependencies(page);
        setTrail(next);
      }
    } catch {
      if (!request.signal.aborted) setFailedPage(next);
    } finally {
      if (!request.signal.aborted) setLoading(false);
    }
  }, [scheduleId]);

  React.useEffect(() => {
    void load([]);
    return () => active.current?.abort();
  }, [load]);
  const items = dependencies ? [
    ...dependencies.people.map((item) => ({ ...item, tone: "blue" as BadgeTone })),
    ...dependencies.vehicles.map((item) => ({ ...item, tone: "green" as BadgeTone })),
    ...dependencies.doors.map((item) => ({ ...item, tone: "amber" as BadgeTone }))
  ] : [];

  return (
    <section className="schedule-dependencies">
      <div className="panel-header">
        <h2>In Use By</h2>
        <Badge tone={items.length ? "blue" : "gray"}>{loading ? "Loading" : `${items.length} shown`}</Badge>
      </div>
      {loading && !dependencies ? (
        <div className="schedule-dependency-empty">Loading dependencies</div>
      ) : items.length ? (
        <div className="schedule-dependency-list">
          {items.map((item) => (
            <span className={`schedule-dependency-pill ${item.tone}`} key={`${item.kind}-${item.id}`}>
              {dependencyIcon(item.kind)}
              <span>{item.name}</span>
            </span>
          ))}
        </div>
      ) : (
        !failedPage && <div className="schedule-dependency-empty">No assignments</div>
      )}
      {failedPage && <div role="alert">Assignments unavailable. <button type="button" onClick={() => void load(failedPage)}>Retry</button></div>}
      {(trail.length > 0 || dependencies?.next_cursor) && <nav aria-label="Schedule assignment pages">
        <button type="button" disabled={loading || !trail.length} onClick={() => void load(trail.slice(0, -1))}>Previous</button>
        <span> Page {trail.length + 1} </span>
        <button type="button" disabled={loading || !dependencies?.next_cursor} onClick={() => {
          if (dependencies?.next_cursor) void load([...trail, dependencies.next_cursor]);
        }}>Next</button>
      </nav>}
    </section>
  );
}

function dependencyIcon(kind: string) {
  if (kind === "vehicle") return <Car size={13} />;
  if (kind === "gate" || kind === "garage_door") return <Warehouse size={13} />;
  return <UserRound size={13} />;
}
