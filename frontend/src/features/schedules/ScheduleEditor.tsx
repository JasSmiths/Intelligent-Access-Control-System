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
  const [dependencies, setDependencies] = React.useState<ScheduleDependencies | null>(null);
  const [dependenciesLoading, setDependenciesLoading] = React.useState(false);
  const [dependenciesError, setDependenciesError] = React.useState(false);
  const [error, setError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const submittingRef = React.useRef(false);
  const originalForm = React.useRef(JSON.stringify(form));
  const requestClose = useEditorDismiss(onClose, JSON.stringify(form) !== originalForm.current, submitting, "schedule changes");
  useModalFocus(modalRef, true, requestClose);

  React.useEffect(() => {
    const controller = new AbortController();
    setDependencies(null);
    setDependenciesError(false);
    setDependenciesLoading(Boolean(schedule));
    if (schedule) {
      schedulesApi.dependencies(schedule.id, { signal: controller.signal })
        .then((value) => { if (!controller.signal.aborted) setDependencies(value); })
        .catch(() => { if (!controller.signal.aborted) { setDependencies(null); setDependenciesError(true); } })
        .finally(() => { if (!controller.signal.aborted) setDependenciesLoading(false); });
    }
    return () => controller.abort();
  }, [schedule]);

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
            <ScheduleDependencyPanel dependencies={dependencies} loading={dependenciesLoading} unavailable={dependenciesError} />
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

function ScheduleDependencyPanel({
  dependencies,
  loading,
  unavailable
}: {
  dependencies: ScheduleDependencies | null;
  loading: boolean;
  unavailable: boolean;
}) {
  const items = dependencies ? [
    ...dependencies.people.map((item) => ({ ...item, tone: "blue" as BadgeTone })),
    ...dependencies.vehicles.map((item) => ({ ...item, tone: "green" as BadgeTone })),
    ...dependencies.doors.map((item) => ({ ...item, tone: "amber" as BadgeTone }))
  ] : [];

  return (
    <section className="schedule-dependencies">
      <div className="panel-header">
        <h2>In Use By</h2>
        <Badge tone={items.length ? "blue" : "gray"}>{loading ? "Loading" : `${items.length} assignment${items.length === 1 ? "" : "s"}`}</Badge>
      </div>
      {loading ? (
        <div className="schedule-dependency-empty">Loading dependencies</div>
      ) : unavailable ? (
        <div className="schedule-dependency-empty" role="alert">Assignments unavailable. Try again after the dependency read succeeds.</div>
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
        <div className="schedule-dependency-empty">No assignments</div>
      )}
    </section>
  );
}

function dependencyIcon(kind: string) {
  if (kind === "vehicle") return <Car size={13} />;
  if (kind === "gate" || kind === "garage_door") return <Warehouse size={13} />;
  return <UserRound size={13} />;
}
